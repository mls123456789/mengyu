"""LLM 服务测试：stream_chat 重试逻辑、异常处理、参数透传。

Mock 策略：构造 MockClient 替代 AsyncOpenAI，支持 `async with await client.chat.completions.create(...)`
（async context manager + async iterator）。monkeypatch get_async_client 返回 mock，
同时清除 lru_cache 避免跨测试污染。

运行：PYTHONIOENCODING=utf-8 python -m pytest tests/test_llm.py -v
"""
from __future__ import annotations

from typing import Optional

import pytest

from app.config import settings
from app.services.llm import LLMError, get_async_client


# ---------- Mock 对象 ----------

class _Delta:
    def __init__(self, content: str | None):
        self.content = content


class _Choice:
    def __init__(self, content: str | None):
        self.delta = _Delta(content)


class _Chunk:
    def __init__(self, content: str | None):
        # content=None → choices 为空（模拟上游心跳 / 空 chunk）
        self.choices = [_Choice(content)] if content is not None else []


class _MockStream:
    """模拟 OpenAI 异步流：async with + async for。

    - chunks: 文本列表，依次 yield（None = 空 chunk，choices=[]）
    - error_after: 产出 N 个 chunk 后抛异常（模拟上游断连）
    """

    def __init__(self, chunks: list, error_after: int | None = None):
        self._chunks = chunks
        self._error_after = error_after
        self._idx = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._error_after is not None and self._idx >= self._error_after:
            raise ConnectionError("mock upstream failure")
        if self._idx >= len(self._chunks):
            raise StopAsyncIteration
        c = _Chunk(self._chunks[self._idx])
        self._idx += 1
        return c


class _MockCompletions:
    """模拟 client.chat.completions：每次 create() 调用返回下一个预设行为。

    behaviors: list — 每项为 _MockStream 实例（成功）或 Exception 实例（create 阶段失败）
    """

    def __init__(self, behaviors: list):
        self._behaviors = iter(behaviors)
        self.call_kwargs_list: list[dict] = []

    async def create(self, **kwargs):
        self.call_kwargs_list.append(kwargs)
        behavior = next(self._behaviors)
        if isinstance(behavior, BaseException):
            raise behavior
        return behavior


class _MockChat:
    def __init__(self, completions):
        self.completions = completions


class _MockClient:
    def __init__(self, completions):
        self.chat = _MockChat(completions)


# ---------- fixture ----------

@pytest.fixture(autouse=True)
def _clear_llm_cache():
    """每个测试前后清除 get_async_client 的 lru_cache。"""
    get_async_client.cache_clear()
    yield
    get_async_client.cache_clear()


@pytest.fixture
def llm_enabled(monkeypatch):
    """让 settings.llm_ready 返回 True。"""
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-test-key")
    monkeypatch.setattr(settings, "LLM_MODEL", "test-model")


def _install_mock_client(monkeypatch, completions: _MockCompletions):
    client = _MockClient(completions)
    monkeypatch.setattr("app.services.llm.get_async_client", lambda: client)
    return completions


async def _collect_stream_chat(*args, **kwargs) -> list[str]:
    """消费 stream_chat 异步生成器，返回文本片段列表。"""
    from app.services.llm import stream_chat
    parts = []
    async for text in stream_chat(*args, **kwargs):
        parts.append(text)
    return parts


# ---------- 测试 ----------

def test_get_async_client_raises_when_llm_not_ready(monkeypatch):
    """settings.llm_ready=False 时 get_async_client 抛 LLMError。"""
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    with pytest.raises(LLMError, match="LLM 未配置"):
        get_async_client()


@pytest.mark.anyio
async def test_stream_chat_retry_once_on_connect_failure(monkeypatch, llm_enabled):
    """第一次 create 抛连接异常、第二次成功 → 正常产出（重试 1 次）。"""
    comps = _MockCompletions([
        ConnectionError("timeout"),               # 第一次：create 阶段失败
        _MockStream(["hello", " world"]),         # 第二次：成功
    ])
    _install_mock_client(monkeypatch, comps)

    parts = await _collect_stream_chat("sys", "usr")
    assert parts == ["hello", " world"]


@pytest.mark.anyio
async def test_stream_chat_error_after_produced_no_retry(monkeypatch, llm_enabled):
    """已 yield 过内容后抛异常 → LLMError，不重复推送。"""
    comps = _MockCompletions([
        _MockStream(["part1", "part2"], error_after=2),  # 产出 2 段后上游断连
    ])
    _install_mock_client(monkeypatch, comps)

    with pytest.raises(LLMError, match="AI 回应中断了"):
        await _collect_stream_chat("sys", "usr")


@pytest.mark.anyio
async def test_stream_chat_two_consecutive_failures(monkeypatch, llm_enabled):
    """连续两次失败 → LLMError('AI 服务暂时不可用')。"""
    comps = _MockCompletions([
        ConnectionError("first failure"),
        TimeoutError("second failure"),
    ])
    _install_mock_client(monkeypatch, comps)

    with pytest.raises(LLMError, match="AI 服务暂时不可用"):
        await _collect_stream_chat("sys", "usr")


@pytest.mark.anyio
async def test_stream_chat_temperature_passthrough(monkeypatch, llm_enabled):
    """显式传 temperature=0.5 时 create 收到 0.5。"""
    comps = _MockCompletions([
        _MockStream(["ok"]),
    ])
    _install_mock_client(monkeypatch, comps)

    await _collect_stream_chat("sys", "usr", temperature=0.5)
    assert comps.call_kwargs_list[0]["temperature"] == 0.5


@pytest.mark.anyio
async def test_stream_chat_empty_chunks_skipped(monkeypatch, llm_enabled):
    """空 chunk（choices=[]）和 delta.content=None 被跳过，不产出。"""
    comps = _MockCompletions([
        _MockStream([None, "real", None, "text"]),  # None → choices=[]
    ])
    _install_mock_client(monkeypatch, comps)

    parts = await _collect_stream_chat("sys", "usr")
    assert parts == ["real", "text"]
