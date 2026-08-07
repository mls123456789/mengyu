"""SSE（Server-Sent Events）流式包装工具。

把「文本增量异步生成器 + 完成回调」转成标准 SSE 数据帧序列：
    data: {"type":"delta","text":"..."}\n\n
    ...
    data: {"type":"done","text":"完整文本", ...meta}\n\n
出错时：
    data: {"type":"error","message":"..."}\n\n
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator, Callable, Optional

logger = logging.getLogger(__name__)


def _frame(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


async def sse_stream(
    text_gen: AsyncIterator[str],
    on_done: Optional[Callable[[str], dict]] = None,
) -> AsyncIterator[str]:
    """消费文本增量生成器，产出 SSE 帧；结束时调用 on_done(完整文本) 持久化。

    on_done 多为同步阻塞操作（SQLite 落库；星座运势还含 ephem 天文计算），
    放进线程池执行，避免阻塞事件循环、拖慢其他并发的 SSE 连接。
    """
    parts: list[str] = []
    try:
        async for delta in text_gen:
            parts.append(delta)
            yield _frame({"type": "delta", "text": delta})
        full = "".join(parts)
        meta = await asyncio.to_thread(on_done, full) if on_done else {}
        yield _frame({"type": "done", "text": full, **meta})
    except Exception as exc:  # noqa: BLE001 - 任何异常都转为友好的 error 帧
        logger.warning("SSE 生成失败: %s", exc, exc_info=True)
        yield _frame({"type": "error", "message": "生成失败，请稍后再试"})


async def sse_replay(text: str, meta: Optional[dict] = None) -> AsyncIterator[str]:
    """把已缓存的内容作为一帧 delta 回放（命中缓存时用，无需再调 LLM）。"""
    if text:
        yield _frame({"type": "delta", "text": text})
    yield _frame({"type": "done", "text": text, **(meta or {})})
