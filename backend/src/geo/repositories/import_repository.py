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


def batch_by_id(conn: sqlite3.Connection, batch_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM import_batch WHERE id = ?", (batch_id,)).fetchone()
    return dict(row) if row else None


def list_batches(conn: sqlite3.Connection, tenant_id: str, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        """SELECT id, source_name, source_format, status, row_total, row_inserted,
                  row_skipped, row_failed, started_at
           FROM import_batch WHERE tenant_id = ?
           ORDER BY started_at DESC, rowid DESC LIMIT ?""",
        (tenant_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def batch_data_counts(conn: sqlite3.Connection, batch_id: str) -> dict:
    """不删除，只数——dry-run 用。"""

    def count(sql: str) -> int:
        return int(conn.execute(sql, (batch_id,)).fetchone()[0])

    return {
        "answers": count("SELECT COUNT(*) FROM answer WHERE import_batch_id = ?"),
        "mentions": count(
            "SELECT COUNT(*) FROM mention WHERE answer_id IN "
            "(SELECT id FROM answer WHERE import_batch_id = ?)"
        ),
        "citations": count(
            "SELECT COUNT(*) FROM citation WHERE answer_id IN "
            "(SELECT id FROM answer WHERE import_batch_id = ?)"
        ),
        "blocks": count(
            "SELECT COUNT(*) FROM answer_block WHERE answer_id IN "
            "(SELECT id FROM answer WHERE import_batch_id = ?)"
        ),
    }


def delete_batch_data(conn: sqlite3.Connection, batch_id: str) -> dict:
    """按 answer 往下级联删干净。

    顺序不能反：citation / mention / answer_block 都是 answer 的子表，
    先删子再删父，否则外键会拦。

    刻意不动 brand 行：竞品的 brand 行是"主体注册"，可能被别的批次的提及引用，
    跟着批次删会让别人的提及失去主语。
    """
    counts: dict[str, int] = {}
    for name, table in (("citations", "citation"), ("mentions", "mention"), ("blocks", "answer_block")):
        cursor = conn.execute(
            f"DELETE FROM {table} WHERE answer_id IN (SELECT id FROM answer WHERE import_batch_id = ?)",
            (batch_id,),
        )
        counts[name] = cursor.rowcount
    cursor = conn.execute("DELETE FROM answer WHERE import_batch_id = ?", (batch_id,))
    counts["answers"] = cursor.rowcount
    return counts


def mark_batch_rolled_back(conn: sqlite3.Connection, batch_id: str, finished_at: str) -> None:
    conn.execute(
        "UPDATE import_batch SET status = 'rolled_back', finished_at = ? WHERE id = ?",
        (finished_at, batch_id),
    )


def insert_answer(conn: sqlite3.Connection, payload: dict[str, Any]) -> str:
    answer_id = new_id()
    # search_called / search_count 只有采集通道有值：导入的答案我们不知道
    # 平台当次有没有联网，所以留 NULL 表示「不知道」，不能用 0 冒充「没搜」
    # （见 014 迁移的说明）。导入通道的 payload 里没有这两个键，.get 拿到 None。
    #
    # sample_day / sample_idx 同理：只有采集通道分配槽位（015 迁移 + 唯一索引）。
    # 导入的答案不知道是当天第几次，留 NULL；唯一索引是部分索引，不管它们。
    conn.execute(
        """INSERT INTO answer(id, tenant_id, tracker_id, question_id, model_key, model_version,
                              channel, raw_uri, text, text_hash, collected_at, latency_ms,
                              token_in, token_out, cost, is_valid, import_batch_id, row_hash,
                              search_called, search_count, sample_day, sample_idx)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)""",
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
            _none_if_missing(payload.get("search_called")),
            _none_if_missing(payload.get("search_count")),
            payload.get("sample_day"),
            payload.get("sample_idx"),
        ),
    )
    return answer_id


def max_slot(
    conn: sqlite3.Connection,
    tracker_id: str,
    question_id: str,
    model_key: str,
    channel: str,
    day: str,
) -> int:
    """当天这个(问题, 模型, 渠道)已经用到的最大样本序号；没采过返回 0。

    取 max 而不是 count：如果中间有行被删掉（人工清理、测试），count 会算出
    一个已经被用过的序号，撞唯一索引。max + 1 只会往前，不会回头。
    """
    row = conn.execute(
        """SELECT MAX(sample_idx) FROM answer
           WHERE tracker_id = ? AND question_id = ? AND model_key = ? AND channel = ?
             AND sample_day = ?""",
        (tracker_id, question_id, model_key, channel, day),
    ).fetchone()
    return int(row[0] or 0)


def _none_if_missing(value):
    """True/False -> 1/0，None 保持 None。

    不能写 int(value or 0)：那样 None 会变成 0，把「不知道」记成「没搜」。
    """
    if value is None:
        return None
    return 1 if value else 0


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
