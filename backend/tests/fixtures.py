"""测试共用的最小数据库夹具。

只造测试需要的那几行（租户/品牌/竞品/监控对象/问题），不用 seed 的演示数据：
导入与采集的测试要能精确数出"新增了几条"，共享库会被其他测试的 seed 覆盖。

结构一律由迁移链建出来，这样夹具本身也在验证迁移链能建出可用结构。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from geo import migrate

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
        """INSERT INTO tracker(id, tenant_id, brand_id, name, created_at, updated_at)
           VALUES (?, ?, ?, '测试监控', ?, ?)""",
        (TRACKER, TENANT, BRAND, NOW, NOW),
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
