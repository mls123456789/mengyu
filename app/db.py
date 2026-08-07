"""SQLite 数据访问层。

使用标准库 sqlite3，按需建表、提供连接。schema：
- users(id, username UNIQUE, password_hash, sign, created_at)
- dreams(id, user_id, title, content, interpretation, mood, created_at)
- journals(id, user_id, mood, content, ai_response, created_at)
- horoscopes(id, user_id, sign, period, period_key, content, created_at)  -- 多时段运势缓存
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator

from app.config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    sign          TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS dreams (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL,
    title          TEXT NOT NULL DEFAULT '',
    content        TEXT NOT NULL,
    interpretation TEXT NOT NULL DEFAULT '',
    tags           TEXT NOT NULL DEFAULT '',   -- 逗号分隔的情绪/意象标签（LLM 从梦境提取）
    mood           TEXT NOT NULL DEFAULT '',   -- 旧字段，保留不破坏旧数据；新逻辑读写 tags
    created_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS journals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    mood        TEXT NOT NULL DEFAULT '',
    content     TEXT NOT NULL,
    ai_response TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS horoscopes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    sign       TEXT NOT NULL,
    period     TEXT NOT NULL DEFAULT 'today',
    period_key TEXT NOT NULL,
    content    TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (sign, period, period_key)
);

-- 「换签重生成」限流记录：跨 worker 共享，用 wall-clock 时间窗口计数。
CREATE TABLE IF NOT EXISTS force_history (
    user_id INTEGER NOT NULL,
    sign    TEXT NOT NULL,
    period  TEXT NOT NULL,
    ts      REAL NOT NULL                   -- time.time()，秒
);
CREATE INDEX IF NOT EXISTS idx_force_history_key ON force_history(user_id, sign, period, ts);

-- 「解梦提交」限流记录：跨 worker 共享，每用户在时间窗内最多解梦 N 次。
CREATE TABLE IF NOT EXISTS dream_quota (
    user_id INTEGER NOT NULL,
    ts      REAL NOT NULL                   -- time.time()，秒
);
CREATE INDEX IF NOT EXISTS idx_dream_quota ON dream_quota(user_id, ts);

-- 「情绪日记提交」限流记录：同上，每用户在时间窗内最多回应 N 次。
CREATE TABLE IF NOT EXISTS journal_quota (
    user_id INTEGER NOT NULL,
    ts      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_journal_quota ON journal_quota(user_id, ts);

-- 「登录/注册」限流记录：按 IP 计数，防爆破/撞库/批量注册。
CREATE TABLE IF NOT EXISTS auth_quota (
    action TEXT NOT NULL,                -- 'login' / 'register'
    ip     TEXT NOT NULL,
    ts     REAL NOT NULL                 -- time.time()，秒
);
CREATE INDEX IF NOT EXISTS idx_auth_quota ON auth_quota(action, ip, ts);
"""


def _ensure_dir() -> None:
    db_dir = os.path.dirname(settings.DB_PATH)
    if db_dir and not os.path.isdir(db_dir):
        os.makedirs(db_dir, exist_ok=True)


def _migrate(conn: sqlite3.Connection) -> None:
    """对旧库做兼容性迁移（已有库补列）。"""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
    if "sign" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN sign TEXT NOT NULL DEFAULT ''")

    # dreams 加 tags 列（情绪/意象标签）；旧库幂等补列，mood 旧字段保留不删
    dcols = {row["name"] for row in conn.execute("PRAGMA table_info(dreams)")}
    if "tags" not in dcols:
        conn.execute("ALTER TABLE dreams ADD COLUMN tags TEXT NOT NULL DEFAULT ''")

    # horoscopes 改为「按星座全局缓存」(sign, period, period_key)，同一星座同一时段
    # 对所有用户结果一致。旧 schema 含 user_id，检测到即 drop+recreate（缓存可丢弃）。
    hcols = {row["name"] for row in conn.execute("PRAGMA table_info(horoscopes)")}
    if "user_id" in hcols or (hcols and "updated_at" not in hcols):
        conn.execute("DROP TABLE IF EXISTS horoscopes")
        conn.execute(
            """
            CREATE TABLE horoscopes (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                sign       TEXT NOT NULL,
                period     TEXT NOT NULL DEFAULT 'today',
                period_key TEXT NOT NULL,
                content    TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                UNIQUE (sign, period, period_key)
            )
            """
        )


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """获取一个连接（带行工厂）。提交/回滚由调用方负责；出错自动回滚。"""
    conn = sqlite3.connect(settings.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    # 写锁竞争时最多等待 5s（而非立刻抛 "database is locked"）。
    # 配合 WAL：读不阻塞写、写不阻塞读，多 worker 下并发更稳。
    conn.execute("PRAGMA busy_timeout = 5000;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """启动时调用：确保目录 + 开 WAL + 建表（幂等）+ 旧库迁移。"""
    _ensure_dir()  # 仅启动期确保目录存在；不再在每次 get_conn 里重复 isdir 检查
    with get_conn() as conn:
        # WAL 持久写入数据库文件头（只需设一次）。默认 rollback journal 会
        # 整库加写锁，SSE 流式落库 + 多 worker 并发时会阻塞所有读。
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.executescript(_SCHEMA)
        _migrate(conn)
