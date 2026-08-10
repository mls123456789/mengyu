"""解梦路由：列表（GET）+ 流式解读接口（POST SSE）+ 删除（DELETE）。

解读为 NDJSON 结构化输出（mood/tags + section + advice），落库存原始 NDJSON 全文，
列表页把每条解读 parse 成结构后注入前端，由 JS 统一渲染。
含输入长度校验与每用户提交限流（仿星座运势的 _force_allowed）。
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
from app.services.dream import interpret_dream_stream, parse_dream
from app.services.sse import sse_stream
from app.tpl import templates

router = APIRouter(prefix="/dream")

# 输入长度上限（与前端 maxlength 一致，防 token 成本失控 / prompt 注入面）
MAX_TITLE = 60
MAX_CONTENT = 2000

# 每用户「解梦提交」限流：N 秒窗口内最多 M 次（仿 horoscope._force_allowed）
_QUOTA_LIMIT = 10      # 最多提交次数
_QUOTA_WINDOW = 600   # 时间窗（秒）= 10 分钟


class DreamIn(BaseModel):
    title: str = ""
    content: str = ""


def _dream_allowed(user_id: int) -> bool:
    """返回是否允许本次解梦，并记录本次时间。

    使用 wall-clock time.time()（跨 worker 共享、可比）；SQLite 写串行化保证计数一致。
    """
    now = time.time()
    cutoff = now - _QUOTA_WINDOW
    with get_conn() as conn:
        conn.execute("DELETE FROM dream_quota WHERE ts < ?", (cutoff,))
        recent = conn.execute(
            "SELECT COUNT(*) AS c FROM dream_quota WHERE user_id = ? AND ts >= ?",
            (user_id, cutoff),
        ).fetchone()["c"]
        if recent >= _QUOTA_LIMIT:
            return False
        conn.execute(
            "INSERT INTO dream_quota (user_id, ts) VALUES (?, ?)",
            (user_id, now),
        )
    return True


def _list_dreams(user_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM dreams WHERE user_id = ? ORDER BY id DESC LIMIT 50",
            (user_id,),
        ).fetchall()


def _dreams_for_view(user_id: int) -> list[dict]:
    """读取梦境列表并 parse interpretation，组装成前端渲染所需的结构。"""
    out: list[dict] = []
    for r in _list_dreams(user_id):
        parsed = parse_dream(r["interpretation"])
        tags = parsed["tags"]
        # 兼容：NDJSON 未解析出 tags 但库里有（如未来格式变动）时回退到 tags 列
        if not tags and r["tags"]:
            tags = [t for t in r["tags"].split(",") if t]
        out.append({
            "id": r["id"],
            "title": r["title"],
            "content": r["content"],
            "tags": tags,
            "sections": parsed["sections"],
            "advice": parsed["advice"],
            "created_at": r["created_at"],
        })
    return out


@router.get("")
def dream_page(request: Request, user: CurrentUser = Depends(require_user)):
    return templates.TemplateResponse(
        request,
        "dream.html",
        {
            "request": request,
            "user": user,
            "dreams_json": _dreams_for_view(user.id),
            "llm_ready": settings.llm_ready,
        },
    )


SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@router.post("/api/interpret")
async def dream_interpret_api(
    request: Request,  # noqa: ARG001 - 保持签名一致
    body: DreamIn,
    user: CurrentUser = Depends(require_user),
):
    """流式解梦：逐段推送 NDJSON 文本，完成后解析并落库。"""
    title = body.title.strip()
    content = body.content.strip()
    if not content:
        return JSONResponse({"detail": "请先写下你的梦境～"}, status_code=400)
    if len(title) > MAX_TITLE:
        return JSONResponse({"detail": f"标题请控制在 {MAX_TITLE} 字以内"}, status_code=400)
    if len(content) > MAX_CONTENT:
        return JSONResponse({"detail": f"梦境内容太长啦，请精简到 {MAX_CONTENT} 字以内"}, status_code=400)
    if not settings.llm_ready:
        return JSONResponse({"detail": "AI 暂未配置（缺少 LLM_API_KEY 等）"}, status_code=503)
    # quota 检查含 SQLite I/O，放线程池执行，避免阻塞事件循环（与 on_done 一致）
    if not await asyncio.to_thread(_dream_allowed, user.id):
        return JSONResponse({"detail": "今天解读得有点多啦，稍候几分钟再试试"}, status_code=429)

    def on_done(full: str) -> dict:
        # 解析 NDJSON 取 tags 落库；interpretation 存原始全文（前端/服务端均可重新 parse）
        tags_str = ",".join(parse_dream(full)["tags"])
        with get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO dreams (user_id, title, content, interpretation, tags, mood) "
                "VALUES (?, ?, ?, ?, ?, '')",
                (user.id, title, content, full, tags_str),
            )
            row = conn.execute(
                "SELECT created_at FROM dreams WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
        return {"id": cur.lastrowid, "created_at": row["created_at"]}

    return StreamingResponse(
        sse_stream(interpret_dream_stream(title=title, content=content), on_done),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.delete("/api/{dream_id}")
def dream_delete_api(
    dream_id: int,
    user: CurrentUser = Depends(require_user),
):
    """删除一条梦境记录。仅能删自己的；不存在或不属于自己均返回 404（不泄漏存在性）。"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT user_id FROM dreams WHERE id = ?", (dream_id,)
        ).fetchone()
        if not row or row["user_id"] != user.id:
            return JSONResponse({"detail": "记录不存在"}, status_code=404)
        conn.execute("DELETE FROM dreams WHERE id = ?", (dream_id,))
    return JSONResponse({"ok": True})
