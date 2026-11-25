"""归因查询：按问题/模型切窗口，并读出窗口内的引用行。

为什么单独一层，而不是继续往 ``sqlite_repository.window_counts`` 上加参数：
那个函数是看板与报告的口径入口，改动会影响 19 项黄金值；
归因是「文章 → 目标问题 → 发布前后」的另一条读法，参数组合更多（问题、模型、窗口、对照），
放在这里可以把 SQL 写清楚，也不让看板的查询多出四个可选分支。

窗口计数与 ``sqlite_repository.window_counts`` 保持同一套语义
（有效答案进分母、own 提及进分子、按本地自然日切），只是多了两个可选过滤条件。
"""

from __future__ import annotations

import sqlite3

from geo.repositories.sqlutil import day_of


def window_counts(
    conn: sqlite3.Connection,
    tracker_id: str,
    start: str,
    end: str,
    *,
    question_id: str | None = None,
    model_key: str | None = None,
) -> dict:
    """窗口内的有效样本数与提及数，可按问题/模型收窄。"""
    where = [
        "a.tracker_id = ?",
        "a.is_valid = 1",
        f"{day_of('a.collected_at')} BETWEEN ? AND ?",
    ]
    params: list[object] = [tracker_id, start, end]
    if question_id:
        where.append("a.question_id = ?")
        params.append(question_id)
    if model_key:
        where.append("a.model_key = ?")
        params.append(model_key)

    row = conn.execute(
        f"""
        SELECT COUNT(*) AS valid_total,
               SUM(CASE WHEN m.id IS NULL THEN 0 ELSE 1 END) AS mention_total
        FROM answer a
        LEFT JOIN mention m ON m.answer_id = a.id AND m.subject_type = 'own'
        WHERE {" AND ".join(where)}
        """,
        params,
    ).fetchone()
    return {
        "valid_total": int(row["valid_total"] or 0),
        "mention_total": int(row["mention_total"] or 0),
    }


def models_in_window(
    conn: sqlite3.Connection,
    tracker_id: str,
    start: str,
    end: str,
    *,
    question_id: str | None = None,
) -> list[str]:
    """窗口内出现过的模型，按名字排序。用于「按模型拆」时枚举分母。"""
    where = [
        "tracker_id = ?",
        "is_valid = 1",
        f"{day_of('collected_at')} BETWEEN ? AND ?",
    ]
    params: list[object] = [tracker_id, start, end]
    if question_id:
        where.append("question_id = ?")
        params.append(question_id)
    rows = conn.execute(
        f"SELECT DISTINCT model_key FROM answer WHERE {' AND '.join(where)} ORDER BY model_key",
        params,
    ).fetchall()
    return [str(row["model_key"]) for row in rows]


def citation_rows(
    conn: sqlite3.Connection,
    tracker_id: str,
    start: str,
    end: str,
    *,
    question_id: str | None = None,
) -> list[dict]:
    """窗口内有效答案的引用明细，带答案的模型/问题/时间，供 URL 匹配用。

    只读 ``citation`` 已有字段：``url`` 是平台原始链接，匹配时再归一化；
    落库的 ``url_hash`` 也是原始链接的 hash，两边不混。
    """
    where = [
        "a.tracker_id = ?",
        "a.is_valid = 1",
        f"{day_of('a.collected_at')} BETWEEN ? AND ?",
    ]
    params: list[object] = [tracker_id, start, end]
    if question_id:
        where.append("a.question_id = ?")
        params.append(question_id)

    rows = conn.execute(
        f"""
        SELECT c.id AS citation_id, c.url, c.media_name, c.domain, c.media_type,
               c.cite_type, c.position,
               a.id AS answer_id, a.model_key, a.question_id, a.collected_at
        FROM citation c
        JOIN answer a ON a.id = c.answer_id
        WHERE {" AND ".join(where)}
        ORDER BY a.collected_at, c.position, c.id
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def question_text(conn: sqlite3.Connection, question_id: str | None) -> str | None:
    if not question_id:
        return None
    row = conn.execute("SELECT text FROM question WHERE id = ?", (question_id,)).fetchone()
    return str(row["text"]) if row else None
