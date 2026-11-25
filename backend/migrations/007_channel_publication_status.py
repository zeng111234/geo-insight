"""渠道与发布记录的状态词表扩展(发布通道真实化,决策 011)。

channel_account.status 从 3 态扩到 6 态:connected / disconnected / expired
/ connecting(登录中) / needs_config(百家号待配密钥) / not_implemented(无发布器),
并新增 last_check_note 列(检测附注,如「已登录但未实名,发文章被锁」)。
publication.status 从 3 态扩到 7 态:queued / running / published / draft_saved
/ blocked / failed / unknown(结果需人工确认,如实不猜)。

SQLite 改不了已有 CHECK,只能重建表(建新表 → 搬数据 → 删旧表 → 改名),
套路与 004 相同:可重跑(看 sqlite_master 词表是否已含新词)、重建期间关
foreign_keys(publication.channel_id 指向 channel_account)、失败可恢复
(先 DROP *_new)。旧库里已有的旧状态值都在新词表里,数据原样搬。
"""

from __future__ import annotations

import sqlite3

CHANNEL_TABLE_SQL = """
CREATE TABLE channel_account_new (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    platform_key TEXT NOT NULL,
    platform_name TEXT NOT NULL,
    account_name TEXT,
    status TEXT NOT NULL DEFAULT 'disconnected'
        CHECK (status IN ('connected', 'disconnected', 'expired',
                          'connecting', 'needs_config', 'not_implemented')),
    login_mode TEXT,
    last_checked_at TEXT,
    last_check_note TEXT
)
"""

PUBLICATION_TABLE_SQL = """
CREATE TABLE publication_new (
    id TEXT PRIMARY KEY,
    article_id TEXT NOT NULL REFERENCES article(id),
    channel_id TEXT NOT NULL REFERENCES channel_account(id),
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'published', 'draft_saved',
                          'blocked', 'failed', 'unknown')),
    published_url TEXT,
    published_at TEXT,
    failure_reason TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (article_id, channel_id)
)
"""

PUB_INDEX_SQL = "CREATE INDEX IF NOT EXISTS idx_pub_article ON publication(article_id)"


def _table_sql(conn: sqlite3.Connection, name: str) -> str:
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone()
    return (row[0] or "") if row else ""


def _rebuild(conn: sqlite3.Connection, old: str, new: str, create_sql: str, copy_sql: str | None,
             after: tuple[str, ...] = ()) -> None:
    conn.execute(f"DROP TABLE IF EXISTS {new}")
    conn.execute(create_sql)
    if copy_sql:
        conn.execute(copy_sql)
        conn.execute(f"DROP TABLE {old}")
    conn.execute(f"ALTER TABLE {new} RENAME TO {old}")
    for stmt in after:
        conn.execute(stmt)


def apply(conn: sqlite3.Connection) -> None:
    channel_done = "not_implemented" in _table_sql(conn, "channel_account")
    pub_done = "draft_saved" in _table_sql(conn, "publication")
    if channel_done and pub_done:
        return

    # PRAGMA foreign_keys 在事务里是空操作,先把隐式事务收掉
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        if not channel_done:
            # 新增列在末尾,老数据显式列出来补 NULL,不用 SELECT *
            _rebuild(
                conn, "channel_account", "channel_account_new", CHANNEL_TABLE_SQL,
                "INSERT INTO channel_account_new "
                "(id, tenant_id, platform_key, platform_name, account_name, status, "
                " login_mode, last_checked_at, last_check_note) "
                "SELECT id, tenant_id, platform_key, platform_name, account_name, status, "
                "       login_mode, last_checked_at, NULL FROM channel_account",
            )
        if not pub_done:
            # 列顺序与 001 完全一致,SELECT * 安全
            _rebuild(
                conn, "publication", "publication_new", PUBLICATION_TABLE_SQL,
                "INSERT INTO publication_new SELECT * FROM publication",
                after=(PUB_INDEX_SQL,),
            )
        conn.commit()
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
