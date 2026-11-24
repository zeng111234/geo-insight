"""SQLite read model for the MVP.

The SQL is intentionally close to what the ClickHouse repository will do:
filter by tenant/tracker/date/model/channel, then aggregate. Swapping the
storage only replaces this module.
"""

import sqlite3
import uuid
from typing import Any

from geo.repositories.sqlutil import day_of


def _filters(
    tracker_id: str,
    start: str,
    end: str,
    model: str | None,
    channel: str | None,
    alias: str = "a",
) -> tuple[str, list[Any]]:
    clauses = [
        f"{alias}.tracker_id = ?",
        f"{alias}.is_valid = 1",
        f"{day_of(alias + '.collected_at')} BETWEEN ? AND ?",
    ]
    params: list[Any] = [tracker_id, start, end]
    if model:
        clauses.append(f"{alias}.model_key = ?")
        params.append(model)
    if channel:
        clauses.append(f"{alias}.channel = ?")
        params.append(channel)
    return " AND ".join(clauses), params


def get_tracker(conn: sqlite3.Connection, tracker_id: str, tenant_id: str) -> dict | None:
    row = conn.execute(
        """
        SELECT t.*, b.name AS brand_name
        FROM tracker t JOIN brand b ON b.id = t.brand_id
        WHERE t.id = ? AND t.tenant_id = ?
        """,
        (tracker_id, tenant_id),
    ).fetchone()
    return dict(row) if row else None


def list_trackers(conn: sqlite3.Connection, tenant_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT t.*, b.name AS brand_name
        FROM tracker t JOIN brand b ON b.id = t.brand_id
        WHERE t.tenant_id = ?
        ORDER BY t.created_at
        """,
        (tenant_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def question_summary(conn: sqlite3.Connection, tracker_id: str) -> dict:
    row = conn.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN q.tier = 'core' THEN 1 ELSE 0 END) AS core,
               SUM(CASE WHEN q.tier = 'long_tail' THEN 1 ELSE 0 END) AS long_tail
        FROM question q JOIN question_set qs ON qs.id = q.question_set_id
        WHERE qs.tracker_id = ? AND q.status = 'active'
        """,
        (tracker_id,),
    ).fetchone()
    return {"total": row["total"] or 0, "core": row["core"] or 0, "long_tail": row["long_tail"] or 0}


def active_question_texts(conn: sqlite3.Connection, tracker_id: str, limit: int = 50) -> list[str]:
    """按创建顺序取活跃问题文本，供采集脚本遍历。"""
    rows = conn.execute(
        """
        SELECT q.text
        FROM question q JOIN question_set qs ON qs.id = q.question_set_id
        WHERE qs.tracker_id = ? AND q.status = 'active'
        ORDER BY q.created_at, q.id
        LIMIT ?
        """,
        (tracker_id, limit),
    ).fetchall()
    return [r["text"] for r in rows]


def sampling_plan_of(conn: sqlite3.Connection, tracker_id: str) -> dict | None:
    """取当前生效的采样计划。

    首版只支持一个计划：一个监控对象一份，多份时取 effective_from 最新的那份。
    真要做多计划并存，等有人真的需要再说。
    """
    row = conn.execute(
        """SELECT * FROM sampling_plan WHERE tracker_id = ?
           ORDER BY effective_from DESC, rowid DESC LIMIT 1""",
        (tracker_id,),
    ).fetchone()
    return dict(row) if row else None


