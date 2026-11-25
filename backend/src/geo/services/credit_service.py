"""积分账本服务：发放、消费、冲正、查余额。

不自己 commit：调用方可能要在同一事务里写业务数据（比如 AI 调用结果和扣分
一起落库）。余额查询走 SUM，绝不在别处缓存"当前余额"。
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from geo.domain import credits

DEFAULT_TZ = timezone(timedelta(hours=8))


class CreditError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _now() -> str:
    return datetime.now(DEFAULT_TZ).isoformat(timespec="seconds")


def record(
    conn: sqlite3.Connection,
    *,
    tenant_id: str,
    direction: str,
    amount: int,
    reason: str,
    ref_type: str | None = None,
    ref_id: str | None = None,
    actor_label: str = "system",
) -> dict:
    """记一笔流水。返回这条流水与记完后的余额。

    同一 (ref_type, ref_id) 重复记会抛 DUPLICATE_REF——这是幂等的最后一道闸门，
    数据库唯一索引是第二道。只在业务上确实需要关联时才传这两个值。
    """
    delta = credits.signed_amount(direction, amount)
    clean_reason = credits.validate_reason(reason)
    if (ref_type is None) != (ref_id is None):
        raise CreditError("BAD_REF", "ref_type 与 ref_id 必须同时给或同时不给")

    if ref_type and ref_id:
        exists = conn.execute(
            "SELECT 1 FROM credit_ledger WHERE ref_type = ? AND ref_id = ?",
            (ref_type, ref_id),
        ).fetchone()
        if exists:
            raise CreditError("DUPLICATE_REF", f"这笔业务动作已记过账：{ref_type}/{ref_id}")

    entry_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO credit_ledger(id, tenant_id, direction, amount, reason,
                                     ref_type, ref_id, actor_label, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (entry_id, tenant_id, direction, amount, clean_reason,
         ref_type, ref_id, actor_label, _now()),
    )
    return {"id": entry_id, "delta": delta, "balance": balance(conn, tenant_id)}


def balance(conn: sqlite3.Connection, tenant_id: str) -> int:
    """余额只从流水求和——这是决策 009 的硬约束。"""
    total = 0
    for row in conn.execute(
        "SELECT direction, SUM(amount) AS s FROM credit_ledger WHERE tenant_id = ? GROUP BY direction",
        (tenant_id,),
    ):
        value = int(row["s"] or 0)
        if row["direction"] == credits.SPEND:
            total -= value
        else:
            total += value
    return total


def entries(conn: sqlite3.Connection, tenant_id: str, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        """SELECT id, direction, amount, reason, ref_type, ref_id, actor_label, created_at
           FROM credit_ledger WHERE tenant_id = ? ORDER BY created_at DESC, id DESC LIMIT ?""",
        (tenant_id, limit),
    ).fetchall()
    return [
        {**dict(r), "delta": credits.signed_amount(r["direction"], r["amount"])}
        for r in rows
    ]


def grant(conn: sqlite3.Connection, tenant_id: str, amount: int, reason: str, actor_label: str = "admin") -> dict:
    return record(
        conn, tenant_id=tenant_id, direction=credits.GRANT,
        amount=amount, reason=reason, actor_label=actor_label,
    )


def spend(
    conn: sqlite3.Connection,
    tenant_id: str,
    amount: int,
    reason: str,
    *,
    ref_type: str,
    ref_id: str,
    actor_label: str = "system",
) -> dict:
    """消费。余额不足就拒绝，不记这笔流水。"""
    current = balance(conn, tenant_id)
    if current < amount:
        raise CreditError(
            "INSUFFICIENT",
            f"余额不足：现有 {current} 分，本次需要 {amount} 分",
        )
    return record(
        conn, tenant_id=tenant_id, direction=credits.SPEND,
        amount=amount, reason=reason, ref_type=ref_type, ref_id=ref_id,
        actor_label=actor_label,
    )


def reverse(
    conn: sqlite3.Connection,
    tenant_id: str,
    amount: int,
    reason: str,
    *,
    ref_type: str,
    ref_id: str,
) -> dict:
    """冲正：记一笔收入把之前的支出抵掉，不删原流水。"""
    return record(
        conn, tenant_id=tenant_id, direction=credits.REVERSAL,
        amount=amount, reason=reason, ref_type=ref_type, ref_id=ref_id,
    )
