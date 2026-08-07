"""梦语 (mengyu) — AI 解梦 + 情绪日记 web 应用入口。

启动：python -m uvicorn app.main:app --reload
"""
from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.db import get_conn, init_db
from app.routers import auth, dream, horoscope, journal, me, pages

# 应用日志级别（app.* logger）；uvicorn 自身日志由其命令行单独控制
logging.getLogger("app").setLevel(settings.LOG_LEVEL)


def _setup_file_logging() -> None:
    """把 app.* 日志写入轮转文件（LOG_FILE 配置后生效；留空则仅控制台）。

    UTF-8 显式编码，避免 GBK 控制台/重定向时中文乱码；10MB×5 份轮转。
    注：多 worker 各自持有 handler，极端情况下轮转瞬间可能竞争——当前规模可接受。
    """
    if not settings.LOG_FILE:
        return
    Path(settings.LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        settings.LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logging.getLogger("app").addHandler(handler)


_setup_file_logging()

STATIC_DIR = Path(__file__).resolve().parent / "static"


# 通用安全响应头（所有响应都注入）；静态资源额外加缓存头。
_SAFE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",                       # 不允许被嵌入 iframe（防点击劫持）
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}


def _csp(nonce: str) -> str:
    """严格 CSP：脚本仅本站 + 当次 nonce（内联 FOUC/星座数据脚本挂它）；
    样式仅本站（圆点颜色改用 CSSOM 设值，无内联 style 属性）；其余资源均限 self。"""
    return (
        "default-src 'self'; "
        f"script-src 'self' 'nonce-{nonce}'; "
        "style-src 'self'; "
        "img-src 'self'; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'; "
        "object-src 'none'"
    )


class HeadersMiddleware(BaseHTTPMiddleware):
    """统一注入响应头：

    - 每请求生成 CSP nonce（模板内联脚本挂载用），响应阶段写入 CSP。
    - 安全头（所有响应）：见 _SAFE_HEADERS。
    - 静态资源缓存（/static/）：字体（woff2）一年 immutable，其余 1 小时。
    生产若由 nginx 直发 /static，缓存以 nginx 为准；此处覆盖 dev 与未走 nginx 的场景。
    """

    async def dispatch(self, request, call_next):
        # 每请求一次性 nonce：模板内联脚本挂它，CSP 放行；不可复用/预测
        request.state.csp_nonce = secrets.token_hex(16)
        response = await call_next(request)
        for key, value in _SAFE_HEADERS.items():
            if key not in response.headers:
                response.headers[key] = value
        # CSP：默认强制；CSP_MODE=report-only 时仅上报不拦截（先试跑用）
        response.headers[
            "Content-Security-Policy-Report-Only"
            if settings.CSP_MODE == "report-only"
            else "Content-Security-Policy"
        ] = _csp(request.state.csp_nonce)
        path = request.url.path
        if path.startswith("/static/"):
            if path.startswith("/static/fonts/") or path.endswith(".woff2"):
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            else:
                response.headers["Cache-Control"] = "public, max-age=3600"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时建表（幂等）
    init_db()
    yield


app = FastAPI(title="梦语 mengyu", lifespan=lifespan)

# 响应头中间件（安全头 + 静态缓存）；放在静态挂载之前以包裹 StaticFiles
app.add_middleware(HeadersMiddleware)

# 会话（签名 cookie）；生产(HTTPS)下 cookie 标 Secure，开发(HTTP)下不标
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    same_site="lax",
    https_only=not settings.is_dev,
)

# 静态资源
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# 路由
app.include_router(pages.router)
app.include_router(auth.router)
app.include_router(dream.router)
app.include_router(journal.router)
app.include_router(horoscope.router)
app.include_router(me.router)


@app.exception_handler(401)
async def _unauthorized_handler(request: Request, exc: HTTPException):
    """未登录：浏览器导航跳登录页；接口/fetch 调用返回 JSON。"""
    accept = request.headers.get("accept", "")
    is_browser_nav = "text/html" in accept and "/api/" not in request.url.path
    if is_browser_nav:
        return RedirectResponse(url="/login", status_code=303)
    return JSONResponse({"detail": exc.detail or "请先登录"}, status_code=401)


@app.get("/healthz")
def healthz():
    """探活端点（nginx upstream / 监控用）：顺带验证 SQLite 可读。"""
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1").fetchone()
    except Exception:  # noqa: BLE001 - 探活失败统一 503，不外泄细节
        return JSONResponse({"status": "db_unavailable"}, status_code=503)
    return {"status": "ok"}
