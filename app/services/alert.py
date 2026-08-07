"""最小告警通道：LLM 连续失败 N 次后经 webhook 推送文本告警（企业微信/钉钉机器人格式）。

fire-and-forget：守护线程发送，绝不阻塞或影响主流程；ALERT_WEBHOOK_URL 未配置 = 关闭。
同类告警 _COOLDOWN 秒内不重复发送；任意一次成功即重置连续失败计数。
注：计数为每 worker 内存态（多 worker 各自计数），当前规模可接受。
"""
from __future__ import annotations

import logging
import threading
import time

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_COOLDOWN = 600  # 两次告警的最小间隔（秒）

_lock = threading.Lock()
_consecutive = 0
_last_alert_ts = 0.0


def on_llm_ok() -> None:
    """一次成功的 LLM 调用：重置连续失败计数。"""
    global _consecutive
    with _lock:
        _consecutive = 0


def on_llm_error(last_exc: BaseException | None = None) -> None:
    """记录一次失败；达到阈值且过了冷却期时推送告警（非阻塞）。"""
    global _consecutive, _last_alert_ts
    url = (settings.ALERT_WEBHOOK_URL or "").strip()
    if not url:
        return
    with _lock:
        _consecutive += 1
        n = _consecutive
        now = time.time()
        if n < settings.ALERT_LLM_FAIL_THRESHOLD or now - _last_alert_ts < _COOLDOWN:
            return
        _last_alert_ts = now
    msg = (
        f"[梦语告警] LLM 调用已连续失败 {n} 次，"
        f"最近错误：{str(last_exc)[:200]}。请检查 LLM 服务/密钥/额度。"
    )
    threading.Thread(target=_post, args=(url, msg), daemon=True).start()


def _post(url: str, msg: str) -> None:
    try:
        # 企业微信 / 钉钉机器人 text 消息格式
        httpx.post(url, json={"msgtype": "text", "text": {"content": msg}}, timeout=5)
    except Exception:  # noqa: BLE001 - 告警失败绝不影响主流程
        logger.warning("告警 webhook 发送失败", exc_info=True)
