"""数据库连接与执行(多用户配额与资费存储)。

设计见 docs/superpowers/specs/2026-08-11-quota-billing-stats-design.md §2。

后端:
- MySQL(生产):`.env MYSQL_URL`(如 mysql://mcp:***@deploy-mysql-1:3306/agentstore)
- SQLite(测试/本地):`DB_BACKEND=sqlite`,DB_SQLITE_PATH 指定文件(默认 :memory:)
  —— SQL 用简单 CRUD,兼容两种后端。

事务操作在同一连接上执行;MySQL 不可用 → 抛 RuntimeError(调用方转 503)。
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any, Callable

from common import config

logger = logging.getLogger(__name__)


def _sqlite_conn():
    path = config.get_env("DB_SQLITE_PATH", ":memory:")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _connect():
    if config.get_env("DB_BACKEND", "mysql") == "sqlite":
        return _sqlite_conn()
    import pymysql
    from pymysql.cursors import DictCursor

    url = config.get_env("MYSQL_URL")
    if not url:
        raise RuntimeError("MYSQL_URL 未配置(配额/资费功能需要 MySQL)")
    try:
        from urllib.parse import unquote, urlsplit

        u = urlsplit(url)
        return pymysql.connect(
            host=u.hostname or "127.0.0.1",
            port=u.port or 3306,
            user=u.username or "",
            password=unquote(u.password or ""),
            database=u.path.lstrip("/"),
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=False,
        )
    except pymysql.MySQLError as exc:
        raise RuntimeError(f"MySQL 连接失败: {exc}") from exc


def _is_sqlite() -> bool:
    return config.get_env("DB_BACKEND", "mysql") == "sqlite"


def _sql(sql: str) -> str:
    """SQLite 适配:占位符 %s → ?;去掉 FOR UPDATE;NOW() → CURRENT_TIMESTAMP。"""
    if not _is_sqlite():
        return sql
    return (
        sql.replace("%s", "?")
        .replace(" FOR UPDATE", "")
        .replace("NOW()", "CURRENT_TIMESTAMP")
    )


def query(sql: str, params: tuple | None = None) -> list[dict]:
    """只读查询,返回 dict 列表。"""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(_sql(sql), params or ())
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def execute(sql: str, params: tuple | None = None) -> int:
    """单条写操作(自动提交),返回影响行数。"""
    conn = _connect()
    try:
        cur = conn.cursor()
        n = cur.execute(_sql(sql), params or ())
        conn.commit()
        return n
    except Exception as exc:
        conn.rollback()
        raise RuntimeError(f"数据库执行失败: {exc}") from exc
    finally:
        conn.close()


def transaction(fn: Callable):
    """事务包装:同一连接上执行 fn(conn, cursor),成功 commit,失败 rollback。

    事务内 cursor.execute 的 SQL 自动做占位符转换(%s → ?)。
    _exec 统一返回值:SELECT 返回 dict 列表,其他返回影响行数(兼容 pymysql/SQLite)。
    """

    def _wrapper(*args, **kwargs):
        conn = _connect()
        try:
            cur = conn.cursor()

            def _exec(sql: str, params: tuple | None = None):
                cur.execute(_sql(sql), params or ())
                if _is_sqlite():
                    if cur.description:  # SELECT
                        return [dict(r) for r in cur.fetchall()]
                    return cur.rowcount
                # pymysql:SELECT 返回行数,但这里用 fetch 统一
                if cur.description:
                    return cur.fetchall()
                return cur.rowcount

            result = fn(cur, _exec, *args, **kwargs)
            conn.commit()
            return result
        except Exception as exc:
            conn.rollback()
            raise RuntimeError(f"数据库事务失败: {exc}") from exc
        finally:
            conn.close()

    return _wrapper


def init_tables() -> None:
    """建表(幂等)。测试/本地 SQLite 用;生产 MySQL 用 deploy/init_tables.sql。"""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(_sql("""
            CREATE TABLE IF NOT EXISTS api_keys (
              apikey      VARCHAR(128) PRIMARY KEY,
              role        VARCHAR(10) NOT NULL DEFAULT 'normal',
              status      VARCHAR(10) NOT NULL DEFAULT 'active',
              free_quota  INT NOT NULL DEFAULT 10,
              paid_quota  INT NOT NULL DEFAULT 0,
              free_used   INT NOT NULL DEFAULT 0,
              paid_used   INT NOT NULL DEFAULT 0,
              created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
        cur.execute(_sql("""
            CREATE TABLE IF NOT EXISTS billing_records (
              id          INTEGER PRIMARY KEY AUTOINCREMENT,
              apikey      VARCHAR(128) NOT NULL,
              group_id    VARCHAR(64) NOT NULL UNIQUE,
              status      VARCHAR(10) NOT NULL DEFAULT 'pending',
              quota_type  VARCHAR(10) NULL,
              created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
              committed_at DATETIME NULL
            )
        """))
        conn.commit()
    finally:
        conn.close()
