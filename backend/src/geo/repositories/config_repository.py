"""Writes and reads for the configuration surface (FR-001 ~ FR-012).

Kept separate from ``sqlite_repository`` so the read model used by the
dashboard stays small. Both modules are "the repository layer": swapping
storage means replacing these two files, nothing else.
"""

import re
import sqlite3
import uuid

from geo.business_time import business_tomorrow
from geo.repositories.sqlutil import day_of

TIERS = ("core", "long_tail")
ALIAS_TYPES = ("full", "short", "en", "misspell")


def uid() -> str:
    return str(uuid.uuid4())


def normalize(text: str) -> str:
    """Question identity for duplicate detection.

    Whitespace is stripped entirely (Chinese questions are often pasted with
    stray spaces), then case-folded for latin text.
    """
    return re.sub(r"\s+", "", text or "").strip().lower()


def tomorrow() -> str:
    # FR-012 生效日按业务时区算，不能用服务器本地 date.today()（UTC 服务器凌晨会错一天）。
    return business_tomorrow()


# ---------------------------------------------------------------------------
# 品牌与别名
# ---------------------------------------------------------------------------

def get_brand_profile(conn: sqlite3.Connection, tracker_id: str, tenant_id: str) -> dict | None:
    row = conn.execute(
        """
        SELECT b.id, b.name, b.industry, b.region, t.name AS tracker_name, t.status
        FROM tracker t JOIN brand b ON b.id = t.brand_id
        WHERE t.id = ? AND t.tenant_id = ?
        """,
        (tracker_id, tenant_id),
    ).fetchone()
    if not row:
        return None
    profile = dict(row)
    profile["aliases"] = [
        dict(r)
        for r in conn.execute(
            """SELECT id, alias, alias_type, is_active FROM brand_alias
               WHERE brand_id = ? AND is_active = 1 ORDER BY alias_type, alias""",
            (profile["id"],),
        ).fetchall()
    ]
    return profile