def active_questions(conn: sqlite3.Connection, tracker_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT q.id, q.text, q.tier
        FROM question q JOIN question_set qs ON qs.id = q.question_set_id
        WHERE qs.tracker_id = ? AND q.status = 'active'
        ORDER BY q.tier, q.created_at, q.id
        """,
        (tracker_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def sample_counts(
    conn: sqlite3.Connection, tracker_id: str, start: str, end: str
) -> dict[tuple[str, str, str], int]:
    """(本地日期, question_id, model_key) -> 已有有效样本数。

    渠道不参与分组：人工用网页问、或脚本用接口问，都算"这天采到了"。
    """
    rows = conn.execute(
        f"""
        SELECT {day_of('collected_at')} AS day, question_id, model_key, COUNT(*) AS n
        FROM answer
        WHERE tracker_id = ? AND is_valid = 1
          AND {day_of('collected_at')} BETWEEN ? AND ?
        GROUP BY day, question_id, model_key
        """,
        (tracker_id, start, end),
    ).fetchall()
    return {(r["day"], r["question_id"], r["model_key"]): int(r["n"]) for r in rows}


def overview_counts(
    conn: sqlite3.Connection,
    tracker_id: str,
    start: str,
    end: str,
    model: str | None,
    channel: str | None,
) -> dict:
    where, params = _filters(tracker_id, start, end, model, channel)

    sample_row = conn.execute(
        f"SELECT COUNT(*) AS sample_total FROM answer a WHERE {where}",
        params,
    ).fetchone()

    valid_row = conn.execute(
        f"SELECT COUNT(*) AS valid_total FROM answer a WHERE {where}",
        params,
    ).fetchone()

    mention_rows = conn.execute(
        f"""
        SELECT a.id AS answer_id, a.question_id, m.position, m.sentiment
        FROM answer a
        JOIN mention m ON m.answer_id = a.id
        WHERE {where} AND m.subject_type = 'own'
        """,
        params,
    ).fetchall()

    seen: dict[str, dict] = {}
    for r in mention_rows:
        seen[r["answer_id"]] = dict(r)
    rows = list(seen.values())

    active_row = conn.execute(
        """
        SELECT COUNT(*) AS active_questions
        FROM question q JOIN question_set qs ON qs.id = q.question_set_id
        WHERE qs.tracker_id = ? AND q.status = 'active'
        """,
        (tracker_id,),
    ).fetchone()

    return {
        "sample_total": sample_row["sample_total"] or 0,
        "valid_total": valid_row["valid_total"] or 0,
        "mention_total": len(rows),
        "positions": [r["position"] for r in rows if r["position"] is not None],
        "sentiments": [r["sentiment"] for r in rows],
        "covered_questions": len({r["question_id"] for r in rows}),
        "active_questions": active_row["active_questions"] or 0,
    }


def trend_rows(
    conn: sqlite3.Connection,
    tracker_id: str,
    start: str,
    end: str,
    model: str | None,
    channel: str | None,
) -> list[dict]:
    where, params = _filters(tracker_id, start, end, model, channel)
    rows = conn.execute(
        f"""
        SELECT {day_of('a.collected_at')} AS day,
               COUNT(*) AS valid_total,
               COUNT(DISTINCT m.answer_id) AS mention_total
        FROM answer a
        LEFT JOIN mention m ON m.answer_id = a.id AND m.subject_type = 'own'
        WHERE {where}
        GROUP BY day
        ORDER BY day
        """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def list_answers(
    conn: sqlite3.Connection,
    tracker_id: str,
    start: str,
    end: str,
    model: str | None,
    question_id: str | None,
    citation_scope: str,
    page: int,
    limit: int,
) -> tuple[list[dict], int]:
    where, params = _filters(tracker_id, start, end, model, None)
    if question_id:
        where += " AND a.question_id = ?"
        params.append(question_id)

    total = conn.execute(f"SELECT COUNT(*) AS n FROM answer a WHERE {where}", params).fetchone()["n"]
    offset = (page - 1) * limit
    rows = conn.execute(
        f"""
        SELECT a.*, q.text AS question_text
        FROM answer a JOIN question q ON q.id = a.question_id
        WHERE {where}
        ORDER BY a.collected_at DESC, a.id
        LIMIT ? OFFSET ?
        """,
        [*params, limit, offset],
    ).fetchall()

    items = []
    for r in rows:
        item = dict(r)
        item["mentions"] = [
            dict(m)
            for m in conn.execute(
                "SELECT m.*, b.name AS brand_name FROM mention m JOIN brand b ON b.id = m.brand_id WHERE m.answer_id = ?",
                (r["id"],),
            ).fetchall()
        ]
        scope_clause = ""
        scope_params: list[Any] = [r["id"]]
        if citation_scope in {"brand", "competitor", "media"}:
            scope_clause = " AND owner_type = ?"
            scope_params.append(citation_scope)
        item["citations"] = [
            dict(c)
            for c in conn.execute(
                f"SELECT * FROM citation WHERE answer_id = ?{scope_clause} ORDER BY position",
                scope_params,
            ).fetchall()
        ]
        items.append(item)
    return items, total


def citation_summary(
    conn: sqlite3.Connection,
    tracker_id: str,
    start: str,
    end: str,
    model: str | None,
    channel: str | None,
) -> dict:
    where, params = _filters(tracker_id, start, end, model, channel)
    base = f"""
        FROM citation c JOIN answer a ON a.id = c.answer_id
        WHERE {where}
    """
    totals = conn.execute(
        f"""
        SELECT COUNT(*) AS total_citations,
               COUNT(DISTINCT c.domain) AS unique_domains,
               COUNT(DISTINCT c.url_hash) AS unique_urls,
               SUM(CASE WHEN c.cite_type = 'video' THEN 1 ELSE 0 END) AS video_count,
               SUM(CASE WHEN c.published_at_unknown = 1 THEN 1 ELSE 0 END) AS unknown_count
        {base}
        """,
        params,
    ).fetchone()

    total = totals["total_citations"] or 0
    by_type = [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT c.media_type, COUNT(*) AS count
            {base}
            GROUP BY c.media_type ORDER BY count DESC
            """,
            params,
        ).fetchall()
    ]
    for r in by_type:
        r["share"] = round(r["count"] / total, 4) if total else None

    top_media = [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT COALESCE(c.media_name, c.domain) AS media_name, COUNT(*) AS count
            {base}
            GROUP BY media_name ORDER BY count DESC LIMIT 10
            """,
            params,
        ).fetchall()
    ]
    for r in top_media:
        r["share"] = round(r["count"] / total, 4) if total else None

    return {
        "total_citations": total,
        "unique_domains": totals["unique_domains"] or 0,
        "unique_urls": totals["unique_urls"] or 0,
        "video_share": round((totals["video_count"] or 0) / total, 4) if total else None,
        "unknown_published_share": round((totals["unknown_count"] or 0) / total, 4) if total else None,
        "by_type": by_type,
        "top_media": top_media,
    }


# ---------------------------------------------------------------------------
# 002 内容工作室
# ---------------------------------------------------------------------------

def list_channels(conn: sqlite3.Connection, tenant_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, platform_key, platform_name, account_name, status, login_mode, last_checked_at
        FROM channel_account WHERE tenant_id = ?
        ORDER BY CASE status WHEN 'connected' THEN 0 WHEN 'expired' THEN 1 ELSE 2 END, platform_name
        """,
        (tenant_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_channel(conn: sqlite3.Connection, channel_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM channel_account WHERE id = ?", (channel_id,)).fetchone()
    return dict(row) if row else None


def connect_channel(conn: sqlite3.Connection, channel_id: str, checked_at: str, login_mode: str = "demo") -> dict | None:
    conn.execute(
        "UPDATE channel_account SET status = 'connected', login_mode = ?, last_checked_at = ? WHERE id = ?",
        (login_mode, checked_at, channel_id),
    )
    conn.commit()
    return get_channel(conn, channel_id)


def list_articles(conn: sqlite3.Connection, tracker_id: str, tenant_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT a.id, a.title, a.template_key, a.status, a.version, a.goal, a.updated_at,
               (SELECT COUNT(*) FROM article_fact f WHERE f.article_id = a.id) AS fact_total,
               (SELECT COUNT(*) FROM article_fact f WHERE f.article_id = a.id AND f.verdict = 'pending') AS fact_pending,
               (SELECT COUNT(*) FROM publication p WHERE p.article_id = a.id) AS publication_total
        FROM article a
        WHERE a.tracker_id = ? AND a.tenant_id = ?
        ORDER BY CASE a.status WHEN 'published' THEN 0 WHEN 'review' THEN 1 ELSE 2 END, a.updated_at DESC
        """,
        (tracker_id, tenant_id),
    ).fetchall()
    return [dict(r) for r in rows]


def get_article(conn: sqlite3.Connection, article_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM article WHERE id = ?", (article_id,)).fetchone()
    if not row:
        return None
    article = dict(row)
    article["facts"] = [
        dict(r)
        for r in conn.execute(
            """SELECT id, claim, verdict, source_ref, position FROM article_fact
               WHERE article_id = ?
               ORDER BY CASE verdict WHEN 'pending' THEN 0 WHEN 'rejected' THEN 1 ELSE 2 END, position""",
            (article_id,),
        ).fetchall()
    ]
    article["publications"] = [
        dict(r)
        for r in conn.execute(
            """SELECT p.id, p.status, p.published_url, p.published_at, p.failure_reason,
                      c.platform_key, c.platform_name
               FROM publication p JOIN channel_account c ON c.id = p.channel_id
               WHERE p.article_id = ? ORDER BY p.created_at""",
            (article_id,),
        ).fetchall()
    ]
    return article


def update_article(conn: sqlite3.Connection, article_id: str, title: str, body: str, updated_at: str) -> dict | None:
    conn.execute(
        "UPDATE article SET title = ?, body = ?, version = version + 1, updated_at = ? WHERE id = ?",
        (title, body, updated_at, article_id),
    )
    conn.commit()
    return get_article(conn, article_id)


def create_publications(conn: sqlite3.Connection, article_id: str, channels: list[dict], created_at: str) -> tuple[list[str], list[str]]:
    """为「文章 × 渠道」建待发布记录；已存在的复用，不重复入队（FR-011）。"""
    created: list[str] = []
    existing: list[str] = []
    for ch in channels:
        row = conn.execute(
            "SELECT id FROM publication WHERE article_id = ? AND channel_id = ?", (article_id, ch["id"])
        ).fetchone()
        if row:
            existing.append(row["id"])
            continue
        pub_id = uuid.uuid4().hex
        conn.execute(
            """INSERT INTO publication(id, article_id, channel_id, status, created_at)
               VALUES (?, ?, ?, 'queued', ?)""",
            (pub_id, article_id, ch["id"], created_at),
        )
        created.append(pub_id)
    conn.commit()
    return created, existing


def window_counts(conn: sqlite3.Connection, tracker_id: str, start: str, end: str) -> dict:
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS valid_total,
               SUM(CASE WHEN m.id IS NULL THEN 0 ELSE 1 END) AS mention_total
        FROM answer a
        LEFT JOIN mention m ON m.answer_id = a.id AND m.subject_type = 'own'
        WHERE a.tracker_id = ? AND a.is_valid = 1
          AND {day_of('a.collected_at')} BETWEEN ? AND ?
        """,
        (tracker_id, start, end),
    ).fetchone()
    return {"valid_total": int(row["valid_total"] or 0), "mention_total": int(row["mention_total"] or 0)}


def data_date_range(conn: sqlite3.Connection, tracker_id: str) -> tuple[str | None, str | None]:
    row = conn.execute(
        f"SELECT MIN({day_of('collected_at')}) AS first_day, MAX({day_of('collected_at')}) AS last_day FROM answer WHERE tracker_id = ?",
        (tracker_id,),
    ).fetchone()
    return (row["first_day"], row["last_day"]) if row else (None, None)


# ---------------------------------------------------------------------------
# 下钻、答案详情、导出（FR-022 / FR-023 / FR-034）
# ---------------------------------------------------------------------------

def metric_sample_rows(
    conn: sqlite3.Connection,
    tracker_id: str,
    start: str,
    end: str,
    model: str | None,
    channel: str | None,
) -> list[dict]:
    """一条答案一行，带提及信息；由服务层按指标决定谁进入分母。"""
    where, params = _filters(tracker_id, start, end, model, channel)
    rows = conn.execute(
        f"""
        SELECT a.id AS answer_id, a.question_id, q.text AS question_text, q.tier,
               a.model_key, a.channel, {day_of('a.collected_at')} AS day, a.collected_at,
               m.id AS mention_id, m.position, m.sentiment, m.snippet
        FROM answer a
        JOIN question q ON q.id = a.question_id
        LEFT JOIN mention m ON m.answer_id = a.id AND m.subject_type = 'own'
        WHERE {where}
        ORDER BY a.collected_at DESC, a.id
        """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def coverage_rows(
    conn: sqlite3.Connection,
    tracker_id: str,
    start: str,
    end: str,
    model: str | None,
    channel: str | None,
) -> list[dict]:
    """以问题为分母：没有样本的问题也必须在列表里，否则覆盖率会被高估。"""
    on_clause = f"a.question_id = q.id AND a.is_valid = 1 AND {day_of('a.collected_at')} BETWEEN ? AND ?"
    params: list[Any] = [start, end]
    if model:
        on_clause += " AND a.model_key = ?"
        params.append(model)
    if channel:
        on_clause += " AND a.channel = ?"
        params.append(channel)
    params.append(tracker_id)
    rows = conn.execute(
        f"""
        SELECT q.id AS question_id, q.text AS question_text, q.tier,
               COUNT(a.id) AS valid_total,
               COUNT(DISTINCT m.answer_id) AS mention_total
        FROM question q
        JOIN question_set qs ON qs.id = q.question_set_id
        LEFT JOIN answer a ON {on_clause}
        LEFT JOIN mention m ON m.answer_id = a.id AND m.subject_type = 'own'
        WHERE qs.tracker_id = ? AND q.status = 'active'
        GROUP BY q.id
        ORDER BY q.text
        """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def get_answer_detail(conn: sqlite3.Connection, answer_id: str, tenant_id: str) -> dict | None:
    row = conn.execute(
        """
        SELECT a.*, q.text AS question_text, q.tier, t.name AS tracker_name, b.name AS brand_name
        FROM answer a
        JOIN question q ON q.id = a.question_id
        JOIN tracker t ON t.id = a.tracker_id
        JOIN brand b ON b.id = t.brand_id
        WHERE a.id = ? AND a.tenant_id = ?
        """,
        (answer_id, tenant_id),
    ).fetchone()
    if not row:
        return None
    detail = dict(row)
    detail["mentions"] = [
        dict(r)
        for r in conn.execute(
            """SELECT m.*, b.name AS brand_name FROM mention m JOIN brand b ON b.id = m.brand_id
               WHERE m.answer_id = ? ORDER BY m.subject_type, m.position""",
            (answer_id,),
        ).fetchall()
    ]
    detail["citations"] = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM citation WHERE answer_id = ? ORDER BY position", (answer_id,)
        ).fetchall()
    ]
    detail["blocks"] = [
        dict(r)
        for r in conn.execute(
            "SELECT seq, block_type, text, list_index FROM answer_block WHERE answer_id = ? ORDER BY seq",
            (answer_id,),
        ).fetchall()
    ]
    return detail


def export_answer_rows(
    conn: sqlite3.Connection, tracker_id: str, start: str, end: str
) -> list[dict]:
    where, params = _filters(tracker_id, start, end, None, None)
    rows = conn.execute(
        f"""
        SELECT a.id AS answer_id, {day_of('a.collected_at')} AS day, a.collected_at,
               a.question_id, q.text AS question_text, q.tier,
               a.model_key, a.channel, a.is_valid, a.text,
               CASE WHEN m.id IS NULL THEN 0 ELSE 1 END AS mentioned,
               m.position, m.sentiment, m.snippet,
               (SELECT COUNT(*) FROM citation c WHERE c.answer_id = a.id) AS citation_count
        FROM answer a
        JOIN question q ON q.id = a.question_id
        LEFT JOIN mention m ON m.answer_id = a.id AND m.subject_type = 'own'
        WHERE {where}
        ORDER BY a.collected_at DESC, a.id
        """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def models_in_period(
    conn: sqlite3.Connection, tracker_id: str, start: str, end: str
) -> list[str]:
    rows = conn.execute(
        f"""SELECT DISTINCT model_key FROM answer
           WHERE tracker_id = ? AND is_valid = 1 AND {day_of('collected_at')} BETWEEN ? AND ?
           ORDER BY model_key""",
        (tracker_id, start, end),
    ).fetchall()
    return [r["model_key"] for r in rows]


def url_dedup_citations(
    conn: sqlite3.Connection,
    tracker_id: str,
    start: str,
    end: str,
    limit: int = 100,
    offset: int = 0,
    media_type: str | None = None,
) -> dict:
    """FR-020：内容明细按 URL 去重，次数仍按出现次数累计。

    totals 是全量口径（不是当前页）；items 按 limit/offset 分页，默认 100 行。
    以前 totals 在 LIMIT 后的行上求和，数据一厚就出现「3254 次引用只报 100 次出现」。
    """
    where, params = _filters(tracker_id, start, end, None, None)
    if media_type:
        where += " AND c.media_type = ?"
        params = [*params, media_type]
    totals = conn.execute(
        f"""
        SELECT COUNT(DISTINCT c.url) AS total_urls, COUNT(*) AS total_occurrences
        FROM citation c JOIN answer a ON a.id = c.answer_id
        WHERE {where}
        """,
        params,
    ).fetchone()
    rows = conn.execute(
        f"""
        SELECT c.url, c.domain, c.media_name, c.media_type,
               COUNT(*) AS times,
               MIN({day_of('a.collected_at')}) AS first_seen
        FROM citation c JOIN answer a ON a.id = c.answer_id
        WHERE {where}
        GROUP BY c.url
        ORDER BY times DESC, first_seen DESC
        LIMIT ? OFFSET ?
        """,
        [*params, int(limit), int(offset)],
    ).fetchall()
    return {
        "items": [dict(r) for r in rows],
        "total_urls": totals["total_urls"],
        "total_occurrences": totals["total_occurrences"],
    }


def competitor_ranking_rows(
    conn: sqlite3.Connection, tracker_id: str, start: str, end: str
) -> list[dict]:
    """竞品在答案里的表现：出现次数、平均位次、情感分布。

    位次只统计有列表的提及（position 非空），和本方品牌的口径一致 ——
    没有列表的答案里，竞品也可能被点名，但那是"提到"不是"排第几"。
    """
    rows = conn.execute(
        f"""
        SELECT b.id AS brand_id, b.name AS name,
               COUNT(*) AS mentions,
               SUM(CASE WHEN m.position IS NOT NULL THEN 1 ELSE 0 END) AS ranked,
               AVG(m.position) AS avg_position,
               SUM(CASE WHEN m.sentiment = 'pos' THEN 1 ELSE 0 END) AS pos,
               SUM(CASE WHEN m.sentiment = 'neu' THEN 1 ELSE 0 END) AS neu,
               SUM(CASE WHEN m.sentiment = 'neg' THEN 1 ELSE 0 END) AS neg
        FROM mention m
        JOIN answer a ON a.id = m.answer_id
        JOIN brand b ON b.id = m.brand_id
        WHERE a.tracker_id = ? AND a.is_valid = 1 AND m.subject_type = 'competitor'
          AND {day_of('a.collected_at')} BETWEEN ? AND ?
        GROUP BY b.id, b.name
        ORDER BY mentions DESC, avg_position ASC
        """,
        (tracker_id, start, end),
    ).fetchall()
    return [dict(r) for r in rows]


def competitor_cooccurrence_rows(
    conn: sqlite3.Connection, tracker_id: str, start: str, end: str
) -> list[dict]:
    """同一答案里同时出现的品牌对（含本方品牌）。

    ``m1.brand_id < m2.brand_id`` 让每一对只算一次，也顺手排除了自配对。
    """
    rows = conn.execute(
        f"""
        SELECT b1.name AS left_name, b2.name AS right_name, COUNT(*) AS times
        FROM mention m1
        JOIN mention m2 ON m2.answer_id = m1.answer_id AND m1.brand_id < m2.brand_id
        JOIN answer a ON a.id = m1.answer_id
        JOIN brand b1 ON b1.id = m1.brand_id
        JOIN brand b2 ON b2.id = m2.brand_id
        WHERE a.tracker_id = ? AND a.is_valid = 1
          AND {day_of('a.collected_at')} BETWEEN ? AND ?
        GROUP BY b1.name, b2.name
        """,
        (tracker_id, start, end),
    ).fetchall()
    return [dict(r) for r in rows]


def sentiment_by_model_rows(
    conn: sqlite3.Connection, tracker_id: str, start: str, end: str
) -> list[dict]:
    """按模型拆情感：只统计本方品牌的提及。

    从 answer 出发左连 mention，所以「一次都没提及」的模型也会出现
    （mentions=0），这本身就是要看的结论 —— 不能因为没数据就从列表里消失。

    分母是"有提及的样本"，不是有效样本 —— 没提到品牌的答案没有情感可言。
    位次一并带出来，方便对照「情感差的模型是不是也排得靠后」。
    """
    rows = conn.execute(
        f"""
        SELECT a.model_key,
               COUNT(m.id) AS mentions,
               SUM(CASE WHEN m.sentiment = 'pos' THEN 1 ELSE 0 END) AS pos,
               SUM(CASE WHEN m.sentiment = 'neu' THEN 1 ELSE 0 END) AS neu,
               SUM(CASE WHEN m.sentiment = 'neg' THEN 1 ELSE 0 END) AS neg,
               SUM(CASE WHEN m.position IS NOT NULL THEN 1 ELSE 0 END) AS ranked,
               AVG(m.position) AS avg_position
        FROM answer a
        LEFT JOIN mention m ON m.answer_id = a.id AND m.subject_type = 'own'
        WHERE a.tracker_id = ? AND a.is_valid = 1
          AND {day_of('a.collected_at')} BETWEEN ? AND ?
        GROUP BY a.model_key
        ORDER BY a.model_key
        """,
        (tracker_id, start, end),
    ).fetchall()
    return [dict(r) for r in rows]


def latest_import_at(
    conn: sqlite3.Connection, tracker_id: str, start: str, end: str
) -> str | None:
    """区间内最后一次成功的导入时间；没有则 None（不要伪造时间）。"""
    row = conn.execute(
        f"""
        SELECT MAX(started_at) AS last
        FROM import_batch
        WHERE tracker_id = ? AND status = 'completed'
          AND {day_of('started_at')} BETWEEN ? AND ?
        """,
        (tracker_id, start, end),
    ).fetchone()
    return row["last"] if row and row["last"] else None


def question_performance_rows(
    conn: sqlite3.Connection, tracker_id: str, start: str, end: str
) -> list[dict]:
    """按问题聚合：有效样本、本方提及、有位次的条数、平均位次。

    从 question 出发左连，所以周期内一条样本都没有的问题也会出现（samples=0）。
    这类问题不是"表现差"，是"没数据"，前端要能区分开。
    """
    rows = conn.execute(
        f"""
        SELECT q.id AS question_id, q.text AS question_text, q.tier,
               COUNT(a.id) AS samples,
               SUM(CASE WHEN m.id IS NOT NULL THEN 1 ELSE 0 END) AS mentions,
               SUM(CASE WHEN m.position IS NOT NULL THEN 1 ELSE 0 END) AS ranked,
               AVG(m.position) AS avg_position
        FROM question q
        JOIN question_set qs ON qs.id = q.question_set_id
        LEFT JOIN answer a
               ON a.question_id = q.id AND a.is_valid = 1
              AND {day_of('a.collected_at')} BETWEEN ? AND ?
        LEFT JOIN mention m ON m.answer_id = a.id AND m.subject_type = 'own'
        WHERE qs.tracker_id = ? AND q.status = 'active'
        GROUP BY q.id, q.text, q.tier
        ORDER BY q.tier, q.created_at, q.id
        """,
        (start, end, tracker_id),
    ).fetchall()
    return [dict(r) for r in rows]
