"""情绪日记模块测试：TestClient 集成（校验/限流/落库/删除/页面注入）。

NDJSON 解析复用 dream.parse_dream（journal 无 mood 行 → tags 为 []），故不重复单元测试。
运行：python -m pytest tests/test_journal.py -v
"""
import pytest

from app.config import settings
from app.db import get_conn
from app.services.dream import parse_dream  # journal 共用此解析器

JOURNAL_NDJSON = (
    '{"k":"section","title":"我在听","text":"我听到你了，这一刻不容易"}\n'
    '{"k":"section","title":"一个新的视角","text":"也许疲惫也在提醒你该停一下"}\n'
    '{"k":"advice","text":"今晚试着早睡十分钟"}\n'
)


def test_parse_journal_ndjson_via_shared_parser():
    # journal 无 mood 行 → tags 为空；section/advice 正常解析
    d = parse_dream(JOURNAL_NDJSON)
    assert d["tags"] == []
    assert len(d["sections"]) == 2
    assert d["sections"][0]["title"] == "我在听"
    assert d["advice"] == "今晚试着早睡十分钟"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


def _register(client, username="tester", password="123456"):
    r = client.post("/register", data={"username": username, "password": password},
                    follow_redirects=False)
    assert r.status_code == 303


def _enable_llm(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-test-key")


def test_length_validation(client):
    _register(client)
    assert client.post("/journal/api/respond", json={"mood": "", "content": ""}).status_code == 400
    assert client.post("/journal/api/respond", json={"mood": "焦虑", "content": "x" * 2001}).status_code == 400
    assert client.post("/journal/api/respond", json={"mood": "x" * 21, "content": "ok"}).status_code == 400


def test_respond_503_when_llm_not_ready(client):
    _register(client)
    r = client.post("/journal/api/respond", json={"mood": "焦虑", "content": "今天好累"})
    assert r.status_code == 503


def test_respond_sse_and_persist(client, monkeypatch):
    _register(client)
    _enable_llm(monkeypatch)

    async def _fake(system, user, **kw):
        yield '{"k":"section","title":"我在听","text":"我听到你了"}\n'
        yield '{"k":"advice","text":"试着休息"}\n'
    monkeypatch.setattr("app.services.journal.stream_chat", _fake)

    r = client.post("/journal/api/respond", json={"mood": "焦虑", "content": "今天好累"})
    assert r.status_code == 200
    body = r.text
    assert '"type": "delta"' in body
    assert '"type": "done"' in body

    # 落库：mood 单独存；ai_response 存原始 NDJSON 全文
    with get_conn() as conn:
        row = conn.execute("SELECT mood, ai_response FROM journals WHERE user_id = ?", (1,)).fetchone()
    assert row["mood"] == "焦虑"
    assert '"k":"section"' in row["ai_response"]


def test_rate_limit(client, monkeypatch):
    _register(client)
    _enable_llm(monkeypatch)
    monkeypatch.setattr("app.routers.journal._QUOTA_LIMIT", 3)

    async def _fake(system, user, **kw):
        yield '{"k":"advice","text":"嗯"}\n'
    monkeypatch.setattr("app.services.journal.stream_chat", _fake)

    for _ in range(3):
        assert client.post("/journal/api/respond", json={"mood": "", "content": "x"}).status_code == 200
    assert client.post("/journal/api/respond", json={"mood": "", "content": "x"}).status_code == 429


def test_delete_own_and_notfound(client):
    _register(client)
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO journals (user_id, mood, content, ai_response) VALUES (1, '焦虑', 'c', '')"
        )
        own_id = cur.lastrowid
    assert client.delete(f"/journal/api/{own_id}").status_code == 200
    assert client.delete(f"/journal/api/{own_id}").status_code == 404


def test_delete_others_returns_404(client):
    _register(client, "user_a")
    with get_conn() as conn:
        cur = conn.execute("INSERT INTO users (username, password_hash) VALUES ('user_b', 'x')")
        b_uid = cur.lastrowid
        cur2 = conn.execute(
            "INSERT INTO journals (user_id, mood, content, ai_response) VALUES (?, '', 'c', '')",
            (b_uid,),
        )
        b_journal = cur2.lastrowid
    assert client.delete(f"/journal/api/{b_journal}").status_code == 404


def test_journal_page_injects_structured_json(client):
    _register(client)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO journals (user_id, mood, content, ai_response) VALUES (1, '焦虑', '内容', ?)",
            (JOURNAL_NDJSON,),
        )
    html = client.get("/journal").text
    assert "initial-journals" in html
    assert "我在听" in html or "\\u6211\\u5728\\u542c" in html
