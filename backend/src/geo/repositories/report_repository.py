"""Reports are immutable snapshots: the payload is written once and never updated."""

import json
import sqlite3
import uuid


def uid() -> str:
    return str(uuid.uuid4())


def save_report(
    conn: sqlite3.Connection,
    *,
    tenant_id: str,
    tracker_id: str,
    period_start: str,
    period_end: str,
    metric_version: str,
    model_set_hash: str,
    competitor_set_version: int | None,
    payload: dict,
    payload_hash: str,
    created_by: str | None,
    now: str,
) -> dict:
    # 同一租户 / 监控对象 / 周期 / 口径版本只保留一份报告。
    # 重试与用户重复点击都返回首次结果，不产生第二条快照（决策 005）。
    existing = conn.execute(
        """SELECT r.id FROM report r
           WHERE r.tenant_id = ? AND r.tracker_id = ?
             AND r.period_start = ? AND r.period_end = ?
             AND r.metric_version = ? AND r.status = 'ready'
           ORDER BY r.created_at LIMIT 1""",
        (tenant_id, tracker_id, period_start, period_end, metric_version),
    ).fetchone()
    if existing:
        reused = get_report(conn, existing["id"], tenant_id)
        reused["reused"] = True
        return reused

    report_id = uid()
    conn.execute(
        """INSERT INTO report(id, tenant_id, tracker_id, period_start, period_end, status,
                              metric_version, model_set_hash, competitor_set_version,
                              created_by, created_at, ready_at)
           VALUES (?, ?, ?, ?, ?, 'ready', ?, ?, ?, ?, ?, ?)""",
        (
            report_id,
            tenant_id,
            tracker_id,
            period_start,
            period_end,
            metric_version,
            model_set_hash,
            competitor_set_version,
            created_by,
            now,
            now,
        ),
    )
    conn.execute(
        """INSERT INTO report_snapshot(report_id, payload, payload_hash, generated_at)
           VALUES (?, ?, ?, ?)""",
        (report_id, json.dumps(payload, ensure_ascii=False, sort_keys=True), payload_hash, now),
    )
    conn.commit()
    return get_report(conn, report_id, tenant_id)


def list_reports(conn: sqlite3.Connection, tracker_id: str, tenant_id: str) -> list[dict]:
    rows = conn.execute(
        """SELECT r.id, r.period_start, r.period_end, r.status, r.metric_version,
                  r.model_set_hash, r.competitor_set_version, r.created_at, r.ready_at,
                  s.payload_hash, s.payload
           FROM report r LEFT JOIN report_snapshot s ON s.report_id = r.id
           WHERE r.tracker_id = ? AND r.tenant_id = ?
           ORDER BY r.period_end DESC, r.created_at DESC""",
        (tracker_id, tenant_id),
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        payload = json.loads(item.pop("payload") or "{}")
        comparison = payload.get("comparison") or {}
        item["comparable"] = comparison.get("comparable")
        item["delta_pp"] = comparison.get("delta_pp")
        item["mention_rate"] = (payload.get("cards") or {}).get("mention_rate")
        item["valid_total"] = (payload.get("sample") or {}).get("valid_total")
        item["days"] = (payload.get("period") or {}).get("days")
        item["conclusion_count"] = len(payload.get("conclusions") or [])
        items.append(item)
    return items


def get_report(conn: sqlite3.Connection, report_id: str, tenant_id: str) -> dict | None:
    row = conn.execute(
        """SELECT r.*, s.payload, s.payload_hash, s.generated_at
           FROM report r LEFT JOIN report_snapshot s ON s.report_id = r.id
           WHERE r.id = ? AND r.tenant_id = ?""",
        (report_id, tenant_id),
    ).fetchone()
    if not row:
        return None
    report = dict(row)
    report["payload"] = json.loads(report.pop("payload") or "{}")
    comparison = report["payload"].get("comparison") or {}
    report["comparable"] = comparison.get("comparable")
    report["mention_rate"] = (report["payload"].get("cards") or {}).get("mention_rate")
    return report


def previous_report(conn: sqlite3.Connection, tracker_id: str, tenant_id: str, before: str) -> dict | None:
    row = conn.execute(
        """SELECT * FROM report
           WHERE tracker_id = ? AND tenant_id = ? AND period_end < ?
           ORDER BY period_end DESC LIMIT 1""",
        (tracker_id, tenant_id, before),
    ).fetchone()
    return dict(row) if row else None
