"""应用配置：从环境变量 / .env 读取，集中管理。

只依赖 python-dotenv + 标准库，保持依赖精简。
"""
from __future__ import annotations

import os
import warnings

from dotenv import load_dotenv

# 加载项目根目录下的 .env（run 脚本的工作目录即为项目根）
load_dotenv()


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


# 视为「开发/测试」的 APP_ENV 取值（不区分大小写）；未设置时默认视为开发模式
_DEV_ENVS = {"", "dev", "development", "test", "testing", "local"}
# 会话签名密钥的默认占位符——生产环境必须覆盖
_PLACEHOLDER_SECRET = "dev-insecure-change-me"


class Settings:
    """全局配置单例。"""

    # ----- LLM（OpenAI 兼容协议）-----
    LLM_BASE_URL: str = _get("LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
    LLM_API_KEY: str = _get("LLM_API_KEY", "")
    LLM_MODEL: str = _get("LLM_MODEL", "glm-4-plus")
    # 生成参数
    LLM_TEMPERATURE: float = float(_get("LLM_TEMPERATURE", "0.8"))
    LLM_MAX_TOKENS: int = _get_int("LLM_MAX_TOKENS", 1200)

    # ----- 数据库（SQLite）-----
    DB_PATH: str = _get("DB_PATH", "./data/mengyu.db")

    # ----- 会话签名密钥（itsdangerous）-----
    # 生产环境务必在 .env 中覆盖为一个随机长字符串。
    SECRET_KEY: str = _get("SECRET_KEY", _PLACEHOLDER_SECRET)

    # ----- 运行 -----
    HOST: str = _get("HOST", "0.0.0.0")
    PORT: int = _get_int("PORT", 8000)
    # 运行环境：dev / development / test / local 视为开发模式；其余（如 prod）视为生产
    APP_ENV: str = _get("APP_ENV", "dev")
    # 应用日志级别（app.* logger）；uvicorn 自身日志由其命令行单独控制
    LOG_LEVEL: str = _get("LOG_LEVEL", "INFO").upper()
    # app.* 日志轮转文件路径；留空 = 仅控制台（开发默认）。生产建议 data/logs/app.log
    LOG_FILE: str = _get("LOG_FILE", "")
    # CSP 模式：enforce=强制拦截（默认）/ report-only=仅上报不拦截（先试跑用）
    CSP_MODE: str = _get("CSP_MODE", "enforce").strip().lower()

    # ----- 告警（可选）-----
    # 企业微信/钉钉机器人 webhook；留空 = 关闭告警
    ALERT_WEBHOOK_URL: str = _get("ALERT_WEBHOOK_URL", "")
    # LLM 连续失败 N 次触发一次告警（两次告警之间至少间隔 10 分钟）
    ALERT_LLM_FAIL_THRESHOLD: int = _get_int("ALERT_LLM_FAIL_THRESHOLD", 3)

    # 明显的占位符，视为未配置
    _PLACEHOLDERS = {"", "sk-your-key-here", "your-api-key", "your-key-here"}

    @property
    def llm_ready(self) -> bool:
        """LLM 是否已配置好可用（有 base_url + 真实 api_key + model）。"""
        if not (self.LLM_BASE_URL and self.LLM_MODEL):
            return False
        return self.LLM_API_KEY.strip().lower() not in self._PLACEHOLDERS

    @property
    def is_dev(self) -> bool:
        """是否处于开发/测试环境（APP_ENV 属于 _DEV_ENVS）；用于 cookie Secure 等开关。"""
        return self.APP_ENV.strip().lower() in _DEV_ENVS


settings = Settings()


def _validate_security() -> None:
    """SECRET_KEY 安全加固：

    - 开发模式（APP_ENV 属于 _DEV_ENVS，默认）下使用占位符：只 warn，可继续运行
      （保证 `python -c` 自测与本地开发不受影响）；
    - 非开发模式下仍使用占位符：直接抛 RuntimeError 拒绝启动。
    """
    is_dev = settings.APP_ENV.strip().lower() in _DEV_ENVS
    if settings.SECRET_KEY == _PLACEHOLDER_SECRET:
        if is_dev:
            warnings.warn(
                "SECRET_KEY 使用默认占位符，仅限开发环境；"
                "生产部署请在 .env 设置一个随机长字符串。",
                RuntimeWarning,
                stacklevel=2,
            )
        else:
            raise RuntimeError(
                "SECRET_KEY 未安全配置：非开发环境(APP_ENV="
                f"{settings.APP_ENV!r}) 必须在环境变量或 .env 中设置 "
                "SECRET_KEY 为一个随机长字符串。"
            )


# 导入即校验：生产环境用默认密钥时直接拒启
_validate_security()
