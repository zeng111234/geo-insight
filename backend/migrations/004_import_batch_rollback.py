"""让 import_batch.status 支持 rolled_back（回滚后要能标记，不能记成 failed）。

SQLite 不能 ALTER 已有的 CHECK 约束，只能重建表（建新表 → 搬数据 → 删旧表 → 改名）。
这也是迁移链第一次遇到"改约束"，所以要显式处理三件事：

1. **可重跑**：先看 sqlite_master 里 status 的取值是否已包含 rolled_back，包含就跳过。
2. **外键**：answer.import_batch_id 指向这张表。重建期间关掉 foreign_keys，
   搬完再打开；表名恢复后外键文本仍然指向 import_batch，不需要改 answer。
3. **失败可恢复**：中途挂掉会留下 import_batch_new，下次进来先删掉它再重建。
"""

from __future__ import annotations

import sqlite3

NEW_TABLE_SQL = """
CREATE TABLE import_batch_new (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    tracker_id TEXT,
    source_name TEXT NOT NULL,
    source_format TEXT NOT NULL CHECK (source_format IN ('csv', 'jsonl')),
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'completed', 'failed', 'rolled_back')),
    row_total INTEGER NOT NULL DEFAULT 0,
    row_inserted INTEGER NOT NULL DEFAULT 0,
    row_skipped INTEGER NOT NULL DEFAULT 0,
    row_failed INTEGER NOT NULL DEFAULT 0,
    report_uri TEXT,
    error TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
)
"""

INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_import_batch_tenant ON import_batch(tenant_id, started_at DESC)"
)


def apply(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'import_batch'"
    ).fetchone()
    definition = (row[0] or "") if row else ""
    if "rolled_back" in definition:
        return

    # PRAGMA foreign_keys 在事务里是空操作，先把隐式事务收掉
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("DROP TABLE IF EXISTS import_batch_new")
        conn.execute(NEW_TABLE_SQL)
        if row:
            # 列顺序与 002 完全一致，所以 SELECT * 是安全的
            conn.execute("INSERT INTO import_batch_new SELECT * FROM import_batch")
            conn.execute("DROP TABLE import_batch")
        conn.execute("ALTER TABLE import_batch_new RENAME TO import_batch")
        conn.execute(INDEX_SQL)
        conn.commit()
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
