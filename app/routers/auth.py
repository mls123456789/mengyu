"""认证路由：注册 / 登录 / 登出。

含按 IP 的防爆破限流（auth_quota 表）与时序侧信道缓解
（用户不存在时也执行一次 dummy 哈希校验，统一两条路径耗时）。
"""
from __future__ import annotations

import sqlite3
import time

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.auth import clear_session_user, hash_password, set_session_user, verify_password
from app.config import settings
from app.db import get_conn
from app.tpl import templates

router = APIRouter()

USERNAME_MIN, USERNAME_MAX = 2, 20
PASSWORD_MIN = 6

# 按 IP 的认证限流：每次尝试（无论成败）都计数，超限直接 429（不执行 PBKDF2，
# 兼防 CPU 耗尽型 DoS）。阈值与 dream_quota 同思路，跨 worker 由 SQLite 共享。
_LOGIN_LIMIT, _LOGIN_WINDOW = 5, 300        # 登录：5 分钟内最多 5 次
_REGISTER_LIMIT, _REGISTER_WINDOW = 3, 600  # 注册：10 分钟内最多 3 次

# 时序侧信道缓解：用户不存在时对该固定 dummy 哈希做校验，
# 使「用户不存在」与「密码错误」两条路径耗时一致，防止用户名枚举。
_DUMMY_HASH = hash_password("timing-equalization-dummy")


def _client_ip(request: Request) -> str:
    # uvicorn 默认处理来自 127.0.0.1 的 X-Forwarded-For（nginx 同机反代场景可用）
    return request.client.host if request.client else "unknown"


def _auth_allowed(action: str, ip: str, limit: int, window: int) -> bool:
    """返回是否允许本次认证尝试，并记录本次时间（仿 dream._dream_allowed）。"""
    now = time.time()
    cutoff = now - window
    with get_conn() as conn:
        conn.execute("DELETE FROM auth_quota WHERE ts < ?", (cutoff,))
        recent = conn.execute(
            "SELECT COUNT(*) AS c FROM auth_quota WHERE action = ? AND ip = ? AND ts >= ?",
            (action, ip, cutoff),
        ).fetchone()["c"]
        if recent >= limit:
            return False
        conn.execute(
            "INSERT INTO auth_quota (action, ip, ts) VALUES (?, ?, ?)",
            (action, ip, now),
        )
    return True


@router.get("/register")
async def register_page(request: Request):
    return templates.TemplateResponse(
        request,
        "auth.html",
        {"request": request, "mode": "register", "llm_ready": settings.llm_ready},
    )


@router.post("/register")
async def register_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
):
    username = username.strip()
    password = password  # 不 strip：允许但通常无前后空格

    # 限流置于最前：超限直接 429，跳过后续校验与 PBKDF2 哈希（防批量注册 + 省 CPU）
    if not _auth_allowed("register", _client_ip(request), _REGISTER_LIMIT, _REGISTER_WINDOW):
        return templates.TemplateResponse(
            request,
            "auth.html",
            {
                "request": request,
                "mode": "register",
                "error": "尝试次数过多，请 10 分钟后再试",
                "prefill": username,
                "llm_ready": settings.llm_ready,
            },
            status_code=429,
        )

    error = None
    if not (USERNAME_MIN <= len(username) <= USERNAME_MAX):
        error = f"用户名长度需在 {USERNAME_MIN}-{USERNAME_MAX} 之间"
    elif len(password) < PASSWORD_MIN:
        error = f"密码至少 {PASSWORD_MIN} 位"

    if error is None:
        try:
            with get_conn() as conn:
                cur = conn.execute(
                    "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                    (username, hash_password(password)),
                )
                uid = cur.lastrowid
            set_session_user(request, uid, username)
            return RedirectResponse(url="/", status_code=303)
        except sqlite3.IntegrityError:
            error = "该用户名已被占用"

    return templates.TemplateResponse(
        request,
        "auth.html",
        {
            "request": request,
            "mode": "register",
            "error": error,
            "prefill": username,
            "llm_ready": settings.llm_ready,
        },
        status_code=400,
    )


@router.get("/login")
async def login_page(request: Request):
    return templates.TemplateResponse(
        request,
        "auth.html",
        {"request": request, "mode": "login", "llm_ready": settings.llm_ready},
    )


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
):
    username = username.strip()
    error = None

    # 限流置于最前：超限直接 429，不执行 PBKDF2（防爆破/撞库，兼防 CPU 耗尽）
    if not _auth_allowed("login", _client_ip(request), _LOGIN_LIMIT, _LOGIN_WINDOW):
        return templates.TemplateResponse(
            request,
            "auth.html",
            {
                "request": request,
                "mode": "login",
                "error": "尝试次数过多，请 5 分钟后再试",
                "prefill": username,
                "llm_ready": settings.llm_ready,
            },
            status_code=429,
        )

    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, username, password_hash, sign FROM users WHERE username = ?",
            (username,),
        ).fetchone()

    # 用户不存在时对 dummy 哈希做一次等价校验，统一两条路径耗时（防用户名枚举）
    password_ok = verify_password(password, row["password_hash"] if row else _DUMMY_HASH)
    if row is None or not password_ok:
        error = "用户名或密码不正确"
    else:
        set_session_user(request, row["id"], row["username"], row["sign"] or "")
        return RedirectResponse(url="/", status_code=303)

    return templates.TemplateResponse(
        request,
        "auth.html",
        {
            "request": request,
            "mode": "login",
            "error": error,
            "prefill": username,
            "llm_ready": settings.llm_ready,
        },
        status_code=400,
    )


@router.post("/logout")
async def logout(request: Request):
    clear_session_user(request)
    return RedirectResponse(url="/login", status_code=303)
