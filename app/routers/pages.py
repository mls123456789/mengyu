"""首页与静态页面路由。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from app.auth import get_current_user
from app.config import settings
from app.db import get_conn
from app.tpl import templates

router = APIRouter()


@router.get("/")
def index(request: Request):
    user = get_current_user(request)
    stats = None
    if user is not None:
        with get_conn() as conn:
            d = conn.execute(
                "SELECT COUNT(*) AS c FROM dreams WHERE user_id = ?", (user.id,)
            ).fetchone()["c"]
            j = conn.execute(
                "SELECT COUNT(*) AS c FROM journals WHERE user_id = ?", (user.id,)
            ).fetchone()["c"]
        stats = {"dreams": d, "journals": j}
    return templates.TemplateResponse(
        request,
        "index.html",
        {"request": request, "user": user, "stats": stats, "llm_ready": settings.llm_ready},
    )
