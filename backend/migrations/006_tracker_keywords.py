"""给 tracker 增加核心词（JSON 数组字符串）。

用途：区分"答案在聊这行但没提我们"和"答非所问"。判断逻辑在 domain/relevance.py，
这里只负责存。

加列而不是建表：核心词是监控对象的属性，一个对象一组，不跨对象复用；
建关联表要多一次 JOIN 和一套增删改接口，收益配不上成本。
"""

from __future__ import annotations

import sqlite3


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def apply(conn: sqlite3.Connection) -> None:
    if "keywords" not in _columns(conn, "tracker"):
        # 默认空数组：老数据不需要回填，语义就是"未配置核心词"
        conn.execute("ALTER TABLE tracker ADD COLUMN keywords TEXT NOT NULL DEFAULT '[]'")
