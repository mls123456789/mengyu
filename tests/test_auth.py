"""认证模块测试：密码哈希、注册、登录、登出、限流、session 管理。

集成测试用 fastapi TestClient（进程内），DB 隔离到 tmp_path，LLM 不配置。
运行：PYTHONIOENCODING=utf-8 python -m pytest tests/test_auth.py -v
"""
import pytest

from app.auth import hash_password, verify_password


# ---------- 密码哈希单元测试 ----------

def test_hash_password_format_and_roundtrip():
    """hash_password 输出 pbkdf2$200000$<salt_hex>$<hash_hex>；verify 正确密码 True。"""
    h = hash_password("test123")
    parts = h.split("$")
    assert len(parts) == 4
    assert parts[0] == "pbkdf2"
    assert parts[1] == "200000"
    # salt 16 bytes = 32 hex chars；hash 为 sha256 = 32 bytes = 64 hex chars
    salt_bytes = bytes.fromhex(parts[2])
    hash_bytes = bytes.fromhex(parts[3])
    assert len(salt_bytes) == 16
    assert len(hash_bytes) == 32
    # 往返验证
    assert verify_password("test123", h) is True
    # 同一密码两次哈希结果不同（随机 salt）
    h2 = hash_password("test123")
    assert h != h2
    assert verify_password("test123", h2) is True


def test_verify_password_wrong_password():
    """正确密码的 hash 对错误密码返回 False。"""
    h = hash_password("correct_password")
    assert verify_password("wrong_password", h) is False


@pytest.mark.parametrize("malformed", [
    "garbage",                       # 单段，无 $ 分隔
    "",                              # 空串
    None,                            # None
    "pbkdf2$200000$abc",             # 分段不足（3 段 < 4）
    "a$b$c$d$e",                    # 分段过多（5 段）
    "pbkdf2$notanum$aabb$ccdd",     # 非数字迭代数
    "bcrypt$12$aabb$ccdd",          # 未知算法
    "pbkdf2$200000$ZZZZ$aabb",      # salt 非 hex
    "pbkdf2$200000$aabb$ZZZZ",      # hash 非 hex
])
def test_verify_password_malformed_tolerant(malformed):
    """畸形存储值一律返回 False，不抛异常（防登录 500）。"""
    assert verify_password("anything", malformed) is False


# ---------- TestClient 集成测试 ----------

@pytest.fixture
def client(tmp_path, monkeypatch):
    """隔离 DB 到临时路径；LLM 默认未配置。"""
    from app.config import settings
    monkeypatch.setattr(settings, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


def _register(client, username="tester", password="123456"):
    return client.post(
        "/register",
        data={"username": username, "password": password},
        follow_redirects=False,
    )


def _login(client, username="tester", password="123456"):
    return client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )


# ---------- 注册 ----------

def test_register_success_sets_session(client):
    """注册成功 303 + session 已设置（再访需登录页不跳转）。"""
    r = _register(client, username="newuser", password="123456")
    assert r.status_code == 303
    # 已登录：访问受保护页面 /dream 返回 200（不被 401 拦截）
    r2 = client.get("/dream")
    assert r2.status_code == 200


def test_register_validation(client):
    """用户名过短/过长、密码过短 → 400。"""
    # 用户名过短（1 字符，最小 2）
    r = _register(client, username="a", password="123456")
    assert r.status_code == 400
    assert "用户名" in r.text

    # 用户名过长（21 字符，最大 20）
    r = _register(client, username="a" * 21, password="123456")
    assert r.status_code == 400
    assert "用户名" in r.text

    # 密码过短（5 字符，最小 6）
    r = _register(client, username="ok_name", password="12345")
    assert r.status_code == 400
    assert "密码" in r.text


def test_register_duplicate_username(client):
    """重复用户名 → 400 + 「已被占用」。"""
    r1 = _register(client, username="dup_user", password="123456")
    assert r1.status_code == 303
    # 同一用户名再注册 → IntegrityError → 400
    r2 = _register(client, username="dup_user", password="654321")
    assert r2.status_code == 400
    assert "已被占用" in r2.text


def test_register_rate_limit(client, monkeypatch):
    """monkeypatch _REGISTER_LIMIT=2 → 第 3 次 429 + 文案；且 429 响应不含 traceback。"""
    monkeypatch.setattr("app.routers.auth._REGISTER_LIMIT", 2)
    # 前 2 次通过限流
    for i in range(2):
        r = _register(client, username=f"rl_u{i}", password="123456")
        assert r.status_code == 303
    # 第 3 次：限流 → 429
    r = _register(client, username="rl_blocked", password="123456")
    assert r.status_code == 429
    assert "尝试次数过多" in r.text
    # 不含 traceback（排除 500 错误页面泄漏）
    assert "Traceback" not in r.text
    assert "traceback" not in r.text.lower()


