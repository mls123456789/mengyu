"""情绪日记路由：列表（GET）+ 流式回应接口（POST SSE）+ 删除（DELETE）。

回应为 NDJSON 结构化输出（section + advice），落库存原始 NDJSON 全文，列表页 parse 后注入前端。
含输入长度校验与每用户提交限流（仿解梦的 _dream_allowed）。
"""
from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from app.auth import CurrentUser, require_user
from app.config import settings
from app.db import get_conn
from app.services.dream import parse_dream  # NDJSON 解析逻辑与解梦共用
from app.services.journal import respond_to_journal_stream
from app.services.sse import sse_stream
from app.tpl import templates

router = APIRouter(prefix="/journal")

MOOD_OPTIONS = ["平静", "开心", "焦虑", "悲伤", "愤怒", "疲惫", "迷茫", "感恩", "孤独", "兴奋"]

# 输入长度上限（content 与解梦一致；防 token 成本失控）
MAX_CONTENT = 2000
MAX_MOOD = 20

# 每用户「日记提交」限流：N 秒窗口内最多 M 次（仿 dream._dream_allowed）
_QUOTA_LIMIT = 10
_QUOTA_WINDOW = 600


class JournalIn(BaseModel):
    mood: str = ""
    content: str = ""


def _journal_allowed(user_id: int) -> bool:
    """返回是否允许本次回应，并记录本次时间（仿 _dream_allowed）。"""
    now = time.time()
    cutoff = now - _QUOTA_WINDOW
    with get_conn() as conn:
        conn.execute("DELETE FROM journal_quota WHERE ts < ?", (cutoff,))
        recent = conn.execute(
            "SELECT COUNT(*) AS c FROM journal_quota WHERE user_id = ? AND ts >= ?",
            (user_id, cutoff),
        ).fetchone()["c"]
        if recent >= _QUOTA_LIMIT:
            return False
        conn.execute(
            "INSERT INTO journal_quota (user_id, ts) VALUES (?, ?)",
            (user_id, now),
        )
    return True


def _list_journals(user_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM journals WHERE user_id = ? ORDER BY id DESC LIMIT 50",
            (user_id,),
        ).fetchall()


def _journals_for_view(user_id: int) -> list[dict]:
    """读取日记列表并 parse ai_response，组装成前端渲染所需的结构。"""
    out: list[dict] = []
    for r in _list_journals(user_id):
        parsed = parse_dream(r["ai_response"])  # journal 无 mood 行 → tags 为 []
        out.append({
            "id": r["id"],
            "mood": r["mood"],
            "content": r["content"],
            "sections": parsed["sections"],
            "advice": parsed["advice"],
            "created_at": r["created_at"],
        })
    return out


@router.get("")
def journal_page(request: Request, user: CurrentUser = Depends(require_user)):
    return templates.TemplateResponse(
        request,
        "journal.html",
        {
            "request": request,
            "user": user,
            "journals_json": _journals_for_view(user.id),
            "mood_options": MOOD_OPTIONS,
            "llm_ready": settings.llm_ready,
        },
    )


SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@router.post("/api/respond")
async def journal_respond_api(
    request: Request,  # noqa: ARG001 - 保持签名一致
    body: JournalIn,
    user: CurrentUser = Depends(require_user),
):
    """流式情绪回应：逐段推送 NDJSON 文本，完成后落库。"""
    mood = body.mood.strip()
    content = body.content.strip()
    if not content:
        return JSONResponse({"detail": "先写点什么吧，哪怕只有一个字～"}, status_code=400)
    if len(mood) > MAX_MOOD:
        return JSONResponse({"detail": "情绪标签过长"}, status_code=400)
    if len(content) > MAX_CONTENT:
        return JSONResponse({"detail": f"日记内容太长啦，请精简到 {MAX_CONTENT} 字以内"}, status_code=400)
    if not settings.llm_ready:
        return JSONResponse({"detail": "AI 暂未配置（缺少 LLM_API_KEY 等）"}, status_code=503)
    # quota 检查含 SQLite I/O，放线程池执行，避免阻塞事件循环（与 on_done 一致）
    if not await asyncio.to_thread(_journal_allowed, user.id):
        return JSONResponse({"detail": "今天写得有点多啦，稍候几分钟再试试"}, status_code=429)

    def on_done(full: str) -> dict:
        # ai_response 存原始 NDJSON 全文（mood 是用户选的，单独存 journals.mood）
        with get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO journals (user_id, mood, content, ai_response) "
                "VALUES (?, ?, ?, ?)",
                (user.id, mood, content, full),
            )
            row = conn.execute(
                "SELECT created_at FROM journals WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
        return {"id": cur.lastrowid, "created_at": row["created_at"]}

    return StreamingResponse(
        sse_stream(respond_to_journal_stream(mood=mood, content=content), on_done),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.delete("/api/{journal_id}")
def journal_delete_api(
    journal_id: int,
    user: CurrentUser = Depends(require_user),
):
    """删除一条日记。仅能删自己的；不存在或不属于自己均返回 404。"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT user_id FROM journals WHERE id = ?", (journal_id,)
        ).fetchone()
        if not row or row["user_id"] != user.id:
            return JSONResponse({"detail": "记录不存在"}, status_code=404)
        conn.execute("DELETE FROM journals WHERE id = ?", (journal_id,))
    return JSONResponse({"ok": True})
