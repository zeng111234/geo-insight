# -*- coding: utf-8 -*-
"""问题集与监控对象的绑定(H2-6,003 FR-003)。

一个 tracker 可以挂多个问题集,``tracker.active_question_set_id`` 指向当前
生效集。切换生效集后,问题列表、指标口径、核心词与跑题判定整体跟随——
查询层的跟随靠 sqlite_repository 里的统一 SQL 谓词(见 _filters 与各
qs.tracker_id 查询),这里的职责是绑定本身:列出、新建、改名、改核心词、
切换,以及给读侧一个「当前生效集的核心词」入口。
"""

from __future__ import annotations

import json
import sqlite3

from geo.domain import relevance
from geo.repositories import sqlite_repository as repo


class QuestionSetError(ValueError):
    """绑定相关的业务错误,消息直接给用户看。"""


def list_question_sets(conn: sqlite3.Connection, tracker_id: str, tenant_id: str) -> list[dict] | None:
    """tracker 名下的问题集;None = tracker 不存在或跨租户。"""
    if not repo.get_tracker(conn, tracker_id, tenant_id):
        return None
    rows = conn.execute(
        """
        SELECT qs.id, qs.name, qs.keywords,
               (SELECT COUNT(*) FROM question q WHERE q.question_set_id = qs.id AND q.status = 'active') AS question_total,
               (SELECT COUNT(*) FROM question q WHERE q.question_set_id = qs.id AND q.status = 'active'
                        AND q.tier = 'core') AS core_total
        FROM question_set qs
        WHERE qs.tracker_id = ? AND qs.tenant_id = ?
        ORDER BY qs.created_at, qs.id
        """,
        (tracker_id, tenant_id),
    ).fetchall()
    active = conn.execute(
        "SELECT active_question_set_id FROM tracker WHERE id = ?", (tracker_id,)
    ).fetchone()[0]
    return [
        {**dict(r), "keywords": relevance.parse_keywords(r["keywords"]), "active": r["id"] == active}
        for r in rows
    ]


def create_question_set(
    conn: sqlite3.Connection, tracker_id: str, tenant_id: str, name: str, keywords: object, now: str
) -> dict:
    if not repo.get_tracker(conn, tracker_id, tenant_id):
        raise QuestionSetError("tracker not found")
    name = (name or "").strip()
    if not name:
        raise QuestionSetError("问题集名称不能为空")
    words = relevance.parse_keywords(keywords)
    error = relevance.validate_keywords(words)
    if error:
        raise QuestionSetError(error)
    set_id = _new_id(conn)
    conn.execute(
        """INSERT INTO question_set(id, tenant_id, tracker_id, name, keywords, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (set_id, tenant_id, tracker_id, name, json.dumps(words, ensure_ascii=False), now),
    )
    conn.commit()
    return {"id": set_id, "name": name}


def update_question_set(
    conn: sqlite3.Connection, set_id: str, tenant_id: str, name: object, keywords: object
) -> dict:
    """改名/改核心词。绑定的 tracker 不属于当前租户时按 not found 处理。"""
    row = conn.execute(
        """SELECT qs.id, qs.tracker_id, qs.tenant_id FROM question_set qs
           WHERE qs.id = ? AND qs.tenant_id = ?""",
        (set_id, tenant_id),
    ).fetchone()
    if not row:
        raise QuestionSetError("question set not found")

    if name is not None:
        name = (name or "").strip()
        if not name:
            raise QuestionSetError("问题集名称不能为空")
        conn.execute("UPDATE question_set SET name = ? WHERE id = ?", (name, set_id))
    if keywords is not None:
        words = relevance.parse_keywords(keywords)
        error = relevance.validate_keywords(words)
        if error:
            raise QuestionSetError(error)
        conn.execute(
            "UPDATE question_set SET keywords = ? WHERE id = ?",
            (json.dumps(words, ensure_ascii=False), set_id),
        )
    conn.commit()
    return {"id": set_id}


def set_active_question_set(
    conn: sqlite3.Connection, tracker_id: str, tenant_id: str, set_id: str
) -> dict:
    """切换生效问题集:校验归属后写 tracker.active_question_set_id。

    口径即刻生效——下一次任何指标/列表查询就走新集(查询层是 SQL 谓词,
    不存在缓存 stale 的问题)。
    """
    if not repo.get_tracker(conn, tracker_id, tenant_id):
        raise QuestionSetError("tracker not found")
    row = conn.execute(
        "SELECT id FROM question_set WHERE id = ? AND tracker_id = ?",
        (set_id, tracker_id),
    ).fetchone()
    if not row:
        raise QuestionSetError("该问题集不属于此监控对象")
    conn.execute(
        "UPDATE tracker SET active_question_set_id = ?, updated_at = ? WHERE id = ?",
        (set_id, _now(), tracker_id),
    )
    conn.commit()
    return {"active_question_set_id": set_id}


def active_keywords(conn: sqlite3.Connection, tracker_id: str) -> list[str]:
    """当前生效问题集的核心词(读侧统一入口:跑题判定/展示都用这里)。

    生效集没配核心词时返回空列表——「未配置」本身就是合法状态,
    is_off_topic 对空关键词返回 None(判定不了,如实如此)。
    """
    row = conn.execute(
        """SELECT qs.keywords FROM question_set qs
           JOIN tracker t ON t.active_question_set_id = qs.id
           WHERE t.id = ?""",
        (tracker_id,),
    ).fetchone()
    if not row:
        return []
    return relevance.parse_keywords(row["keywords"])


def update_active_keywords(
    conn: sqlite3.Connection, tracker_id: str, tenant_id: str, words: list[str]
) -> None:
    """核心词写进当前生效集(H2-6 之后核心词属于问题集,不属于 tracker)。"""
    if not repo.get_tracker(conn, tracker_id, tenant_id):
        raise QuestionSetError("tracker not found")
    active = conn.execute(
        "SELECT active_question_set_id FROM tracker WHERE id = ?", (tracker_id,)
    ).fetchone()[0]
    if not active:
        raise QuestionSetError("该监控对象还没有生效问题集,先创建或切换问题集")
    conn.execute(
        "UPDATE question_set SET keywords = ? WHERE id = ?",
        (json.dumps(words, ensure_ascii=False), active),
    )
    conn.commit()


def _new_id(conn: sqlite3.Connection) -> str:
    import uuid

    return uuid.uuid4().hex


def _now() -> str:
    from datetime import datetime

    return datetime.now().astimezone().isoformat(timespec="seconds")
