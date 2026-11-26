"""回答的人工纠正（P2）。

## 为什么是独立表，不写回 mention

roadmap 的口径是「人工标注作为唯一事实源，识别只做预填」。但系统里 19 项手算值
（11-01..11-03 提及率 22.22%、9 样本等）已经用**自动识别结果**锁定。
如果纠正写回 mention，重算就会改这些数，已验收的基准作废——这不是产品决策该顺手做的事。

所以纠正记录独立成表：它是人工结论的**事实源**，可回显、可审计、可导出；
自动识别仍是可复现的另一路。将来要让纠正参与指标，是一条显式的口径变更
（改查询 + 改基准），不能靠"点了纠正所以数字变了"这种静默行为。

## upsert 与审计

一条回答只有一条纠正（answer_id 主键）。再纠正一次是覆盖，不是追加——
否则回显时不知道哪条算数。旧值在覆盖前进审计，谁在什么时候把什么改成什么可查。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from geo.services import audit_service
from geo.services.audit_service import _now as audit_now

ALLOWED_SENTIMENTS = frozenset({"pos", "neu", "neg"})


class CorrectionError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def validate(payload: dict[str, Any]) -> dict[str, Any]:
    """校验纠正字段。宽松在于允许只改其中一项（其余沿用旧值由调用方合并）。"""
    out: dict[str, Any] = {}
    if "own_mentioned" in payload and payload["own_mentioned"] is not None:
        value = payload["own_mentioned"]
        if isinstance(value, bool):
            value = int(value)
        if value not in (0, 1):
            raise CorrectionError("BAD_OWN_MENTIONED", "own_mentioned 必须是 0/1/null")
        out["own_mentioned"] = value
    elif "own_mentioned" in payload:
        out["own_mentioned"] = None

    if "sentiment" in payload and payload["sentiment"] is not None:
        value = payload["sentiment"]
        if value not in ALLOWED_SENTIMENTS:
            raise CorrectionError(
                "BAD_SENTIMENT", f"sentiment 必须是 {sorted(ALLOWED_SENTIMENTS)} 之一或 null"
            )
        out["sentiment"] = value
    elif "sentiment" in payload:
        out["sentiment"] = None

    if "position" in payload and payload["position"] is not None:
        value = payload["position"]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise CorrectionError("BAD_POSITION", "position 必须是 ≥1 的整数或 null")
        out["position"] = value
    elif "position" in payload:
        out["position"] = None

    if "note" in payload:
        value = payload["note"]
        if not isinstance(value, str) or len(value) > 2000:
            raise CorrectionError("BAD_NOTE", "note 必须是 ≤2000 字的字符串")
        out["note"] = value
    return out


def get_correction(conn: sqlite3.Connection, answer_id: str, tenant_id: str) -> dict | None:
    """读一条纠正；租户不匹配按不存在处理（不泄露别的租户有没有这条）。"""
    row = conn.execute(
        """SELECT own_mentioned, sentiment, position, note, corrected_by, created_at, updated_at
           FROM answer_correction WHERE answer_id = ? AND tenant_id = ?""",
        (answer_id, tenant_id),
    ).fetchone()
    return dict(row) if row else None


def upsert_correction(
    conn: sqlite3.Connection,
    *,
    answer_id: str,
    tenant_id: str,
    payload: dict[str, Any],
    corrected_by: str = "",
) -> dict:
    """写入或覆盖纠正。答案必须属于当前租户，否则 NOT_FOUND。

    不提交事务：调用方把写入与审计放在同一事务里，审计失败整体回滚。
    """
    answer = conn.execute(
        "SELECT id FROM answer WHERE id = ? AND tenant_id = ?", (answer_id, tenant_id)
    ).fetchone()
    if not answer:
        raise CorrectionError("NOT_FOUND", f"回答不存在或不属于当前租户：{answer_id}")

    fields = validate(payload)
    if not fields:
        raise CorrectionError("EMPTY_CORRECTION", "至少要给一项纠正内容（own_mentioned/sentiment/position/note）")

    before = get_correction(conn, answer_id, tenant_id)
    # 覆盖式：只给的字段更新，没给的沿用旧值。避免"改情感把位次清空"这种意外。
    merged = dict(before or {})
    merged.update(fields)

    now = audit_now()
    conn.execute(
        """INSERT INTO answer_correction
               (answer_id, tenant_id, own_mentioned, sentiment, position, note,
                corrected_by, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(answer_id) DO UPDATE SET
               own_mentioned = excluded.own_mentioned,
               sentiment = excluded.sentiment,
               position = excluded.position,
               note = excluded.note,
               corrected_by = excluded.corrected_by,
               updated_at = excluded.updated_at""",
        (
            answer_id,
            tenant_id,
            merged.get("own_mentioned"),
            merged.get("sentiment"),
            merged.get("position"),
            merged.get("note", ""),
            corrected_by,
            (before or {}).get("created_at", now),
            now,
        ),
    )
    audit_service.record(
        conn,
        tenant_id=tenant_id,
        action="answer.correction",
        object_type="answer_correction",
        object_id=answer_id,
        before=before,
        after=get_correction(conn, answer_id, tenant_id),
        now=now,
    )
    return get_correction(conn, answer_id, tenant_id)  # type: ignore[return-value]
