"""个人中心路由：展示用户信息、星座偏好、解梦/日记统计、注销账号。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app.auth import CurrentUser, clear_session_user, require_user
from app.config import settings
from app.db import get_conn
from app.services.horoscope import SIGN_NAMES
from app.tpl import templates

router = APIRouter(prefix="/me")


@router.get("")
def me_page(request: Request, user: CurrentUser = Depends(require_user)):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT username, sign, created_at FROM users WHERE id = ?", (user.id,)
        ).fetchone()
        dreams = conn.execute(
            "SELECT COUNT(*) AS c FROM dreams WHERE user_id = ?", (user.id,)
        ).fetchone()["c"]
        journals = conn.execute(
            "SELECT COUNT(*) AS c FROM journals WHERE user_id = ?", (user.id,)
        ).fetchone()["c"]
    return templates.TemplateResponse(
        request,
        "me.html",
        {
            "request": request,
            "user": user,
            "profile": row,
            "sign_names": SIGN_NAMES,
            "stats": {"dreams": dreams, "journals": journals},
            "llm_ready": settings.llm_ready,
        },
    )


@router.post("/sign")
def update_sign(
    request: Request,
    sign: str = Form(""),
    user: CurrentUser = Depends(require_user),
):
    """保存默认星座偏好（与星座运势页共用 users.sign）。"""
    sign = sign.strip()
    if sign in SIGN_NAMES:
        with get_conn() as conn:
            conn.execute("UPDATE users SET sign = ? WHERE id = ?", (sign, user.id))
        request.session["sign"] = sign
    return RedirectResponse(url="/me", status_code=303)


@router.post("/delete-account")
def delete_account(request: Request, user: CurrentUser = Depends(require_user)):
    """注销账号（个保法第 47 条删除权）：级联删除用户及其全部数据。

    dreams/journals 经 ON DELETE CASCADE 随用户删除；quota 类表无外键，手动清理。
    前端需二次确认弹窗（mengyuConfirm）后才提交。
    """
    with get_conn() as conn:
        conn.execute("DELETE FROM dream_quota WHERE user_id = ?", (user.id,))
        conn.execute("DELETE FROM journal_quota WHERE user_id = ?", (user.id,))
        conn.execute("DELETE FROM force_history WHERE user_id = ?", (user.id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user.id,))
    clear_session_user(request)
    return RedirectResponse(url="/", status_code=303)