def get_brand(conn: sqlite3.Connection, brand_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM brand WHERE id = ?", (brand_id,)).fetchone()
    return dict(row) if row else None


def brand_tenant(conn: sqlite3.Connection, brand_id: str) -> str | None:
    """品牌归属的租户。删除类操作必须先过这一关，否则知道 id 就能删别人的数据。"""
    row = conn.execute("SELECT tenant_id FROM brand WHERE id = ?", (brand_id,)).fetchone()
    return row["tenant_id"] if row else None


def add_alias(conn: sqlite3.Connection, brand_id: str, alias: str, alias_type: str) -> dict:
    alias = alias.strip()
    if alias_type not in ALIAS_TYPES:
        raise ValueError("alias_type must be one of full|short|en|misspell")
    exists = conn.execute(
        "SELECT id FROM brand_alias WHERE brand_id = ? AND alias = ?", (brand_id, alias)
    ).fetchone()
    if exists:
        raise ValueError("别名已存在")
    alias_id = uid()
    conn.execute(
        "INSERT INTO brand_alias(id, brand_id, alias, alias_type, is_active) VALUES (?, ?, ?, ?, 1)",
        (alias_id, brand_id, alias, alias_type),
    )
    conn.commit()
    return {"id": alias_id, "alias": alias, "alias_type": alias_type, "is_active": 1}


def remove_alias(
    conn: sqlite3.Connection, brand_id: str, alias_id: str, commit: bool = True
) -> dict | None:
    """删除别名，返回被删那一行（没删到返回 None）。

    返回快照而不是 bool，是为了让调用方能把 ``before`` 写进审计。
    ``commit=False`` 时把事务留给调用方 —— 删除与审计必须一起提交或一起回滚。
    """
    row = conn.execute(
        "SELECT id, brand_id, alias, alias_type, is_active FROM brand_alias WHERE id = ? AND brand_id = ?",
        (alias_id, brand_id),
    ).fetchone()
    if row is None:
        return None
    conn.execute("DELETE FROM brand_alias WHERE id = ? AND brand_id = ?", (alias_id, brand_id))
    if commit:
        conn.commit()
    return dict(row)


# ---------------------------------------------------------------------------
# 监控对象
# ---------------------------------------------------------------------------

def create_tracker(
    conn: sqlite3.Connection,
    *,
    tenant_id: str,
    name: str,
    brand_name: str,
    industry: str | None,
    region: str | None,
    aliases: list[str],
    competitors: list[str],
    owner_id: str | None,
    now: str,
    settings_json: str = "{}",
) -> dict:
    brand_id = uid()
    conn.execute(
        "INSERT INTO brand(id, tenant_id, name, industry, region, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (brand_id, tenant_id, brand_name, industry, region, now),
    )
    seen: set[str] = set()
    for alias in [brand_name, *aliases]:
        if not alias.strip() or alias.strip() in seen:
            continue
        seen.add(alias.strip())
        conn.execute(
            "INSERT INTO brand_alias(id, brand_id, alias, alias_type, is_active) VALUES (?, ?, ?, ?, 1)",
            (uid(), brand_id, alias.strip(), "full" if alias.strip() == brand_name else "short"),
        )

    competitor_set_id = uid()
    conn.execute(
        "INSERT INTO competitor_set(id, tenant_id, version, created_at) VALUES (?, ?, 1, ?)",
        (competitor_set_id, tenant_id, now),
    )
    for competitor in competitors:
        if competitor.strip():
            conn.execute(
                "INSERT INTO competitor(id, competitor_set_id, name, aliases) VALUES (?, ?, ?, '[]')",
                (uid(), competitor_set_id, competitor.strip()),
            )

    tracker_id = uid()
    conn.execute(
        """INSERT INTO tracker(id, tenant_id, brand_id, competitor_set_id, name, industry, region,
                               status, owner_id, settings, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?)""",
        (
            tracker_id,
            tenant_id,
            brand_id,
            competitor_set_id,
            name,
            industry,
            region,
            owner_id,
            settings_json,
            now,
            now,
        ),
    )
    # 问题集先建空集：FR-007 要求允许保存，但页面必须提示「不会产生数据」。
    # 同时设为生效集——否则新建后写核心词会报「还没有生效问题集」，
    # 用户在抽屉里填了核心词却存不上（上游 tracker_service 要求 active 存在）。
    question_set_id = uid()
    conn.execute(
        "INSERT INTO question_set(id, tenant_id, tracker_id, name, created_at) VALUES (?, ?, ?, ?, ?)",
        (question_set_id, tenant_id, tracker_id, f"{name} · 问题集", now),
    )
    conn.execute(
        "UPDATE tracker SET active_question_set_id = ? WHERE id = ?",
        (question_set_id, tracker_id),
    )
    conn.commit()
    return {"tracker_id": tracker_id, "brand_id": brand_id, "has_questions": False}


def update_tracker(
    conn: sqlite3.Connection,
    tracker_id: str,
    tenant_id: str,
    *,
    name: str | None,
    brand_name: str | None,
    industry: str | None,
    region: str | None,
    keywords: str | None,
    status: str | None,
    now: str,
) -> dict | None:
    row = conn.execute(
        "SELECT brand_id FROM tracker WHERE id = ? AND tenant_id = ?", (tracker_id, tenant_id)
    ).fetchone()
    if not row:
        return None

    fields, params = [], []
    for column, value in (
        ("name", name),
        ("industry", industry),
        ("region", region),
        ("keywords", keywords),
        ("status", status),
    ):
        if value is not None:
            fields.append(f"{column} = ?")
            params.append(value)
    fields.append("updated_at = ?")
    params.append(now)
    conn.execute(f"UPDATE tracker SET {', '.join(fields)} WHERE id = ?", [*params, tracker_id])

    if brand_name or industry or region:
        brand_fields, brand_params = [], []
        for column, value in (("name", brand_name), ("industry", industry), ("region", region)):
            if value is not None:
                brand_fields.append(f"{column} = ?")
                brand_params.append(value)
        if brand_fields:
            conn.execute(
                f"UPDATE brand SET {', '.join(brand_fields)} WHERE id = ?",
                [*brand_params, row["brand_id"]],
            )
    conn.commit()
    return dict(conn.execute("SELECT * FROM tracker WHERE id = ?", (tracker_id,)).fetchone())


def set_tracker_status(
    conn: sqlite3.Connection, tracker_id: str, tenant_id: str, status: str, now: str
) -> tuple[bool, str | None]:
    """状态机：archived 不可回 active（T048 / data-model 状态机）。"""
    allowed = {"draft", "active", "paused", "archived"}
    if status not in allowed:
        return False, "status must be draft|active|paused|archived"
    row = conn.execute(
        "SELECT status FROM tracker WHERE id = ? AND tenant_id = ?", (tracker_id, tenant_id)
    ).fetchone()
    if not row:
        return False, "tracker not found"
    if row["status"] == "archived" and status != "archived":
        return False, "已归档的监控对象不可重新启用"
    conn.execute(
        "UPDATE tracker SET status = ?, updated_at = ? WHERE id = ?", (status, now, tracker_id)
    )
    conn.commit()
    return True, None


# ---------------------------------------------------------------------------
# 竞品集合
# ---------------------------------------------------------------------------

def list_competitors(conn: sqlite3.Connection, tracker_id: str) -> dict:
    row = conn.execute(
        "SELECT competitor_set_id FROM tracker WHERE id = ?", (tracker_id,)
    ).fetchone()
    if not row or not row["competitor_set_id"]:
        return {"version": None, "items": []}
    set_row = conn.execute(
        "SELECT id, version FROM competitor_set WHERE id = ?", (row["competitor_set_id"],)
    ).fetchone()
    items = [
        dict(r)
        for r in conn.execute(
            "SELECT id, name, aliases FROM competitor WHERE competitor_set_id = ? ORDER BY name",
            (row["competitor_set_id"],),
        ).fetchall()
    ]
    return {"set_id": set_row["id"], "version": set_row["version"], "items": items}


def add_competitor(conn: sqlite3.Connection, tracker_id: str, name: str) -> dict:
    row = conn.execute(
        "SELECT competitor_set_id FROM tracker WHERE id = ?", (tracker_id,)
    ).fetchone()
    if not row or not row["competitor_set_id"]:
        raise ValueError("监控对象没有竞品集合")
    set_id = row["competitor_set_id"]
    exists = conn.execute(
        "SELECT id FROM competitor WHERE competitor_set_id = ? AND name = ?", (set_id, name)
    ).fetchone()
    if exists:
        raise ValueError("竞品已存在")
    item_id = uid()
    conn.execute(
        "INSERT INTO competitor(id, competitor_set_id, name, aliases) VALUES (?, ?, ?, '[]')",
        (item_id, set_id, name),
    )
    # 竞品集合变更必须留下版本，历史指标不重算（FR-003 / Edge Cases）。
    conn.execute("UPDATE competitor_set SET version = version + 1 WHERE id = ?", (set_id,))
    conn.commit()
    version = conn.execute(
        "SELECT version FROM competitor_set WHERE id = ?", (set_id,)
    ).fetchone()["version"]
    return {"id": item_id, "name": name, "version": version}


def remove_competitor(
    conn: sqlite3.Connection, tracker_id: str, competitor_id: str, commit: bool = True
) -> dict | None:
    """移除竞品，返回被删那一行（没删到返回 None）。

    竞品集合是版本化快照，删一个要 bump version —— 否则报告里"对比的是哪一版"
    就对不上了。版本号变化也一并带回去，供审计记录。
    """
    row = conn.execute(
        "SELECT competitor_set_id FROM tracker WHERE id = ?", (tracker_id,)
    ).fetchone()
    if not row:
        return None
    set_id = row["competitor_set_id"]
    before = conn.execute(
        "SELECT id, competitor_set_id, name, aliases FROM competitor WHERE id = ? AND competitor_set_id = ?",
        (competitor_id, set_id),
    ).fetchone()
    if before is None:
        return None
    conn.execute(
        "DELETE FROM competitor WHERE id = ? AND competitor_set_id = ?", (competitor_id, set_id)
    )
    conn.execute("UPDATE competitor_set SET version = version + 1 WHERE id = ?", (set_id,))
    version = conn.execute(
        "SELECT version FROM competitor_set WHERE id = ?", (set_id,)
    ).fetchone()["version"]
    if commit:
        conn.commit()
    snapshot = dict(before)
    snapshot["set_version_after"] = version
    return snapshot


# ---------------------------------------------------------------------------
# 问题集
# ---------------------------------------------------------------------------

def _question_set(conn: sqlite3.Connection, tracker_id: str, tenant_id: str, now: str) -> str:
    row = conn.execute(
        """SELECT id FROM question_set WHERE tracker_id = ?
           ORDER BY (id = (SELECT active_question_set_id FROM tracker WHERE id = ?)) DESC, created_at
           LIMIT 1""",
        (tracker_id, tracker_id),
    ).fetchone()
    if row:
        return row["id"]
    set_id = uid()
    conn.execute(
        "INSERT INTO question_set(id, tenant_id, tracker_id, name, created_at) VALUES (?, ?, ?, '问题集', ?)",
        (set_id, tenant_id, tracker_id, now),
    )
    conn.commit()
    return set_id


def list_questions(conn: sqlite3.Connection, tracker_id: str) -> dict:
    rows = conn.execute(
        """
        SELECT q.id, q.text, q.tier, q.intent, q.status, q.created_at,
               (SELECT COUNT(*) FROM answer a WHERE a.question_id = q.id AND a.is_valid = 1) AS sample_total
        FROM question q JOIN question_set qs ON qs.id = q.question_set_id
        WHERE qs.tracker_id = ?
          AND qs.id = (SELECT t.active_question_set_id FROM tracker t WHERE t.id = qs.tracker_id)
        ORDER BY CASE q.tier WHEN 'core' THEN 0 ELSE 1 END, q.created_at
        """,
        (tracker_id,),
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["daily_samples"] = 3 if item["tier"] == "core" else 1
        items.append(item)
    return {
        "items": items,
        "summary": {
            "total": sum(1 for i in items if i["status"] == "active"),
            "core": sum(1 for i in items if i["status"] == "active" and i["tier"] == "core"),
            "long_tail": sum(1 for i in items if i["status"] == "active" and i["tier"] == "long_tail"),
        },
    }


def import_questions(
    conn: sqlite3.Connection,
    tracker_id: str,
    tenant_id: str,
    items: list[dict],
    now: str,
) -> dict:
    set_id = _question_set(conn, tracker_id, tenant_id, now)
    existing = {
        r["normalized_text"]
        for r in conn.execute(
            "SELECT normalized_text FROM question WHERE question_set_id = ?", (set_id,)
        ).fetchall()
    }
    created, duplicates = [], []
    for item in items:
        text = (item.get("text") or "").strip()
        tier = item.get("tier") or "long_tail"
        if not text:
            continue
        if tier not in TIERS:
            raise ValueError("tier must be core|long_tail")
        key = normalize(text)
        if key in existing:
            duplicates.append(text)
            continue
        existing.add(key)
        question_id = uid()
        conn.execute(
            """INSERT INTO question(id, tenant_id, question_set_id, text, normalized_text, tier, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'active', ?)""",
            (question_id, tenant_id, set_id, text, key, tier, now),
        )
        created.append({"id": question_id, "text": text, "tier": tier})
    conn.commit()
    return {"created": created, "duplicates": duplicates, "question_set_id": set_id}


def update_question_tier(
    conn: sqlite3.Connection, question_id: str, tier: str, now: str
) -> dict | None:
    if tier not in TIERS:
        raise ValueError("tier must be core|long_tail")
    row = conn.execute(
        """SELECT q.id, q.tier, qs.tracker_id FROM question q
           JOIN question_set qs ON qs.id = q.question_set_id WHERE q.id = ?""",
        (question_id,),
    ).fetchone()
    if not row:
        return None
    conn.execute("UPDATE question SET tier = ? WHERE id = ?", (tier, question_id))
    # FR-012：层级变更次日生效，不回改历史采样次数。
    effective_from = tomorrow()
    conn.execute(
        "UPDATE sampling_plan SET effective_from = ? WHERE tracker_id = ?",
        (effective_from, row["tracker_id"]),
    )
    conn.commit()
    return {
        "id": question_id,
        "previous_tier": row["tier"],
        "tier": tier,
        "effective_from": effective_from,
        "history_unchanged": True,
    }


# ---------------------------------------------------------------------------
# 采样计划与失败率
# ---------------------------------------------------------------------------

def latest_plan(conn: sqlite3.Connection, tracker_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM sampling_plan WHERE tracker_id = ? ORDER BY effective_from DESC LIMIT 1",
        (tracker_id,),
    ).fetchone()
    return dict(row) if row else None


def run_list(
    conn: sqlite3.Connection,
    tracker_id: str,
    start: str | None = None,
    end: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """采集任务列表：每个 run 的条目按状态聚合，不编造进度。

    run.stats 在种子里是空 {}，所以成功/失败数从 run_item 真算。
    周期过滤按 scheduled_for（任务何时排期），和失败率统计口径一致。
    """
    clauses = ["r.tracker_id = ?"]
    params: list = [tracker_id]
    if start and end:
        clauses.append(f"{day_of('r.scheduled_for')} BETWEEN ? AND ?")
        params.extend([start, end])
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT r.id, r.trigger, r.status, r.scheduled_for, r.started_at, r.finished_at,
               COUNT(ri.id) AS total,
               SUM(CASE WHEN ri.status = 'succeeded' THEN 1 ELSE 0 END) AS succeeded,
               SUM(CASE WHEN ri.status = 'failed' THEN 1 ELSE 0 END) AS failed
        FROM run r LEFT JOIN run_item ri ON ri.run_id = r.id
        WHERE {' AND '.join(clauses)}
        GROUP BY r.id
        ORDER BY r.scheduled_for DESC, r.id DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [
        {
            "id": r["id"],
            "trigger": r["trigger"],
            "status": r["status"],
            "scheduled_for": r["scheduled_for"],
            "started_at": r["started_at"],
            "finished_at": r["finished_at"],
            "total": int(r["total"] or 0),
            "succeeded": int(r["succeeded"] or 0),
            "failed": int(r["failed"] or 0),
        }
        for r in rows
    ]


def failure_reasons(
    conn: sqlite3.Connection, tracker_id: str, start: str | None = None, end: str | None = None
) -> list[dict]:
    """按错误码统计失败原因，供采集健康页归因。

    和 failure_stats 共用同一套表与周期谓词，两者分母必须一致：
    「失败 214 次」和下面各原因加起来必须是同一个数。

    未知错误码（如种子里的 AUTH_EXPIRED 不在 ErrorCode 枚举）不隐藏，
    照实列出并标 known=false——把数据不一致暴露出来，比悄悄归到「其它」诚实。
    """
    from geo.domain.enums import ErrorCode

    clauses = ["r.tracker_id = ?", "ri.status = 'failed'"]
    params: list = [tracker_id]
    if start and end:
        clauses.append(f"{day_of('COALESCE(ri.attempted_at, r.scheduled_for)')} BETWEEN ? AND ?")
        params.extend([start, end])
    rows = conn.execute(
        f"""
        SELECT ri.error_code AS code, COUNT(*) AS count
        FROM run_item ri JOIN run r ON r.id = ri.run_id
        WHERE {' AND '.join(clauses)}
        GROUP BY ri.error_code
        ORDER BY count DESC, code
        """,
        params,
    ).fetchall()
    known = {code.value for code in ErrorCode}
    return [
        {"code": r["code"] or "UNKNOWN", "count": r["count"],
         "known": (r["code"] or "") in known}
        for r in rows
    ]


def failure_stats(
    conn: sqlite3.Connection, tracker_id: str, start: str | None = None, end: str | None = None
) -> dict:
    clauses = ["r.tracker_id = ?"]
    params: list = [tracker_id]
    if start and end:
        clauses.append(f"{day_of('COALESCE(ri.attempted_at, r.scheduled_for)')} BETWEEN ? AND ?")
        params.extend([start, end])
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS attempted,
               SUM(CASE WHEN ri.status = 'failed' THEN 1 ELSE 0 END) AS failed
        FROM run_item ri JOIN run r ON r.id = ri.run_id
        WHERE {' AND '.join(clauses)}
        """,
        params,
    ).fetchone()
    attempted = int(row["attempted"] or 0)
    failed = int(row["failed"] or 0)
    return {"attempted": attempted, "failed": failed, "rate": (failed / attempted) if attempted else None}


# ---------------------------------------------------------------------------
# 删除监控对象
# ---------------------------------------------------------------------------

# 删除顺序从叶子到根。三处顺序错了会撞外键，都是实际踩过的：
# - run_item / article 都引用 question，必须在 question 之前删；
# - tracker.active_question_set_id（迁移 010）反过来引用 question_set，
#   所以删问题集之前要先把 tracker 上的这个指针断开；
# - answer 被 citation/mention/answer_block 引用，先删子表再删父表。
# answer 的子表。answer_correction 是 020 迁移新增的人工纠正，同属这一类。
_ANSWER_CHILDREN = ("citation", "mention", "answer_block", "answer_correction")
# article 的子表。publication 没有 ON DELETE CASCADE，真实库有发布记录时
# 不先删它会让 DELETE article 撞外键、整个删对象事务回滚（老测试没造发布记录）。
_ARTICLE_CHILDREN = ("article_fact", "article_stage", "publication")


def delete_tracker(conn: sqlite3.Connection, tracker_id: str, tenant_id: str) -> dict | None:
    """级联删除监控对象及其全部派生数据，返回删除前的快照供审计用。

    **不 commit**：删除与审计必须同一事务，否则会出现"数据删了但没记账"。
    调用方负责 commit / rollback。

    返回 ``{"tracker": {...}, "deleted": {表名: 行数}}``；对象不存在或不属于该租户时返回 None。

    为什么要连子表一起删：answer/report/article 上都有 tracker_id，
    只删 tracker 会留下一堆孤儿行；而这些行里有客户问过的问题和原始回答，
    留着比删掉更危险。
    """
    row = conn.execute(
        "SELECT id, tenant_id, brand_id, name, industry, region, status, created_at "
        "FROM tracker WHERE id = ? AND tenant_id = ?",
        (tracker_id, tenant_id),
    ).fetchone()
    if not row:
        return None

    deleted: dict[str, int] = {}

    for table in _ANSWER_CHILDREN:
        cur = conn.execute(
            f"DELETE FROM {table} WHERE answer_id IN "
            f"(SELECT id FROM answer WHERE tracker_id = ?)",
            (tracker_id,),
        )
        deleted[table] = cur.rowcount

    cur = conn.execute(
        "DELETE FROM report_snapshot WHERE report_id IN "
        "(SELECT id FROM report WHERE tracker_id = ?)",
        (tracker_id,),
    )
    deleted["report_snapshot"] = cur.rowcount

    # article 引用 question，必须先于 question 删；它的子表又必须先于 article 删。
    # publication 无 ON DELETE CASCADE，是真库里删对象失败的直接原因。
    for table in _ARTICLE_CHILDREN:
        cur = conn.execute(
            f"DELETE FROM {table} WHERE article_id IN "
            "(SELECT id FROM article WHERE tracker_id = ?)",
            (tracker_id,),
        )
        deleted[table] = cur.rowcount

    # 顺序是硬约束：answer.run_item_id REFERENCES run_item(id)。
    # 必须先删 answer 再删 run_item，否则真实库里 1510 条带 run_item_id 的答案
    # 会让 DELETE run_item 撞外键、整个删除事务回滚（接口 500，对象删不掉）。
    # 老测试只造 run_item_id=NULL 的 answer，全绿但覆盖不到——这是补上的真缺口。
    cur = conn.execute("DELETE FROM answer WHERE tracker_id = ?", (tracker_id,))
    deleted["answer"] = cur.rowcount

    # run_item 引用 question，必须先于 question 删
    cur = conn.execute(
        "DELETE FROM run_item WHERE run_id IN (SELECT id FROM run WHERE tracker_id = ?)",
        (tracker_id,),
    )
    deleted["run_item"] = cur.rowcount

    for table in ("report", "article", "run"):
        cur = conn.execute(f"DELETE FROM {table} WHERE tracker_id = ?", (tracker_id,))
        deleted[table] = cur.rowcount

    # 断开 tracker 指向问题集的指针（迁移 010 新增的列），否则下面删 question_set 会撞外键
    conn.execute(
        "UPDATE tracker SET active_question_set_id = NULL WHERE id = ?", (tracker_id,)
    )

    cur = conn.execute(
        "DELETE FROM question WHERE question_set_id IN "
        "(SELECT id FROM question_set WHERE tracker_id = ?)",
        (tracker_id,),
    )
    deleted["question"] = cur.rowcount

    for table in ("question_set", "sampling_plan"):
        cur = conn.execute(f"DELETE FROM {table} WHERE tracker_id = ?", (tracker_id,))
        deleted[table] = cur.rowcount

    conn.execute("DELETE FROM tracker WHERE id = ? AND tenant_id = ?", (tracker_id, tenant_id))
    return {"tracker": dict(row), "deleted": deleted}
