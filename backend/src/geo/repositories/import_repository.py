"""导入通道的读写层：上下文查询、批次记账、answer/mention/citation 落库。

校验与编排在 ``services/import_service.py``，这里只放 SQL，
方便以后换存储时逐条对照改写。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any


def new_id() -> str:
    return str(uuid.uuid4())


def default_tracker(conn: sqlite3.Connection, tenant_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM tracker WHERE tenant_id = ? ORDER BY created_at LIMIT 1",
        (tenant_id,),
    ).fetchone()
    return dict(row) if row else None


def get_tracker(conn: sqlite3.Connection, tracker_id: str, tenant_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM tracker WHERE id = ? AND tenant_id = ?", (tracker_id, tenant_id)
    ).fetchone()
    return dict(row) if row else None


def question_set_for(conn: sqlite3.Connection, tracker_id: str) -> str | None:
    row = conn.execute(
        "SELECT id FROM question_set WHERE tracker_id = ? ORDER BY created_at LIMIT 1",
        (tracker_id,),
    ).fetchone()
    return row["id"] if row else None


def ensure_question_set(
    conn: sqlite3.Connection, tracker_id: str, tenant_id: str, name: str, now: str
) -> str:
    existing = question_set_for(conn, tracker_id)
    if existing:
        return existing
    new = new_id()
    conn.execute(
        "INSERT INTO question_set(id, tenant_id, tracker_id, name, created_at) VALUES (?, ?, ?, ?, ?)",
        (new, tenant_id, tracker_id, name, now),
    )
    return new


def load_questions(conn: sqlite3.Connection, tracker_id: str) -> dict[str, str]:
    """归一化问题文本 -> question_id。"""
    rows = conn.execute(
        """
        SELECT q.id, q.normalized_text
        FROM question q JOIN question_set qs ON qs.id = q.question_set_id
        WHERE qs.tracker_id = ?
        """,
        (tracker_id,),
    ).fetchall()
    return {r["normalized_text"]: r["id"] for r in rows}


def create_question(
    conn: sqlite3.Connection,
    tenant_id: str,
    question_set_id: str,
    text: str,
    normalized_text: str,
    tier: str,
    intent: str | None,
    now: str,
) -> str:
    new = new_id()
    conn.execute(
        """INSERT INTO question(id, tenant_id, question_set_id, text, normalized_text, tier, intent, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?)""",
        (new, tenant_id, question_set_id, text, normalized_text, tier, intent, now),
    )
    return new


def load_subjects(conn: sqlite3.Connection, tenant_id: str) -> dict[str, tuple[str, str]]:
    """小写名称/别名 -> (subject_type, 展示名)。

    subject_type 直接读 ``brand.kind``，不靠"是不是品牌表里的行"来推断：
    竞品为了满足 mention 的外键也会在 brand 里有一行，靠猜测区分迟早出错。
    本方优先：同一名称既是本方别名又在竞品清单里时，按本方算。
    """
    subjects: dict[str, tuple[str, str]] = {}
    for row in conn.execute("SELECT name, kind FROM brand WHERE tenant_id = ?", (tenant_id,)):
        subjects.setdefault(row["name"].strip().lower(), (row["kind"] or "own", row["name"]))
    for row in conn.execute(
        """SELECT ba.alias, b.name FROM brand_alias ba JOIN brand b ON b.id = ba.brand_id
           WHERE b.tenant_id = ? AND ba.is_active = 1""",
        (tenant_id,),
    ):
        subjects.setdefault(row["alias"].strip().lower(), ("own", row["name"]))
    for row in conn.execute(
        """SELECT c.name, c.aliases FROM competitor c
           JOIN competitor_set cs ON cs.id = c.competitor_set_id
           WHERE cs.tenant_id = ?""",
        (tenant_id,),
    ):
        subjects.setdefault(row["name"].strip().lower(), ("competitor", row["name"]))
        try:
            for alias in json.loads(row["aliases"] or "[]"):
                if isinstance(alias, str) and alias.strip():
                    subjects.setdefault(alias.strip().lower(), ("competitor", row["name"]))
        except ValueError:
            continue
    return subjects


def ensure_brand(
    conn: sqlite3.Connection, tenant_id: str, name: str, now: str, kind: str = "own"
) -> str:
    row = conn.execute(
        "SELECT id FROM brand WHERE tenant_id = ? AND name = ?", (tenant_id, name)
    ).fetchone()
    if row:
        return row["id"]
    new = new_id()
    conn.execute(
        "INSERT INTO brand(id, tenant_id, name, kind, created_at) VALUES (?, ?, ?, ?, ?)",
        (new, tenant_id, name, kind, now),
    )
    return new


def create_batch(
    conn: sqlite3.Connection,
    tenant_id: str,
    tracker_id: str,
    source_name: str,
    source_format: str,
    started_at: str,
) -> str:
    new = new_id()
    conn.execute(
        """INSERT INTO import_batch(id, tenant_id, tracker_id, source_name, source_format, status, started_at)
           VALUES (?, ?, ?, ?, ?, 'running', ?)""",
        (new, tenant_id, tracker_id, source_name, source_format, started_at),
    )
    return new


def finish_batch(
    conn: sqlite3.Connection,
    batch_id: str,
    *,
    inserted: int,
    skipped: int,
    failed: int,
    total: int,
    status: str,
    report_uri: str | None,
    error: str | None = None,
    finished_at: str,
) -> None:
    conn.execute(
        """UPDATE import_batch
           SET status = ?, row_total = ?, row_inserted = ?, row_skipped = ?, row_failed = ?,
               report_uri = ?, error = ?, finished_at = ?
           WHERE id = ?""",
        (status, total, inserted, skipped, failed, report_uri, error, finished_at, batch_id),
    )


def row_hash_exists(conn: sqlite3.Connection, row_hash: str) -> bool:
    return (
        conn.execute("SELECT 1 FROM answer WHERE row_hash = ? LIMIT 1", (row_hash,)).fetchone()
        is not None
    )


def insert_answer(conn: sqlite3.Connection, payload: dict[str, Any]) -> str:
    answer_id = new_id()
    conn.execute(
        """INSERT INTO answer(id, tenant_id, tracker_id, question_id, model_key, model_version,
                              channel, raw_uri, text, text_hash, collected_at, latency_ms,
                              token_in, token_out, cost, is_valid, import_batch_id, row_hash)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
        (
            answer_id,
            payload["tenant_id"],
            payload["tracker_id"],
            payload["question_id"],
            payload["model_key"],
            payload.get("model_version"),
            payload["channel"],
            payload["raw_uri"],
            payload["text"],
            payload["text_hash"],
            payload["collected_at"],
            payload.get("latency_ms"),
            payload.get("token_in"),
            payload.get("token_out"),
            payload.get("cost"),
            payload["batch_id"],
            payload["row_hash"],
        ),
    )
    return answer_id


def insert_mention(conn: sqlite3.Connection, answer_id: str, brand_id: str, mention: dict) -> None:
    conn.execute(
        """INSERT INTO mention(id, answer_id, brand_id, subject_type, matched_alias, match_type,
                               position, position_type, sentiment, sentiment_confidence, snippet)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            new_id(),
            answer_id,
            brand_id,
            mention["subject_type"],
            mention["matched_alias"],
            mention["match_type"],
            mention.get("position"),
            "list_index" if mention.get("position") is not None else None,
            mention["sentiment"],
            mention.get("sentiment_confidence"),
            mention["snippet"],
        ),
    )


def insert_citation(conn: sqlite3.Connection, answer_id: str, citation: dict) -> None:
    conn.execute(
        """INSERT INTO citation(id, answer_id, url, url_hash, domain, media_name, media_type,
                                owner_type, position, cite_type, published_at, published_at_unknown)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            new_id(),
            answer_id,
            citation["url"],
            citation["url_hash"],
            citation["domain"],
            citation.get("media_name"),
            citation["media_type"],
            citation["owner_type"],
            citation["position"],
            citation["cite_type"],
            citation.get("published_at"),
            0 if citation.get("published_at") else 1,
        ),
    )
