"""D2:answer 记录「这次到底有没有联网搜索」。

两列，都可空:

- ``search_called``：三态。``1`` 搜了、``0`` 明确没搜、``NULL`` **不知道**。
  为什么不用 ``NOT NULL DEFAULT 0``：导入通道的答案、历史种子数据，
  我们根本不知道平台当次有没有联网。默认 0 等于把「不知道」写成「没搜」，
  之后算「搜索触发率」时这些行会被当成"没触发"，把比率算低。
  宁可留空，让统计时显式排除。
- ``search_count``：平台自己报的搜索次数。深度思考模式一次问答会搜多轮，
  次数与"有没有搜"是两条信息。

「没搜索」与「搜了没引用」的区分靠这两列加 ``citation`` 行数:

    search_called = 0                    -> 没搜索（要改问法）
    search_called = 1 且 citation 为空    -> 搜了但没被引用（正常，不是失败）
    search_called = 1 且 citation 非空    -> 搜了也引了

有了这三种状态，D1 的「目标问题至少 2/3 次触发搜索」才可判定；
只有一个布尔值时，这两种 0 引用的情况会混成一个数字，无从下手。
"""

from __future__ import annotations

import sqlite3


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def apply(conn: sqlite3.Connection) -> None:
    cols = _columns(conn, "answer")
    if "search_called" not in cols:
        conn.execute("ALTER TABLE answer ADD COLUMN search_called INTEGER")
    if "search_count" not in cols:
        conn.execute("ALTER TABLE answer ADD COLUMN search_count INTEGER")
