"""E4:积分账本（决策 009 —— 账本是真的，定价是假的）。

积分不用于对外收费，只用来证明"每次 AI 调用都有成本"。额度由脚本人工发放
（``--grant``），消费由调用方记一笔支出。**余额 = 收入 − 支出，只从流水求和**，
不在别处存一个"当前余额"字段——两处各算一遍迟早对不上。

为什么流水只增不改：
  - 记错了一笔，用一笔反向冲正，不删原记录；
  - 冲正也要留下是谁、什么时候、为什么。

CHECK 限制 direction 只能是 grant/spend/reversal：出现第三种就是调用方写错了，
不该悄悄进账本。金额用整数（分），不用 float——账本里浮点累加会漏分。
"""

from __future__ import annotations

import sqlite3


def apply(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS credit_ledger (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            direction TEXT NOT NULL CHECK (direction IN ('grant', 'spend', 'reversal')),
            amount INTEGER NOT NULL CHECK (amount > 0),
            reason TEXT NOT NULL,
            ref_type TEXT,
            ref_id TEXT,
            actor_label TEXT NOT NULL DEFAULT 'system',
            created_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_credit_ledger_tenant ON credit_ledger(tenant_id, created_at)"
    )
    # 幂等键：同一笔业务动作只能扣一次。重试调用不能重复扣钱。
    conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS ux_credit_ledger_ref
           ON credit_ledger(ref_type, ref_id)
           WHERE ref_type IS NOT NULL AND ref_id IS NOT NULL"""
    )
