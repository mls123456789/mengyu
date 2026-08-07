"""SSE 流式工具测试：sse_stream 帧序列 + sse_replay 回放。

运行：PYTHONIOENCODING=utf-8 python -m pytest tests/test_sse.py -v
"""
import json

import pytest

from app.services.sse import sse_replay, sse_stream


# ---------- helpers ----------

def _parse_frames(raw: str) -> list[dict]:
    """把 SSE 文本拆成 JSON 帧列表。"""
    frames = []
    for line in raw.splitlines():
        if line.startswith("data: "):
            frames.append(json.loads(line[6:]))
    return frames


async def _collect(agen) -> str:
    """把异步生成器的所有输出拼接成字符串。"""
    parts = []
    async for chunk in agen:
        parts.append(chunk)
    return "".join(parts)


# ---------- sse_stream ----------

@pytest.mark.anyio
async def test_sse_stream_normal_flow():
    """delta 帧逐个推送 + 末尾 done 帧含完整文本与 on_done meta。"""

    async def gen():
        yield "hello"
        yield " "
        yield "world"

    def on_done(full_text: str) -> dict:
        return {"id": 42, "char_count": len(full_text)}

    raw = await _collect(sse_stream(gen(), on_done))
    frames = _parse_frames(raw)

    # 3 delta + 1 done
    assert len(frames) == 4
    assert frames[0] == {"type": "delta", "text": "hello"}
    assert frames[1] == {"type": "delta", "text": " "}
    assert frames[2] == {"type": "delta", "text": "world"}
    # done 帧含完整拼接文本 + on_done 返回的 meta
    done = frames[3]
    assert done["type"] == "done"
    assert done["text"] == "hello world"
    assert done["id"] == 42
    assert done["char_count"] == 11


@pytest.mark.anyio
async def test_sse_stream_exception_yields_error_frame():
    """生成器抛异常 → 单帧 error，不崩溃。"""

    async def gen():
        yield "partial"
        raise RuntimeError("upstream blew up")

    raw = await _collect(sse_stream(gen()))
    frames = _parse_frames(raw)

    # 1 delta (已 yield 的) + 1 error
    # 注意：异常在 async for 内部被 catch，已产出的 delta 保留
    delta_frames = [f for f in frames if f["type"] == "delta"]
    error_frames = [f for f in frames if f["type"] == "error"]
    assert len(delta_frames) == 1
    assert delta_frames[0]["text"] == "partial"
    assert len(error_frames) == 1
    assert "生成失败" in error_frames[0]["message"]


@pytest.mark.anyio
async def test_sse_stream_on_done_none_meta_empty():
    """on_done=None 时 meta 为空 dict，done 帧只含 type+text。"""

    async def gen():
        yield "abc"

    raw = await _collect(sse_stream(gen(), on_done=None))
    frames = _parse_frames(raw)
    assert len(frames) == 2
    done = frames[1]
    assert done["type"] == "done"
    assert done["text"] == "abc"
    # 除了 type 和 text 没有其他键
    assert set(done.keys()) == {"type", "text"}


@pytest.mark.anyio
async def test_sse_stream_blocking_on_done_via_to_thread():
    """on_done 是同步阻塞函数（含 import time; time.sleep）也能正常工作。"""
    import time

    async def gen():
        yield "data"

    def slow_on_done(full: str) -> dict:
        time.sleep(0.05)  # 模拟 SQLite 写
        return {"persisted": True}

    raw = await _collect(sse_stream(gen(), slow_on_done))
    frames = _parse_frames(raw)
    assert frames[-1]["persisted"] is True


# ---------- sse_replay ----------

@pytest.mark.anyio
async def test_sse_replay_with_content():
    """有内容 → delta + done 两帧。"""
    raw = await _collect(sse_replay("cached text", {"sign": "白羊座"}))
    frames = _parse_frames(raw)
    assert len(frames) == 2
    assert frames[0] == {"type": "delta", "text": "cached text"}
    done = frames[1]
    assert done["type"] == "done"
    assert done["text"] == "cached text"
    assert done["sign"] == "白羊座"


@pytest.mark.anyio
async def test_sse_replay_empty_content():
    """空内容 → 仅 done 帧（无 delta）。"""
    raw = await _collect(sse_replay("", {"cached": True}))
    frames = _parse_frames(raw)
    assert len(frames) == 1
    assert frames[0]["type"] == "done"
    assert frames[0]["text"] == ""
    assert frames[0]["cached"] is True


@pytest.mark.anyio
async def test_sse_replay_no_meta():
    """meta=None → done 帧仅含 type+text。"""
    raw = await _collect(sse_replay("text"))
    frames = _parse_frames(raw)
    done = frames[-1]
    assert set(done.keys()) == {"type", "text"}
