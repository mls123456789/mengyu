"""星座运势路由测试：参数校验、缓存命中/未命中、force 限流、星座持久化、页面渲染。

运行：PYTHONIOENCODING=utf-8 python -m pytest tests/test_horoscope.py -v
"""
import json

import pytest

from app.config import settings
from app.db import get_conn


# ---------- 常量与 helpers ----------

MOCK_NDJSON_LINES = [
    '{"k":"overall","score":82,"text":"综合运势不错"}',
    '{"k":"love","score":65,"text":"爱情平稳"}',
    '{"k":"career","score":70,"text":"事业有进展"}',
    '{"k":"wealth","score":55,"text":"财运一般"}',
    '{"k":"health","score":80,"text":"注意锻炼"}',
    '{"k":"lucky","color":"蓝色","number":7,"direction":"东","item":"一本书"}',
    '{"k":"match","best":"天秤座","worst":"巨蟹座"}',
    '{"k":"advice","yi":["出行","学习"],"ji":["冲动","熬夜"]}',
    '{"k":"motto","text":"保持积极心态"}',
]


def _make_mock_stream(call_counter: list):
    """返回一个 async generator 函数；每次调用 call_counter[0] += 1。"""
    async def _fake(system, user, **kw):
        call_counter[0] += 1
        for line in MOCK_NDJSON_LINES:
            yield line + "\n"
    return _fake


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-test-key")
    monkeypatch.setattr(settings, "LLM_MODEL", "test-model")
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


def _register(client, username="tester", password="123456"):
    r = client.post("/register", data={"username": username, "password": password},
                    follow_redirects=False)
    assert r.status_code == 303


def _fortune(client, sign="白羊座", period="today", force=False):
    return client.post(
        "/horoscope/api/fortune",
        json={"sign": sign, "period": period, "force": force},
    )


def _parse_sse_frames(text: str) -> list[dict]:
    frames = []
    for line in text.splitlines():
        if line.startswith("data: "):
            frames.append(json.loads(line[6:]))
    return frames


# ---------- 参数校验 ----------

def test_invalid_sign_400(client):
    _register(client)
    r = _fortune(client, sign="天马座")
    assert r.status_code == 400
    assert "星座" in r.json()["detail"]


def test_invalid_period_400(client):
    _register(client)
    r = _fortune(client, sign="白羊座", period="yearly")
    assert r.status_code == 400
    assert "时段" in r.json()["detail"]


def test_llm_not_ready_503(client, monkeypatch):
    """LLM 未配置 → 503。"""
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    _register(client)
    r = _fortune(client)
    assert r.status_code == 503


# ---------- cache miss + 落库 ----------

def test_cache_miss_generates_and_persists(client, monkeypatch):
    """首次请求（cache miss）→ 调用 LLM → 200 SSE → horoscopes 表落库。"""
    _register(client)
    call_count = [0]
    monkeypatch.setattr(
        "app.services.horoscope.stream_chat",
        _make_mock_stream(call_count),
    )

    r = _fortune(client, sign="白羊座", period="today")
    assert r.status_code == 200
    assert call_count[0] == 1

    # 检查 SSE 帧
    frames = _parse_sse_frames(r.text)
    delta_frames = [f for f in frames if f["type"] == "delta"]
    done_frames = [f for f in frames if f["type"] == "done"]
    assert len(delta_frames) >= 1
    assert len(done_frames) == 1
    # done 帧含 meta
    assert done_frames[0]["sign"] == "白羊座"
    assert done_frames[0]["period"] == "today"

    # horoscopes 表落库（分数由 apply_and_serialize 用确定性值覆盖，只检查 content 含 NDJSON 键）
    with get_conn() as conn:
        row = conn.execute(
            "SELECT content FROM horoscopes WHERE sign = ? AND period = ?",
            ("白羊座", "today"),
        ).fetchone()
    assert row is not None
    content = row["content"]
    # apply_and_serialize 用 json.dumps 重新序列化（带空格），检查关键 key 存在
    assert '"overall"' in content
    assert '"love"' in content
    assert '"motto"' in content


# ---------- cache hit ----------

def test_cache_hit_no_llm_call(client, monkeypatch):
    """第二次同 sign+period 请求 → 回放缓存，不再调用 LLM。"""
    _register(client)
    call_count = [0]
    monkeypatch.setattr(
        "app.services.horoscope.stream_chat",
        _make_mock_stream(call_count),
    )

    # 第一次：cache miss → 生成
    r1 = _fortune(client, sign="金牛座", period="today")
    assert r1.status_code == 200
    assert call_count[0] == 1

    # 第二次：cache hit → 回放（mock 不再被调用）
    r2 = _fortune(client, sign="金牛座", period="today")
    assert r2.status_code == 200
    assert call_count[0] == 1  # 没有增加

    # 回放的 SSE 含 done 帧
    frames = _parse_sse_frames(r2.text)
    done_frames = [f for f in frames if f["type"] == "done"]
    assert len(done_frames) == 1


# ---------- force 限流 ----------

def test_force_rate_limit(client, monkeypatch):
    """monkeypatch _FORCE_LIMIT=1 → 第二次 force=true → 429。"""
    _register(client)
    monkeypatch.setattr("app.routers.horoscope._FORCE_LIMIT", 1)
    call_count = [0]
    monkeypatch.setattr(
        "app.services.horoscope.stream_chat",
        _make_mock_stream(call_count),
    )

    # 第一次 force=true → 通过（cache miss + force 生成）
    r1 = _fortune(client, sign="双子座", period="today", force=True)
    assert r1.status_code == 200
    assert call_count[0] == 1

    # 第二次 force=true → 限流 → 429
    r2 = _fortune(client, sign="双子座", period="today", force=True)
    assert r2.status_code == 429
    assert "换签" in r2.json()["detail"]


# ---------- sign 持久化 ----------

def test_sign_persisted_to_db_and_session(client, monkeypatch):
    """请求运势后 users.sign 和 session 都更新。"""
    _register(client)
    call_count = [0]
    monkeypatch.setattr(
        "app.services.horoscope.stream_chat",
        _make_mock_stream(call_count),
    )

    r = _fortune(client, sign="天蝎座", period="today")
    assert r.status_code == 200

    # DB 中 users.sign 已更新
    with get_conn() as conn:
        row = conn.execute("SELECT sign FROM users WHERE username = ?", ("tester",)).fetchone()
    assert row["sign"] == "天蝎座"

    # session 中 sign 也已设置（访问星座页可见）
    r2 = client.get("/horoscope")
    assert r2.status_code == 200
    # 页面含当前星座
    assert "天蝎座" in r2.text


# ---------- 页面渲染 ----------

def test_horoscope_page_200_with_signs(client):
    """GET /horoscope 页面 200 且含星座列表。"""
    _register(client)
    r = client.get("/horoscope")
    assert r.status_code == 200
    # 页面含十二星座名
    assert "白羊座" in r.text
    assert "双鱼座" in r.text
