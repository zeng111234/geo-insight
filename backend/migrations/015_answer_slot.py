"""D3:把「一条答案」变成「某天某个槽位的那一条样本」。

加两列 + 一个唯一索引:

- ``sample_day``：本地自然日（``YYYY-MM-DD``）。用采集时刻算，不用 UTC。
- ``sample_idx``：当天该(问题, 模型, 渠道)的第几次，从 1 开始。
- 唯一索引 ``ux_answer_slot``：同一槽位只能有一条。

## 为什么用唯一索引而不是只靠代码判断

幂等是"重试不出错"的硬要求，而代码判断在两种情况下会失效：

1. **并发**：两个采集循环同时看到"今天还差 2 条"，各自算 idx=2，代码判重都在
   对方写入之前完成，于是都通过。唯一索引是数据库层面的最后一道闸。
2. **重试**：网络超时后重跑，如果顺序是"先算 idx 再采集"，两次会算到同一个 idx；
   没有唯一约束就是两条一样的样本，指标里多算一次提及。

有约束之后，重复写入直接抛 IntegrityError，转成 DUPLICATE 返回，
调用方看到的结果和"内容重复"完全一样，不需要区分。

## 为什么是部分索引（WHERE sample_day IS NOT NULL）

历史数据（种子、导入）根本不知道当时是第几次采样，两列只能是 NULL。
如果建普通唯一索引，NULL 之间在 SQLite 里互不相等，其实也不会冲突，
但**意图不明确**：后来的人会以为"NULL 也算一个槽位"。部分索引把
"只有真正分配过槽位的行才受约束"写进 schema，比写在注释里可靠。

## 为什么不回填历史数据

回填只能靠猜：种子数据没有采集时刻的先后信息，按 id 或 collected_at 排序
编一个"第几次"出来，等于给以后的分析埋一个假的维度。
留 NULL 表示"不知道"，与 ``search_called`` 的处理一致。
"""

from __future__ import annotations

import sqlite3


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def apply(conn: sqlite3.Connection) -> None:
    cols = _columns(conn, "answer")
    if "sample_day" not in cols:
        conn.execute("ALTER TABLE answer ADD COLUMN sample_day TEXT")
    if "sample_idx" not in cols:
        conn.execute("ALTER TABLE answer ADD COLUMN sample_idx INTEGER")

    conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS ux_answer_slot
           ON answer(tracker_id, question_id, model_key, channel, sample_day, sample_idx)
           WHERE sample_day IS NOT NULL AND sample_idx IS NOT NULL"""
    )
