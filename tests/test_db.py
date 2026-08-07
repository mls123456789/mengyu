"""数据库迁移测试：_migrate 对旧 schema 的兼容性。

手工建旧表（缺 sign/tags 列、旧 horoscopes 含 user_id），调 init_db 后验证迁移结果。

运行：PYTHONIOENCODING=utf-8 python -m pytest tests/test_db.py -v
"""
import sqlite3

import pytest

from app.config import settings
from app.db import get_conn, init_db


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """隔离 DB 到临时路径，返回路径字符串。"""
    path = str(tmp_path / "migrate_test.db")
    monkeypatch.setattr(settings, "DB_PATH", path)
    return path


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """获取表的所有列名。"""
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_migrate_adds_sign_to_users(db_path):
    """旧 users 表无 sign 列 → init_db 后 sign 存在。"""
    # 手工建旧 users 表（无 sign 列）
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        )
    """)
    # 插入一条测试数据（确保迁移不丢数据）
    conn.execute("INSERT INTO users (username, password_hash) VALUES ('alice', 'hash1')")
    conn.commit()
    conn.close()

    # 运行 init_db（含 _migrate）
    init_db()

    # 验证 sign 列已添加
    with get_conn() as conn:
        cols = _table_columns(conn, "users")
        assert "sign" in cols
        # 数据未丢
        row = conn.execute("SELECT * FROM users WHERE username = 'alice'").fetchone()
        assert row is not None
        assert row["sign"] == ""  # 默认空串


def test_migrate_adds_tags_to_dreams(db_path):
    """旧 dreams 表无 tags 列 → init_db 后 tags 存在。"""
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        );
        CREATE TABLE dreams (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id        INTEGER NOT NULL,
            title          TEXT NOT NULL DEFAULT '',
            content        TEXT NOT NULL,
            interpretation TEXT NOT NULL DEFAULT '',
            mood           TEXT NOT NULL DEFAULT '',
            created_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        );
    """)
    conn.close()

    init_db()

    with get_conn() as conn:
        cols = _table_columns(conn, "dreams")
        assert "tags" in cols


def test_migrate_rebuilds_horoscopes_without_user_id(db_path):
    """旧 horoscopes 含 user_id 列 → 迁移后表被重建（user_id 消失）。"""
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE horoscopes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            sign       TEXT NOT NULL,
            period     TEXT NOT NULL DEFAULT 'today',
            period_key TEXT NOT NULL,
            content    TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        );
    """)
    # 插入旧数据（迁移会丢弃）
    conn.execute(
        "INSERT INTO horoscopes (user_id, sign, period, period_key, content) "
        "VALUES (1, '白羊座', 'today', '2026-01-01', 'old content')"
    )
    conn.commit()
    conn.close()

    init_db()

    with get_conn() as conn:
        cols = _table_columns(conn, "horoscopes")
        # user_id 已移除
        assert "user_id" not in cols
        # 新列已添加
        assert "updated_at" in cols
        assert "sign" in cols
        assert "period" in cols
        # 旧数据已被清空（DROP + RECREATE）
        count = conn.execute("SELECT COUNT(*) AS c FROM horoscopes").fetchone()["c"]
        assert count == 0


def test_init_db_idempotent(db_path):
    """init_db 连跑两次不报错，表结构一致。"""
    init_db()
    init_db()  # 第二次应幂等

    with get_conn() as conn:
        # 所有表都存在
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        for t in ("users", "dreams", "journals", "horoscopes",
                  "force_history", "dream_quota", "journal_quota", "auth_quota"):
            assert t in tables, f"table {t} missing after double init_db"

        # 列结构完整
        user_cols = _table_columns(conn, "users")
        assert "sign" in user_cols
        dream_cols = _table_columns(conn, "dreams")
        assert "tags" in dream_cols
