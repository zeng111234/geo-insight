"""SQL 片段：日期口径。

系统里所有时间列（`collected_at`、`attempted_at`、`scheduled_for`）存的是带时区的
ISO8601，例如 `2026-11-20T09:00:00+08:00`。

**不要用 SQLite 的 `date()`**：它会把带偏移的时间先转成 UTC 再取日期，
于是北京时间 00:00–07:59 采集的样本会被算到前一天。监测系统的"某天"应该是
业务时区的自然日，不是 UTC 日。
"""

from __future__ import annotations


def day_of(column: str) -> str:
    """返回取本地日期的 SQL 表达式。column 可以是列名，也可以是 COALESCE(...) 这类表达式。"""
    return f"substr({column}, 1, 10)"
