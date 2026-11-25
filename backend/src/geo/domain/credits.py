"""积分账本的唯一口径（决策 009）。

三个不变式：
  1. 余额 = 所有 grant + reversal − 所有 spend，**只从流水求和**；
  2. 金额单位是"分"的整数，不用 float；
  3. 同一 (ref_type, ref_id) 只能记一笔——网络重试不能重复扣。

**账本是真的，定价是假的**：额度由脚本人工发放，不对外收费。
这句话必须和代码放在一起，免得半年后被当成计费系统去接支付。
"""

from __future__ import annotations

GRANT = "grant"
SPEND = "spend"
REVERSAL = "reversal"
DIRECTIONS = frozenset({GRANT, SPEND, REVERSAL})


def signed_amount(direction: str, amount: int) -> int:
    """把方向和正数金额换成有符号增量；写错方向直接报错。"""
    if direction not in DIRECTIONS:
        raise ValueError(f"未知的流水方向：{direction}")
    if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
        raise ValueError("金额必须是正整数（单位：分）")
    if direction == SPEND:
        return -amount
    return amount


def validate_reason(reason: str) -> str:
    value = (reason or "").strip()
    if not value or len(value) > 200:
        raise ValueError("流水原因必须是 1-200 字")
    return value