# ---------- 登录 ----------

def test_login_success_and_wrong_password(client):
    """统一文案：不存在用户与密码错误文案完全一致；正确密码 303。"""
    _register(client, username="loginuser", password="123456")

    # 密码错误 → 400 + 统一文案
    r_wrong = _login(client, username="loginuser", password="wrongpw")
    assert r_wrong.status_code == 400
    assert "用户名或密码不正确" in r_wrong.text

    # 用户不存在 → 400 + 同一文案（dummy hash 时序均衡，不泄漏用户是否存在）
    r_nouser = _login(client, username="nonexistent", password="whatever")
    assert r_nouser.status_code == 400
    assert "用户名或密码不正确" in r_nouser.text

    # 正确密码 → 303 重定向
    r_ok = _login(client, username="loginuser", password="123456")
    assert r_ok.status_code == 303


def test_login_rate_limit(client, monkeypatch):
    """monkeypatch _LOGIN_LIMIT=3 → 第 4 次 429；错误尝试也计数。"""
    monkeypatch.setattr("app.routers.auth._LOGIN_LIMIT", 3)
    # 先注册一个用户
    _register(client, username="rllogin", password="123456")
    # 3 次错误登录（每次都计数）
    for i in range(3):
        r = _login(client, username="rllogin", password="wrongpw")
        assert r.status_code == 400
    # 第 4 次：限流 → 429（即使密码正确也被拦）
    r = _login(client, username="rllogin", password="123456")
    assert r.status_code == 429
    assert "尝试次数过多" in r.text


def test_login_restores_sign(client):
    """直接插 users 行含 sign='白羊座' → 登录 303 → GET /me 200 + 页面含星座。"""
    from app.db import get_conn

    h = hash_password("signpass123")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO users (username, password_hash, sign) VALUES (?, ?, ?)",
            ("signuser", h, "白羊座"),
        )
    # 登录
    r = _login(client, username="signuser", password="signpass123")
    assert r.status_code == 303
    # /me 需登录态 → 200 说明 session 已恢复（含 user_id/username）
    r2 = client.get("/me")
    assert r2.status_code == 200
    # 页面含星座信息（模板可能 unicode 转义）
    text = r2.text
    assert "白羊座" in text or "\\u767d\\u7f8a\\u5ea7" in text


# ---------- 登出 ----------

def test_logout_clears_session_including_sign(client):
    """注册（自动登录）→ 登出 → 访问 /dream 应被要求登录。"""
    # 注册 → 自动登录
    _register(client, username="logoutuser", password="123456")
    assert client.get("/dream").status_code == 200

    # 登出 → 303 重定向到 /login，session 清除（含 user_id/username/sign）
    r = client.post("/logout", follow_redirects=False)
    assert r.status_code == 303

    # 登出后访问受保护页面 → 401
    # （TestClient 默认 Accept: */*，不含 text/html → 401 handler 走 JSON 分支）
    r2 = client.get("/dream")
    assert r2.status_code == 401

    # 用 text/html Accept 模拟浏览器导航 → 303 重定向到 /login
    # （TestClient 默认跟随 GET 重定向，须 follow_redirects=False 才能拿到 303）
    r3 = client.get("/dream", headers={"Accept": "text/html"}, follow_redirects=False)
    assert r3.status_code == 303
    assert "/login" in r3.headers.get("location", "")


# ---------- 401 JSON ----------

def test_require_user_401_json(client):
    """未登录访问 /api/ 路径或带非 HTML Accept → 401 JSON（而非 303 跳转）。"""
    # /api/ 路径 + text/html Accept → 仍然 401 JSON（/api/ 路径不走浏览器跳转）
    r = client.post(
        "/dream/api/interpret",
        json={"title": "t", "content": "梦境内容"},
        headers={"Accept": "text/html"},
    )
    assert r.status_code == 401
    body = r.json()
    assert "detail" in body
    assert "登录" in body["detail"]

    # 非 /api/ 页面路径 + application/json Accept → 401 JSON
    r2 = client.get("/dream", headers={"Accept": "application/json"})
    assert r2.status_code == 401
    body2 = r2.json()
    assert "detail" in body2

    # /api/ DELETE 路径未登录 → 401 JSON
    r3 = client.delete("/dream/api/999", headers={"Accept": "*/*"})
    assert r3.status_code == 401
