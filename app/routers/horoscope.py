"""星座运势路由。

核心流程（确定性 + 全局缓存）：
- 同一「星座 + 时段」的运势**全局只生成一次**，对所有用户、所有请求一致；
- 默认请求命中缓存即回放，不再调用 LLM；
- 仅当 force=true（「换一签」）才重新生成文案；
- 分数/幸运/速配由确定性引擎给定，落库时强制覆盖，保证数值恒定。
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
from app.services import astro
from app.services.horoscope import (
    PERIODS,
    SIGN_META,
    SIGN_NAMES,
    SIGNS,
    horoscope_stream,
    parse_fortune,
    period_human,
    period_key,
)
from app.services.sse import sse_replay, sse_stream
from app.tpl import templates

router = APIRouter(prefix="/horoscope")

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

# ----- 「换签重生成」每用户限流（SQLite 共享表，多 worker 一致）-----
# 同一 (user_id, sign, period) 在 _FORCE_WINDOW 秒内最多强制刷新 _FORCE_LIMIT 次。
_FORCE_LIMIT = 3       # 最多强制刷新次数
_FORCE_WINDOW = 600    # 时间窗（秒）= 10 分钟


def _force_allowed(user_id: int, sign: str, period: str) -> bool:
    """返回是否允许本次 force=true 刷新，并记录本次时间。

    使用 wall-clock time.time()（跨 worker 共享、可比）。
    SQLite 写串行化保证计数一致；并发下 best-effort。
    """
    now = time.time()
    cutoff = now - _FORCE_WINDOW
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM force_history WHERE ts < ?",
            (cutoff,),
        )
        recent = conn.execute(
            "SELECT COUNT(*) AS c FROM force_history "
            "WHERE user_id = ? AND sign = ? AND period = ? AND ts >= ?",
            (user_id, sign, period, cutoff),
        ).fetchone()["c"]
        if recent >= _FORCE_LIMIT:
            return False
        conn.execute(
            "INSERT INTO force_history (user_id, sign, period, ts) VALUES (?, ?, ?, ?)",
            (user_id, sign, period, now),
        )
    return True


class FortuneIn(BaseModel):
    sign: str = ""
    period: str = "today"
    force: bool = False


def _cached_content(sign: str, period: str, pkey: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT content FROM horoscopes WHERE sign = ? AND period = ? AND period_key = ?",
            (sign, period, pkey),
        ).fetchone()
    return row["content"] if row else None


def _persist_sign(user_id: int, sign: str) -> None:
    """把用户选择的星座持久化到 users.sign（下次登录自动恢复）。"""
    with get_conn() as conn:
        conn.execute("UPDATE users SET sign = ? WHERE id = ?", (sign, user_id))


@router.get("")
def horoscope_page(request: Request, period: str = "today", user: CurrentUser = Depends(require_user)):
    if period not in PERIODS:
        period = "today"
    pkey = period_key(period)
    # 已选星座仅存于会话（重新登录后重置为未选）
    my_sign = request.session.get("sign", "") or ""
    if my_sign not in SIGN_NAMES:
        my_sign = ""
    sections = parse_fortune(_cached_content(my_sign, period, pkey)) if my_sign else {}
    from app.services.horoscope import PERIOD_LABELS

    return templates.TemplateResponse(
        request,
        "horoscope.html",
        {
            "request": request,
            "user": user,
            "signs": SIGNS,
            "sign_meta": SIGN_META,
            "my_sign": my_sign,
            "period": period,
            "periods": [(p, PERIOD_LABELS[p]) for p in PERIODS],
            "period_human": period_human(period),
            "sections": sections,
            "llm_ready": settings.llm_ready,
        },
    )


@router.post("/api/fortune")
async def horoscope_fortune_api(
    request: Request,  # noqa: ARG001
    body: FortuneIn,
    user: CurrentUser = Depends(require_user),
):
    sign = body.sign.strip()
    period = body.period.strip() or "today"
    if sign not in SIGN_NAMES:
        return JSONResponse({"detail": "请选择一个有效的星座"}, status_code=400)
    if period not in PERIODS:
        return JSONResponse({"detail": "时段参数无效"}, status_code=400)
    if not settings.llm_ready:
        return JSONResponse({"detail": "运势服务暂时不可用，请稍后再试"}, status_code=503)

    pkey = period_key(period)
    # 记住用户选择的星座：存 session，并持久化到 users.sign（下次登录自动恢复）。
    # 同步 SQLite I/O 一律经 asyncio.to_thread，避免阻塞事件循环。
    request.session["sign"] = sign
    await asyncio.to_thread(_persist_sign, user.id, sign)

    # 命中缓存且非强制刷新：直接回放，结果与所有人一致
    cached = await asyncio.to_thread(_cached_content, sign, period, pkey)
    if cached and not body.force:
        return StreamingResponse(
            sse_replay(cached, {"sign": sign, "period": period, "period_key": pkey}),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )

    # force=true 的「换签重生成」按用户限流，避免频繁调用 LLM
    if body.force and not await asyncio.to_thread(_force_allowed, user.id, sign, period):
        return JSONResponse(
            {"detail": "换签太频繁啦，请稍候几分钟后再试"},
            status_code=429,
        )

    # 确定性数值只算一次：stream（造 prompt）与 on_done（覆盖落库）共用，
    # 避免 cache-miss 时天文 + 黄历数据算两遍。ephem 天文计算是 CPU 密集同步调用，
    # 放线程池执行。
    comp = await asyncio.to_thread(astro.compute, sign, period, pkey)

    def on_done(full: str) -> dict:
        # 用确定性数值覆盖 LLM 产出，保证分数/幸运/速配恒定；文案保留 LLM 的
        sections = parse_fortune(full)
        final = astro.apply_and_serialize(sections, comp)
        with get_conn() as conn:
            conn.execute(
                "DELETE FROM horoscopes WHERE sign = ? AND period = ? AND period_key = ?",
                (sign, period, pkey),
            )
            conn.execute(
                "INSERT INTO horoscopes (sign, period, period_key, content) VALUES (?, ?, ?, ?)",
                (sign, period, pkey, final),
            )
        return {"sign": sign, "period": period, "period_key": pkey}

    return StreamingResponse(
        sse_stream(horoscope_stream(sign=sign, period=period, comp=comp), on_done),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
