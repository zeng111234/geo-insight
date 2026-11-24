"""H2-6:监控对象绑定问题集 + 核心词下放到问题集(003 US2/FR-003)。

三个变更:

1. ``tracker.active_question_set_id``——当前生效的问题集。切换它,指标口径、
   问题列表、核心词与跑题判定整体跟随(查询层的统一谓词见 sqlite_repository)。
2. ``question_set.keywords``——核心词从 tracker 挪到问题集:不同集的核心词
   可以不同,切换时判定随之切换(spec Edge 的硬要求)。老数据回填:tracker
   的 keywords 复制给它名下每个集;tracker.keywords 列保留不删(回滚安全),
   但不再被读。
3. active 回填:每个 tracker 的 active 指向它的第一个集(按 created_at)。
   单集的库(现状)语义不变——黄金窗口的 9 样本 / 22.22% 不动。
"""

from __future__ import annotations

import sqlite3


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def apply(conn: sqlite3.Connection) -> None:
    cols = _columns(conn, "tracker")
    if "active_question_set_id" not in cols:
        conn.execute(
            "ALTER TABLE tracker ADD COLUMN active_question_set_id TEXT REFERENCES question_set(id)"
        )
    if "keywords" not in _columns(conn, "question_set"):
        conn.execute("ALTER TABLE question_set ADD COLUMN keywords TEXT NOT NULL DEFAULT '[]'")

    # 核心词回填:tracker → 名下每个集(006 的存量语义平移)
    conn.execute(
        """UPDATE question_set
           SET keywords = (SELECT t.keywords FROM tracker t WHERE t.id = question_set.tracker_id)"""
    )
    # active 回填:第一个集(单集库语义不变;多集库的旧查询本来就是跨集聚合,
    # 指到第一个集后口径收窄,这是 H2-6 的本意)
    conn.execute(
        """UPDATE tracker SET active_question_set_id = (
               SELECT qs.id FROM question_set qs
               WHERE qs.tracker_id = tracker.id ORDER BY qs.created_at, qs.id LIMIT 1)"""
    )
    conn.commit()
