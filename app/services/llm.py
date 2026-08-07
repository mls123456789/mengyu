"""LLM 服务：OpenAI 兼容协议客户端封装（异步流式）。

支持 DeepSeek / 智谱 GLM / 通义 / Moonshot / new-api 等任意 OpenAI 兼容后端。
使用 AsyncOpenAI 以便在 FastAPI 中做真正的流式输出（SSE）。
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import AsyncIterator

import httpx
from openai import AsyncOpenAI

from app.config import settings
from app.services import alert

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """LLM 调用失败。"""


@lru_cache(maxsize=1)
def get_async_client() -> AsyncOpenAI:
    if not settings.llm_ready:
        raise LLMError(
            "LLM 未配置：请在 .env 中设置 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL"
        )
    return AsyncOpenAI(
        base_url=settings.LLM_BASE_URL,
        api_key=settings.LLM_API_KEY,
        # connect 快速失败；对流式而言 read=60 是「两次 chunk 之间最长等待 60s」，
        # 既能挂掉卡死的上游，又不会误杀偏慢但正常的逐 token 输出。
        timeout=httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=5.0),
    )


async def stream_chat(
    system: str, user: str, *, temperature: float | None = None
) -> AsyncIterator[str]:
    """异步流式调用 chat completion，逐段 yield 文本增量。

    连接阶段失败（超时/拒绝）重试 1 次；一旦已向调用方输出过文本就不再重试，
    以免向用户重复推送同一段内容。
    """
    client = get_async_client()
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    temp = settings.LLM_TEMPERATURE if temperature is None else temperature

    produced = False
    last_exc = None
    for _attempt in range(2):  # 最多尝试 2 次
        try:
            # async with 管理上游流：正常结束/异常/调用方被取消（客户端断开 SSE）
            # 都会立即关闭 HTTP 流——不再继续烧 token 到 read timeout，
            # 也不依赖 GC 释放连接，避免高并发断连时耗尽连接池。
            async with await client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=messages,
                temperature=temp,
                max_tokens=settings.LLM_MAX_TOKENS,
                stream=True,
            ) as stream:
                async for chunk in stream:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta.content
                    if delta:
                        produced = True
                        yield delta
            alert.on_llm_ok()  # 一次完整成功 → 重置连续失败计数
            return  # 正常结束
        except LLMError:
            raise
        except Exception as exc:  # noqa: BLE001 - 统一转为业务异常
            last_exc = exc
            if produced:
                # 已经输出过内容，重试会造成重复——直接报错
                logger.warning("LLM 流式中断（已输出，不重试）: %s", exc)
                alert.on_llm_error(exc)
                raise LLMError("AI 回应中断了，请重试一次") from exc
            # 尚未输出任何内容：进入下一次重试
    logger.warning("LLM 调用失败（已重试仍失败）: %s", last_exc)
    alert.on_llm_error(last_exc)
    raise LLMError("AI 服务暂时不可用，请稍后再试") from last_exc
