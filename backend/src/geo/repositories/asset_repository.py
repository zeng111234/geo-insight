"""客户资料文件的读写（SOP 第一步资料归档）。

只做 SQL；文件本身由 tracker_asset_service 管。所有查询都带 tenant_id，
防止跨租户读取别人的资料。
"""
from __future__ import annotations

import sqlite3

from geo.services.tracker_asset_service import VALID_CATEGORIES

# SOP「收集开户资料」的达标口径：形象照约 10 张；荣誉/活动类合计 30 张+。
SOP_TARGETS = {
    "portrait": 10,
    "environment": 10,
    "honor_event": 30,  # honor + event 合计
}


def add_asset(
    conn: sqlite3.Connection,
    *,
    asset_id: str,
    tenant_id: str,
    tracker_id: str,
    category: str,
    filename: str,
    original_name: str,
    size_bytes: int,
    now: str,
) -> dict:
    conn.execute(
        """INSERT INTO tracker_asset
           (id, tenant_id, tracker_id, category, filename, original_name, size_bytes, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (asset_id, tenant_id, tracker_id, category, filename, original_name, size_bytes, now),
    )
    return get_asset(conn, asset_id, tenant_id)  # type: ignore[return-value]


def get_asset(conn: sqlite3.Connection, asset_id: str, tenant_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM tracker_asset WHERE id = ? AND tenant_id = ?", (asset_id, tenant_id)
    ).fetchone()
    return dict(row) if row else None


def list_assets(conn: sqlite3.Connection, tracker_id: str, tenant_id: str) -> list[dict]:
    rows = conn.execute(
        """SELECT id, tracker_id, category, filename, original_name, size_bytes, created_at
           FROM tracker_asset WHERE tracker_id = ? AND tenant_id = ?
           ORDER BY category, created_at""",
        (tracker_id, tenant_id),
    ).fetchall()
    return [dict(r) for r in rows]


def delete_asset(conn: sqlite3.Connection, asset_id: str, tenant_id: str) -> dict | None:
    row = get_asset(conn, asset_id, tenant_id)
    if not row:
        return None
    conn.execute("DELETE FROM tracker_asset WHERE id = ? AND tenant_id = ?", (asset_id, tenant_id))
    return row


def checklist(conn: sqlite3.Connection, tracker_id: str, tenant_id: str) -> dict:
    """按 SOP 分类统计张数与达标情况，给「资料齐没齐」一个可核对的答案。"""
    rows = conn.execute(
        """SELECT category, COUNT(*) AS n FROM tracker_asset
           WHERE tracker_id = ? AND tenant_id = ? GROUP BY category""",
        (tracker_id, tenant_id),
    ).fetchall()
    counts = {r["category"]: r["n"] for r in rows}
    items = []
    for key in sorted(VALID_CATEGORIES):
        n = counts.get(key, 0)
        target = SOP_TARGETS.get(key)
        label = {
            "portrait": "形象照",
            "environment": "环境设施实景",
            "honor": "荣誉墙/证书/旌旗",
            "event": "颁奖/出访/采访/活动合影",
            "other": "其它资料",
        }[key]
        items.append({
            "category": key,
            "label": label,
            "count": n,
            "target": target,
            "met": None if target is None else n >= target,
        })
    honor_event = counts.get("honor", 0) + counts.get("event", 0)
    items.append({
        "category": "honor_event",
        "label": "荣誉与活动照（合计）",
        "count": honor_event,
        "target": SOP_TARGETS["honor_event"],
        "met": honor_event >= SOP_TARGETS["honor_event"],
    })
    return {"items": items, "total": sum(counts.values())}
