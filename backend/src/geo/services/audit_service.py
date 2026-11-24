"""审计日志：删改类操作的留痕。

设计约定（与 ``audit_log`` 表一致）：

- **不自己 commit**。删除与审计必须同一事务，否则会出现"数据删了但没记账"。
  调用方负责 ``conn.commit()``。
- ``before_json`` / ``after_json`` 存**完整行快照**，不存差异。
  差异在读取时算得出来，而快照丢了就真丢了。
- ``actor_id`` 存 ``app_user.id``。展示用邮箱，查询时再 join — 不在这里冗余。
- 鉴权关闭时（自动化检查）没有会话，``actor_id`` 留空并写 ``actor_label='anonymous'``，
  不伪造一个系统用户。

表定义见 ``migrations/001_init.sql`` 的 ``audit_log``。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

# 已在使用的动作。新增动作必须先登记在这里，否则 record 直接报错 ——
# 拼写错误不该悄悄产生一个没人认得的分类。
# 待接：import.rollback（导入批次回滚）、answer.delete、question.delete。
ACTIONS = {
    "alias.delete",
    "competitor.delete",
}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def record(
    conn: sqlite3.Connection,
    *,
    tenant_id: str,
    action: str,
    object_type: str,
    object_id: str | None = None,
    actor_id: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    now: str | None = None,
) -> int:
    """写一条审计。**不提交事务**，由调用方 commit。"""
    if action not in ACTIONS:
        raise ValueError(f"未知的审计动作：{action}")

    cur = conn.execute(
        """
        INSERT INTO audit_log
            (tenant_id, actor_id, action, object_type, object_id, before_json, after_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tenant_id,
            actor_id,
            action,
            object_type,
            object_id,
            json.dumps(before, ensure_ascii=False) if before is not None else None,
            json.dumps(after, ensure_ascii=False) if after is not None else None,
            now or _now(),
        ),
    )
    return int(cur.lastrowid or 0)


def recent(
    conn: sqlite3.Connection,
    tenant_id: str,
    limit: int = 50,
    action: str | None = None,
    object_type: str | None = None,
) -> list[dict[str, Any]]:
    """最近的审计记录，新的在前。``actor_email`` 靠 join 出来，不落库。"""
    clauses = ["a.tenant_id = ?"]
    params: list[Any] = [tenant_id]
    if action:
        clauses.append("a.action = ?")
        params.append(action)
    if object_type:
        clauses.append("a.object_type = ?")
        params.append(object_type)
    params.append(max(1, min(limit, 500)))

    rows = conn.execute(
        f"""
        SELECT a.id, a.actor_id, u.email AS actor_email, a.action, a.object_type,
               a.object_id, a.before_json, a.after_json, a.created_at
        FROM audit_log a
        LEFT JOIN app_user u ON u.id = a.actor_id
        WHERE {' AND '.join(clauses)}
        ORDER BY a.created_at DESC, a.id DESC
        LIMIT ?
        """,
        params,
    ).fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        for key in ("before_json", "after_json"):
            raw = item.pop(key)
            item[key.replace("_json", "")] = json.loads(raw) if raw else None
        out.append(item)
    return out


def count(conn: sqlite3.Connection, tenant_id: str) -> int:
    return int(
        conn.execute("SELECT COUNT(*) FROM audit_log WHERE tenant_id = ?", (tenant_id,)).fetchone()[0]
    )
