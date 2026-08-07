"""认证：密码哈希（pbkdf2）+ 基于 session 的登录态。

密码哈希使用 hashlib.pbkdf2_hmac（标准库），格式：
    pbkdf2$<iterations>$<salt_b64>$<hash_b64>
会话登录态写 user_id（int），由 Starlette SessionMiddleware 签名。
"""
from __future__ import annotations

import hashlib
import secrets
from typing import Optional

from fastapi import Request
from pydantic import BaseModel

ITERATIONS = 200_000  # pbkdf2 迭代次数


# ---------- 密码哈希 ----------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, ITERATIONS
    )
    return f"pbkdf2${ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        iterations = int(iters)
    except (AttributeError, ValueError):
        # 任何格式错误（含库中 hash 损坏）一律返回 False，绝不抛出——避免登录直接 500
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return secrets.compare_digest(dk, expected)


# ---------- 当前用户 ----------

class CurrentUser(BaseModel):
    id: int
    username: str


def set_session_user(request: Request, user_id: int, username: str, sign: str = "") -> None:
    request.session["user_id"] = user_id
    request.session["username"] = username
    # 带回用户持久化的星座偏好（users.sign）；注册时为空 = 未选
    request.session["sign"] = sign or ""


def clear_session_user(request: Request) -> None:
    request.session.pop("user_id", None)
    request.session.pop("username", None)
    request.session.pop("sign", None)


def get_current_user(request: Request) -> Optional[CurrentUser]:
    uid = request.session.get("user_id")
    username = request.session.get("username")
    if uid is None or not username:
        return None
    return CurrentUser(id=uid, username=username)


async def require_user(request: Request) -> CurrentUser:
    """FastAPI 依赖：必须已登录，否则 401。"""
    user = get_current_user(request)
    if user is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="请先登录")
    return user
