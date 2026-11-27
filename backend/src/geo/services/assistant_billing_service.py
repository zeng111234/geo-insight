"""AI 助手的计费闭环：收快照 → 记账 → 真调 LLM → 失败冲正。

单独成 service 是为了能用临时库单测完整覆盖「成功扣分 / 失败不白扣」——
API 层只做参数校验和调用，不承载账本逻辑。

账本口径与 AI 写作完全一致（决策 009）：
- 余额 = 流水求和；试点对象未预充额度时允许负余额，表示「已消费未预充」，
  不造假数字、也不用余额不足挡住功能。所以用 record(SPEND) 而非 spend()。
- LLM 失败记一笔 REVERSAL 冲正，不删原流水。
- **必须 commit**：credit_service.record 不提交，连接一关流水就回滚（真踩过）。
"""

from __future__ import annotations

import sqlite3
import uuid

from geo.domain import credits as credits_domain
from geo.repositories import sqlite_repository as repo
from geo.services import assistant_chat_service as chat
from geo.services import credit_service

COST_PER_ASK = 3  # 分/次；API 响应和前端都明示，不藏成本


class BillingError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def ask_with_billing(
    conn: sqlite3.Connection,
    tenant_id: str,
    tracker_id: str,
    question: str,
    *,
    actor_label: str = "assistant",
    chat_fn=None,
) -> dict:
    """返回答案 + 计费明细。chat_fn 可注入（测试用假调用，不联网不花钱）。"""
    if not repo.get_tracker(conn, tracker_id, tenant_id):
        raise BillingError("NOT_FOUND", "监控对象不存在")
    if not chat.available():
        raise BillingError("NOT_CONFIGURED", "没有配置 LLM key，AI 助手不可用")

    snap = chat.snapshot(conn, tracker_id, tenant_id)
    ref_id = str(uuid.uuid4())
    credit_service.record(
        conn, tenant_id=tenant_id, direction=credits_domain.SPEND,
        amount=COST_PER_ASK, reason="AI 助手提问（试点未预充额度）",
        ref_type="assistant", ref_id=ref_id, actor_label=actor_label,
    )
    # record 不 commit；但下面 LLM 调用期间不提交——一次请求要么全成，
    # 要么失败时冲正一起提交，避免中途留下半截账。

    call = chat_fn or chat.ask
    try:
        result = call(snap, question)
    except Exception as exc:
        # LLM 失败：冲正刚才那笔消费，两笔一起提交，不白扣钱。
        credit_service.record(
            conn, tenant_id=tenant_id, direction=credits_domain.REVERSAL,
            amount=COST_PER_ASK, reason="AI 助手失败冲正",
            ref_type="assistant_reversal", ref_id=ref_id,
            actor_label=actor_label,
        )
        conn.commit()
        code = getattr(exc, "code", "LLM_ERROR")
        raise BillingError(code, str(exc)) from exc

    conn.commit()
    return {
        **result,
        "cost": COST_PER_ASK,
        "balance": credit_service.balance(conn, tenant_id),
        "snapshot_period": snap.get("period"),
        "ref_id": ref_id,
    }
