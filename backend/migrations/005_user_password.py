"""给 app_user 加密码哈希列。

没有默认值、允许为空：老库里已有的用户没有密码，由 auth_service.ensure_admin_password
在启动或 seed 时补上，这样升级路径不会把已有账号锁死。
"""

from __future__ import annotations

import sqlite3


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def apply(conn: sqlite3.Connection) -> None:
    if "password_hash" not in _columns(conn, "app_user"):
        conn.execute("ALTER TABLE app_user ADD COLUMN password_hash TEXT")
