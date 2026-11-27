"""文章事实条目的审核核实（补规格 002 FR-005 的人工三态缺口）。

事实条目由 AI 生成或人工导入时默认是 pending。此前全项目没有任何
把 verdict 改成 confirmed/rejected 的路径——前端只读展示、后端无写入口，
规格 FR-005 要求的「已确认 / 待核实 / 驳回」三态只实现了一态，
导致 AI 草稿的事实永远停在待核实，审核闭环断裂。

本模块提供唯一的写入口：review_fact。规则：
  - verdict 必须是 confirmed / pending / rejected 之一；
  - 事实必须存在且属于目标文章（防跨文章改 verdict）；
  - confirmed / rejected 可附 source_ref 作为核实依据，留痕；未附也允许，
    但记成「人工核实（未附来源）」而非伪造一个来源；
  - 幂等：同一 verdict + 同一 source_ref 重复提交不报错、不产生额外副作用。
不自己 commit：由调用方在同一事务里提交。
"""

from __future__ import annotations

import sqlite3

VALID_VERDICTS = frozenset({"confirmed", "pending", "rejected"})
CONFIRMABLE = frozenset({"confirmed", "rejected"})


class FactError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def review_fact(
    conn: sqlite3.Connection,
    article_id: str,
    fact_id: str,
    verdict: str,
    source_ref: str | None = None,
) -> dict:
    """把一条事实的 verdict 改成审核结果。返回更新后的事实。"""
    if verdict not in VALID_VERDICTS:
        raise FactError("INVALID_VERDICT", f"不支持的事实状态：{verdict}")

    row = conn.execute(
        "SELECT id, article_id, claim, verdict, source_ref, position "
        "FROM article_fact WHERE id = ? AND article_id = ?",
        (fact_id, article_id),
    ).fetchone()
    if not row:
        raise FactError("NOT_FOUND", "事实条目不存在或不属于该文章")

    if verdict in CONFIRMABLE:
        # 未给来源时保留原 source_ref（可能已有），没有就如实标注人工核实。
        evidence = (source_ref or "").strip() or (row["source_ref"] or "").strip()
        if not evidence:
            evidence = "人工核实（未附来源）"
    else:
        # 退回 pending 不覆盖原来源——保留核实线索。
        evidence = (source_ref or "").strip() or (row["source_ref"] or "").strip() or None

    if row["verdict"] != verdict or (evidence or None) != (row["source_ref"] or None):
        conn.execute(
            "UPDATE article_fact SET verdict = ?, source_ref = ? WHERE id = ?",
            (verdict, evidence, fact_id),
        )

    updated = conn.execute(
        "SELECT id, article_id, claim, verdict, source_ref, position "
        "FROM article_fact WHERE id = ?",
        (fact_id,),
    ).fetchone()
    return dict(updated)
