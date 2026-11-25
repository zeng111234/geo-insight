"""机会分需要的两组聚合：竞品在场程度、引用来自哪些域名。

只做聚合，不解释含义——「这个域名算不算我们能发的」属于服务层判断，
因为那份名单来自 publishers/ 里已经接入的平台，会随渠道增加而变。
"""

from __future__ import annotations

import sqlite3

from geo.repositories.sqlutil import day_of


def competitor_rows(
    conn: sqlite3.Connection, tracker_id: str, start: str, end: str
) -> dict[str, dict]:
    """每个问题里出现过竞品的答案数。

    按「答案」计数而不是按「提及」计数：一条答案提到三个竞品，对判断
    「AI 在这题上给不给品牌答案」仍然只算一次，按条数算会把权重放大三倍。
    """
    rows = conn.execute(
        f"""
        SELECT a.question_id,
               COUNT(DISTINCT a.id) AS answers,
               COUNT(m.id) AS mentions
        FROM answer a
        JOIN mention m ON m.answer_id = a.id AND m.subject_type = 'competitor'
        WHERE a.tracker_id = ? AND a.is_valid = 1
          AND {day_of('a.collected_at')} BETWEEN ? AND ?
        GROUP BY a.question_id
        """,
        (tracker_id, start, end),
    ).fetchall()
    return {
        row["question_id"]: {"answers": int(row["answers"] or 0), "mentions": int(row["mentions"] or 0)}
        for row in rows
    }


def citation_domain_rows(
    conn: sqlite3.Connection, tracker_id: str, start: str, end: str
) -> list[dict]:
    """每个问题下、按域名聚合的引用条数。"""
    rows = conn.execute(
        f"""
        SELECT a.question_id, COALESCE(c.domain, '') AS domain, COUNT(*) AS n
        FROM citation c
        JOIN answer a ON a.id = c.answer_id
        WHERE a.tracker_id = ? AND a.is_valid = 1
          AND {day_of('a.collected_at')} BETWEEN ? AND ?
        GROUP BY a.question_id, COALESCE(c.domain, '')
        """,
        (tracker_id, start, end),
    ).fetchall()
    return [
        {"question_id": r["question_id"], "domain": r["domain"], "n": int(r["n"] or 0)}
        for r in rows
    ]
