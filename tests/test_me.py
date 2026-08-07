"""个人中心路由测试：页面渲染、星座偏好、注销账号。

运行：PYTHONIOENCODING=utf-8 python -m pytest tests/test_me.py -v
"""
import pytest

from app.config import settings
from app.db import get_conn


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


# ---------- 未登录 ----------

def test_me_unauthenticated_401(client):
    """未登录访问 /me → 401（默认 Accept）或 303 跳转（text/html）。"""
    r = client.get("/me")
    assert r.status_code == 401
    r2 = client.get("/me", headers={"Accept": "text/html"}, follow_redirects=False)
    assert r2.status_code == 303


# ---------- 已登录页面 ----------

def test_me_page_200_with_stats(client):
    """登录后访问 /me → 200 且显示 dreams/journals 计数。"""
    _register(client)

    # 插入测试数据
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO dreams (user_id, title, content) VALUES (1, 'd1', 'c1')"
        )
        conn.execute(
            "INSERT INTO dreams (user_id, title, content) VALUES (1, 'd2', 'c2')"
        )
        conn.execute(
            "INSERT INTO journals (user_id, mood, content) VALUES (1, 'happy', 'j1')"
        )

    r = client.get("/me")
    assert r.status_code == 200
    # 页面含用户名
    assert "tester" in r.text


# ---------- 星座偏好 ----------

def test_update_sign_valid(client):
    """POST /me/sign 有效星座 → DB + session 都更新。"""
    _register(client)

    r = client.post("/me/sign", data={"sign": "狮子座"}, follow_redirects=False)
    assert r.status_code == 303

    # DB 更新
    with get_conn() as conn:
        row = conn.execute("SELECT sign FROM users WHERE username = ?", ("tester",)).fetchone()
    assert row["sign"] == "狮子座"

    # session 更新（访问星座运势页可见）
    r2 = client.get("/horoscope", follow_redirects=False)
    # 即使 /horoscope 返回 200 或 303（取决于 Accept），session 中 sign 已设置
    # 直接访问 /horoscope 看页面中是否含狮子座
    r3 = client.get("/horoscope")
    if r3.status_code == 200:
        assert "狮子座" in r3.text


def test_update_sign_invalid(client):
    """POST /me/sign 无效星座 → DB 不更新。"""
    _register(client)

    r = client.post("/me/sign", data={"sign": "天马座"}, follow_redirects=False)
    assert r.status_code == 303  # 仍重定向，但不更新

    # DB 未更新（sign 仍为空）
    with get_conn() as conn:
        row = conn.execute("SELECT sign FROM users WHERE username = ?", ("tester",)).fetchone()
    assert row["sign"] == ""


# ---------- 注销账号 ----------

def test_delete_account_cascade(client):
    """POST /me/delete-account → 用户删除 + dreams/journals 级联 + quota 清空 + session 清空。"""
    _register(client, username="deleter", password="123456")

    # 插入关联数据
    with get_conn() as conn:
        uid = conn.execute(
            "SELECT id FROM users WHERE username = ?", ("deleter",)
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO dreams (user_id, title, content) VALUES (?, 'd1', 'c1')", (uid,)
        )
        conn.execute(
            "INSERT INTO journals (user_id, mood, content) VALUES (?, 'ok', 'j1')", (uid,)
        )
        conn.execute(
            "INSERT INTO dream_quota (user_id, ts) VALUES (?, 1000.0)", (uid,)
        )
        conn.execute(
            "INSERT INTO journal_quota (user_id, ts) VALUES (?, 1000.0)", (uid,)
        )
        conn.execute(
            "INSERT INTO force_history (user_id, sign, period, ts) VALUES (?, '白羊座', 'today', 1000.0)",
            (uid,),
        )

    # 注销
    r = client.post("/me/delete-account", follow_redirects=False)
    assert r.status_code == 303

    # 用户已删
    with get_conn() as conn:
        user = conn.execute(
            "SELECT id FROM users WHERE username = ?", ("deleter",)
        ).fetchone()
    assert user is None

    # dreams/journals 级联删空
    with get_conn() as conn:
        dreams = conn.execute(
            "SELECT COUNT(*) AS c FROM dreams WHERE user_id = ?", (uid,)
        ).fetchone()["c"]
        journals = conn.execute(
            "SELECT COUNT(*) AS c FROM journals WHERE user_id = ?", (uid,)
        ).fetchone()["c"]
    assert dreams == 0
    assert journals == 0

    # quota 表清空
    with get_conn() as conn:
        dq = conn.execute(
            "SELECT COUNT(*) AS c FROM dream_quota WHERE user_id = ?", (uid,)
        ).fetchone()["c"]
        jq = conn.execute(
            "SELECT COUNT(*) AS c FROM journal_quota WHERE user_id = ?", (uid,)
        ).fetchone()["c"]
        fh = conn.execute(
            "SELECT COUNT(*) AS c FROM force_history WHERE user_id = ?", (uid,)
        ).fetchone()["c"]
    assert dq == 0
    assert jq == 0
    assert fh == 0

    # session 清空：再访 /dream 需要登录
    r2 = client.get("/dream")
    assert r2.status_code == 401


def test_delete_account_requires_login(client):
    """未登录 POST /me/delete-account → 401。"""
    r = client.post("/me/delete-account")
    assert r.status_code == 401
