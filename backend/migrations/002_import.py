"""答案导入通道需要的结构：导入批次表 + answer 的溯源字段。

为什么是 .py 而不是 .sql：SQLite 没有 ``ADD COLUMN IF NOT EXISTS``，
而迁移必须能安全重跑（见 geo.migrate 的规则）。用 Python 先查 ``PRAGMA table_info``
再决定是否加列，语义就明确了。
"""

from __future__ import annotations

import sqlite3


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def apply(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS import_batch (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            tracker_id TEXT,
            source_name TEXT NOT NULL,
            source_format TEXT NOT NULL CHECK (source_format IN ('csv', 'jsonl')),
            status TEXT NOT NULL DEFAULT 'running'
                CHECK (status IN ('running', 'completed', 'failed')),
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
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_import_batch_tenant ON import_batch(tenant_id, started_at DESC)"
    )

    columns = _columns(conn, "answer")
    if "import_batch_id" not in columns:
        # 默认 NULL 才能通过 ALTER 加外键列，这也正好对应"种子数据没有批次"
        conn.execute("ALTER TABLE answer ADD COLUMN import_batch_id TEXT REFERENCES import_batch(id)")
    if "row_hash" not in columns:
        conn.execute("ALTER TABLE answer ADD COLUMN row_hash TEXT")

    # 部分唯一索引：只约束导进来的行，种子与历史数据（row_hash 为 NULL）不受影响。
    # 幂等的实际执行者就是这个索引——重复导入同一份文件时插入会被挡下。
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_answer_row_hash ON answer(row_hash) WHERE row_hash IS NOT NULL"
    )
