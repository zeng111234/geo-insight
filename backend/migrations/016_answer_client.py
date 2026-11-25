"""E2:给 answer 加「端」维度 —— client 列。

决策 009 选的是「加列」而不是把 model_key 拆成 `doubao_web`：拆键会让所有
按 model_key 聚合的既有口径（提及率、按模型拆、导出）同时失效，而且老数据
没法无损迁移。加列只多一个分组维度，老口径不传 client 时完全不变。

老数据一律回填 `web`。这不是猜——采集器只有官方 API 通道，历史上不可能
有手机端的样本。新增采集器若走手机端，由采集流程显式写 client='mobile'。

DEFAULT 常量让 SQLite 的 ADD COLUMN 自动回填已有行，不需要 UPDATE 全表。
"""

from __future__ import annotations

import sqlite3


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def apply(conn: sqlite3.Connection) -> None:
    if "client" not in _columns(conn, "answer"):
        conn.execute(
            "ALTER TABLE answer ADD COLUMN client TEXT NOT NULL DEFAULT 'web'"
        )
