"""解梦模块测试：parse_dream 单元 + TestClient 集成（校验/限流/落库/删除）。

集成测试用 fastapi TestClient（进程内），DB 隔离到 tmp_path，LLM 按需 mock，不连真实服务。
运行：python -m pytest tests/test_dream.py -v
"""
import pytest

from app.config import settings
from app.db import get_conn
from app.services.dream import parse_dream


# ---------- parse_dream 单元测试 ----------

VALID_NDJSON = (
    '{"k":"mood","tags":["焦虑","坠落"]}\n'
    '{"k":"section","title":"核心意象","text":"坠落象征失控感"}\n'
    '{"k":"section","title":"情绪映射","text":"近期压力的投射"}\n'
    '{"k":"advice","text":"试着睡前放空几分钟"}\n'
)

# 新格式：标题 JSON 行 + 纯文本正文（正文逐 token 流式，前端打字机）
NEW_FORMAT = (
    '{"k":"mood","tags":["焦虑","坠落"]}\n'
    '{"k":"section","title":"核心意象"}\n'
    '坠落往往象征失控感，常映射现实中对某件事缺乏掌控。\n'
    '{"k":"section","title":"情绪映射"}\n'
    '这份失控感或许来自近期积压的压力。\n'
    '{"k":"advice"}\n'
    '今晚试着深呼吸。\n'
)


def test_parse_dream_valid():
    d = parse_dream(VALID_NDJSON)
    assert d["tags"] == ["焦虑", "坠落"]
    assert len(d["sections"]) == 2
    assert d["sections"][0] == {"title": "核心意象", "text": "坠落象征失控感"}
    assert d["sections"][1] == {"title": "情绪映射", "text": "近期压力的投射"}
    assert d["advice"] == "试着睡前放空几分钟"


def test_parse_dream_new_format_title_plus_text():
    # 新格式：标题 JSON 行 + 紧随的纯文本正文 → 解析结果与旧 NDJSON 一致
    d = parse_dream(NEW_FORMAT)
    assert d["tags"] == ["焦虑", "坠落"]
    assert len(d["sections"]) == 2
    assert d["sections"][0] == {"title": "核心意象", "text": "坠落往往象征失控感，常映射现实中对某件事缺乏掌控。"}
    assert d["sections"][1] == {"title": "情绪映射", "text": "这份失控感或许来自近期积压的压力。"}
    assert d["advice"] == "今晚试着深呼吸。"


def test_parse_dream_new_format_multiline_text():
    # 正文跨多行：多行应合并为单个 section 的 text
    raw = (
        '{"k":"section","title":"意象"}\n'
        '第一行正文。\n'
        '第二行正文。\n'
        '{"k":"advice"}\n'
        '建议。\n'
    )
    d = parse_dream(raw)
    assert d["sections"] == [{"title": "意象", "text": "第一行正文。\n第二行正文。"}]
    assert d["advice"] == "建议。"


def test_parse_dream_tolerant_to_noise():
    # 混入代码围栏、空行、非法行 —— 全部容错跳过
    raw = '```json\n{"k":"mood","tags":["水"]}\n\n这不是JSON\n{"k":"section","title":"意象","text":"流动"}\n```'
    d = parse_dream(raw)
    assert d["tags"] == ["水"]
    assert d["sections"] == [{"title": "意象", "text": "流动"}]


def test_parse_dream_legacy_plain_text_fallback():
    # 旧的纯文本解读（无合法 NDJSON）→ 整段作单个 section，不丢显示
    legacy = "这是一段旧的纯文本解读，没有任何JSON结构。"
    d = parse_dream(legacy)
    assert d["tags"] == []
    assert d["advice"] == ""
    assert len(d["sections"]) == 1
    assert d["sections"][0]["text"] == legacy
    assert d["sections"][0]["title"] == ""


def test_parse_dream_empty_and_tags_cleanup():
    assert parse_dream("") == {"tags": [], "sections": [], "advice": ""}
    # tags 非字符串/空值被清洗；超过上限被截断
    d = parse_dream('{"k":"mood","tags":["a", "", 3, "b"]}')
    assert d["tags"] == ["a", "3", "b"]


# ---------- TestClient 集成测试 ----------

