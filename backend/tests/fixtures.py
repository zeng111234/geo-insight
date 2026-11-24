"""测试共用的最小数据库夹具。

只造测试需要的那几行（租户/品牌/竞品/监控对象/问题），不用 seed 的演示数据：
导入与采集的测试要能精确数出"新增了几条"，共享库会被其他测试的 seed 覆盖。

结构一律由迁移链建出来，这样夹具本身也在验证迁移链能建出可用结构。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from geo import migrate
from geo.repositories import import_repository as import_repo
from geo.services.import_service import sha

TENANT = "00000000-0000-0000-0000-000000000001"
BRAND = "b0000000-0000-0000-0000-000000000001"
COMPETITOR_SET = "c0000000-0000-0000-0000-000000000001"
TRACKER = "t0000000-0000-0000-0000-000000000001"
QUESTION_SET = "q0000000-0000-0000-0000-000000000001"
QUESTION = "q1000000-0000-0000-0000-000000000001"
QUESTION_TEXT = "沈阳哪一家律所最专业"
NOW = "2026-11-24T12:00:00+08:00"


def build_minimal_db(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    migrate.apply(conn)
    conn.execute(
        "INSERT INTO tenant(id, name, slug, created_at) VALUES (?, '测试租户', 'test', ?)", (TENANT, NOW)
    )
    conn.execute(
        "INSERT INTO brand(id, tenant_id, name, created_at) VALUES (?, ?, '辽宁辽征律师事务所', ?)",
        (BRAND, TENANT, NOW),
    )
    conn.execute(
        "INSERT INTO brand_alias(id, brand_id, alias, alias_type) VALUES ('a1', ?, '辽征律所', 'short')",
        (BRAND,),
    )
    conn.execute(
        "INSERT INTO competitor_set(id, tenant_id, version, created_at) VALUES (?, ?, 1, ?)",
        (COMPETITOR_SET, TENANT, NOW),
    )
    conn.execute(
        "INSERT INTO competitor(id, competitor_set_id, name) VALUES ('cp1', ?, '北京盈科（沈阳）律师事务所')",
        (COMPETITOR_SET,),
    )
    conn.execute(
        """INSERT INTO tracker(id, tenant_id, brand_id, competitor_set_id, name, created_at, updated_at)
           VALUES (?, ?, ?, ?, '测试监控', ?, ?)""",
        (TRACKER, TENANT, BRAND, COMPETITOR_SET, NOW, NOW),
    )
    conn.execute(
        "INSERT INTO question_set(id, tenant_id, tracker_id, name, created_at) VALUES (?, ?, ?, '核心问题', ?)",
        (QUESTION_SET, TENANT, TRACKER, NOW),
    )
    conn.execute(
        """INSERT INTO question(id, tenant_id, question_set_id, text, normalized_text, tier, created_at)
           VALUES (?, ?, ?, ?, ?, 'core', ?)""",
        (QUESTION, TENANT, QUESTION_SET, QUESTION_TEXT, QUESTION_TEXT, NOW),
    )
    conn.commit()
    return conn


def add_plan(
    conn: sqlite3.Connection,
    *,
    model_keys: str = '["doubao"]',
    core_times: int = 3,
    long_tail_times: int = 1,
    channel: str = "api",
) -> str:
    plan_id = "p0000000-0000-0000-0000-000000000001"
    conn.execute(
        """INSERT INTO sampling_plan(id, tracker_id, channel, model_keys, core_times,
                                     long_tail_times, timezone, effective_from)
           VALUES (?, ?, ?, ?, ?, ?, 'Asia/Shanghai', '2026-11-01')""",
        (plan_id, TRACKER, channel, model_keys, core_times, long_tail_times),
    )
    conn.commit()
    return plan_id


def add_question(
    conn: sqlite3.Connection, text: str, tier: str = "long_tail", question_id: str | None = None
) -> str:
    new_id = question_id or f"q{abs(hash(text)) % 10**12:012d}"
    conn.execute(
        """INSERT INTO question(id, tenant_id, question_set_id, text, normalized_text, tier, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (new_id, TENANT, QUESTION_SET, text, text, tier, NOW),
    )
    conn.commit()
    return new_id


def add_answer(
    conn: sqlite3.Connection,
    collected_at: str,
    *,
    model_key: str = "doubao",
    question_id: str = QUESTION,
    text: str | None = None,
    channel: str = "api",
) -> str:
    """直接写一条有效答案，绕过导入通道的解析——测试要精确控制时间与条数。"""
    body = text or f"{collected_at} / {model_key} 的答案"
    return import_repo.insert_answer(
        conn,
        {
            "tenant_id": TENANT,
            "tracker_id": TRACKER,
            "question_id": question_id,
            "model_key": model_key,
            "model_version": None,
            "channel": channel,
            "raw_uri": "test://fixture",
            "text": body,
            "text_hash": sha(body),
            "collected_at": collected_at,
            "batch_id": None,
            "row_hash": sha(
                "|".join([TRACKER, question_id, model_key, channel, collected_at, body])
            ),
        },
    )


def add_mention(
    conn: sqlite3.Connection,
    answer_id: str,
    *,
    sentiment: str = "pos",
    position: int | None = None,
    subject_type: str = "own",
    brand_id: str = BRAND,
    alias: str = "辽宁辽征律师事务所",
) -> str:
    """写一条提及。UNIQUE(answer_id, brand_id)，同一答案同一品牌只能有一条。"""
    new_id = f"m{abs(hash((answer_id, brand_id))) % 10**12:012d}"
    conn.execute(
        """INSERT INTO mention(id, answer_id, brand_id, subject_type, matched_alias,
                               match_type, position, position_type, sentiment,
                               sentiment_confidence, snippet)
           VALUES (?, ?, ?, ?, ?, 'exact', ?, ?, ?, 0.9, ?)""",
        (
            new_id,
            answer_id,
            brand_id,
            subject_type,
            alias,
            position,
            "list_index" if position is not None else None,
            sentiment,
            f"{alias} 出现在答案中",
        ),
    )
    conn.commit()
    return new_id