@pytest.fixture
def client(tmp_path, monkeypatch):
    """隔离 DB 到临时路径；LLM 默认未配置，各测试按需覆盖。"""
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
    assert client.post("/dream/api/interpret", json={"title": "t", "content": ""}).status_code == 400
    assert client.post("/dream/api/interpret", json={"title": "t", "content": "x" * 2001}).status_code == 400
    assert client.post("/dream/api/interpret", json={"title": "t" * 61, "content": "ok"}).status_code == 400


def test_interpret_503_when_llm_not_ready(client):
    _register(client)  # LLM_API_KEY 为空 → llm_ready=False
    r = client.post("/dream/api/interpret", json={"title": "t", "content": "一个梦"})
    assert r.status_code == 503


def test_interpret_sse_and_persist(client, monkeypatch):
    _register(client)
    _enable_llm(monkeypatch)

    async def _fake_stream(system, user, **kw):
        yield '{"k":"mood","tags":["焦虑"]}\n'
        yield '{"k":"section","title":"核心意象","text":"坠落"}\n'
        yield '{"k":"advice","text":"试试深呼吸"}\n'
    monkeypatch.setattr("app.services.dream.stream_chat", _fake_stream)

    r = client.post("/dream/api/interpret", json={"title": "坠落的梦", "content": "我一直在往下掉"})
    assert r.status_code == 200
    body = r.text
    assert '"type": "delta"' in body
    assert '"type": "done"' in body
    assert '"id"' in body and "created_at" in body

    # 落库：interpretation 存原始 NDJSON，tags 解析后逗号分隔
    with get_conn() as conn:
        row = conn.execute(
            "SELECT tags, interpretation FROM dreams WHERE user_id = ?", (1,)
        ).fetchone()
    assert row["tags"] == "焦虑"
    assert '"k":"mood"' in row["interpretation"]


def test_rate_limit(client, monkeypatch):
    _register(client)
    _enable_llm(monkeypatch)
    monkeypatch.setattr("app.routers.dream._QUOTA_LIMIT", 3)  # 调低阈值便于测试

    async def _fake_stream(system, user, **kw):
        yield '{"k":"mood","tags":["x"]}\n'
    monkeypatch.setattr("app.services.dream.stream_chat", _fake_stream)

    for _ in range(3):
        assert client.post("/dream/api/interpret", json={"title": "t", "content": "梦"}).status_code == 200
    # 第 4 次：超出窗口配额 → 429
    assert client.post("/dream/api/interpret", json={"title": "t", "content": "梦"}).status_code == 429


def test_delete_own_and_notfound(client):
    _register(client)
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO dreams (user_id, title, content, interpretation, tags, mood) "
            "VALUES (1, 't', 'c', '', '', '')"
        )
        own_id = cur.lastrowid
    assert client.delete(f"/dream/api/{own_id}").status_code == 200
    assert client.delete(f"/dream/api/{own_id}").status_code == 404  # 已删/不存在


def test_delete_others_returns_404(client):
    _register(client, "user_a")
    # 另一个用户 user_b 及其梦境（直接插库，不登录 user_b）
    with get_conn() as conn:
        cur = conn.execute("INSERT INTO users (username, password_hash) VALUES ('user_b', 'x')")
        b_uid = cur.lastrowid
        cur2 = conn.execute(
            "INSERT INTO dreams (user_id, title, content, interpretation, tags, mood) "
            "VALUES (?, 'b', 'c', '', '', '')",
            (b_uid,),
        )
        b_dream = cur2.lastrowid
    # user_a 试图删 user_b 的梦 → 归属不符 → 404（且不泄漏存在性）
    assert client.delete(f"/dream/api/{b_dream}").status_code == 404


def test_dream_page_injects_structured_json(client):
    _register(client)
    # 插一条带 NDJSON 解读的历史记录
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO dreams (user_id, title, content, interpretation, tags, mood) "
            "VALUES (1, '旧梦', '内容', ? , '焦虑,坠落', '')",
            (VALID_NDJSON,),
        )
    html = client.get("/dream").text
    # tojson 注入了结构化数据
    assert "initial-dreams" in html
    assert "核心意象" in html or "\\u6838\\u5fc3\\u610f\\u8c61" in html  # tojson 中文可能转义
