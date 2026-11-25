"""积分账本验收（决策 009：账本是真的，定价是假的）。

三条底线，每条都有测试：
  1. 余额 = 流水求和 —— 不另存一个"当前余额"字段，两处各算一遍迟早对不上；
  2. 同一业务动作只能扣一次 —— 重试不能重复扣；
  3. 冲正记反向流水，不删原记录 —— 错了要能查出来。

外加一条金额用整数分，不用 float。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from geo.services import credit_service
from geo.settings import settings
from tests import fixtures

TENANT = settings.default_tenant_id


class CreditLedgerTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.conn = fixtures.build_minimal_db(Path(self._dir.name) / "credit.db")

    def tearDown(self):
        self.conn.close()
        self._dir.cleanup()

    def _grant(self, amount: int = 1000) -> dict:
        result = credit_service.grant(self.conn, TENANT, amount, "测试发放")
        self.conn.commit()
        return result

    def test_balance_is_sum_of_ledger_not_a_stored_field(self):
        self.assertEqual(credit_service.balance(self.conn, TENANT), 0)
        self._grant(1000)
        self.assertEqual(credit_service.balance(self.conn, TENANT), 1000)
        credit_service.spend(
            self.conn, TENANT, 300, "调用一次", ref_type="ai_call", ref_id="call-1"
        )
        self.conn.commit()
        self.assertEqual(credit_service.balance(self.conn, TENANT), 700)
        # 账上没有"余额"列可写——这也是一种约束：想作弊只能改流水
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(credit_ledger)")}
        self.assertNotIn("balance", cols)

    def test_same_ref_cannot_be_charged_twice(self):
        self._grant(1000)
        first = credit_service.spend(
            self.conn, TENANT, 100, "生成草稿", ref_type="ai_call", ref_id="call-1"
        )
        self.conn.commit()
        self.assertEqual(first["balance"], 900)

        with self.assertRaises(credit_service.CreditError) as caught:
            credit_service.spend(
                self.conn, TENANT, 100, "生成草稿（重试）", ref_type="ai_call", ref_id="call-1"
            )
        self.assertEqual(caught.exception.code, "DUPLICATE_REF")
        self.conn.rollback()
        self.assertEqual(credit_service.balance(self.conn, TENANT), 900)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM credit_ledger WHERE direction='spend'").fetchone()[0],
            1,
        )

    def test_reversal_keeps_the_original_entry(self):
        self._grant(1000)
        credit_service.spend(
            self.conn, TENANT, 100, "生成草稿", ref_type="ai_call", ref_id="call-1"
        )
        credit_service.reverse(
            self.conn, TENANT, 100, "调用失败退回", ref_type="ai_reversal", ref_id="call-1"
        )
        self.conn.commit()
        entries = credit_service.entries(self.conn, TENANT)
        self.assertEqual(len(entries), 3)  # grant + spend + reversal，没删
        self.assertEqual(credit_service.balance(self.conn, TENANT), 1000)
        self.assertEqual(
            sorted(e["direction"] for e in entries), ["grant", "reversal", "spend"]
        )

    def test_spend_refuses_when_insufficient(self):
        self._grant(50)
        with self.assertRaises(credit_service.CreditError) as caught:
            credit_service.spend(
                self.conn, TENANT, 100, "余额不足", ref_type="ai_call", ref_id="call-1"
            )
        self.assertEqual(caught.exception.code, "INSUFFICIENT")
        self.conn.rollback()
        self.assertEqual(credit_service.balance(self.conn, TENANT), 50)

    def test_amount_must_be_positive_integer_cents(self):
        for bad in (0, -1, 1.5, True):
            with self.subTest(amount=bad), self.assertRaises(ValueError):
                credit_service.record(
                    self.conn, tenant_id=TENANT, direction="grant",
                    amount=bad, reason="非法金额",
                )

    def test_unknown_direction_is_rejected(self):
        with self.assertRaises(ValueError):
            credit_service.record(
                self.conn, tenant_id=TENANT, direction="mint",
                amount=100, reason="凭空造币",
            )

    def test_ref_pair_must_be_complete(self):
        self._grant(100)
        with self.assertRaises(credit_service.CreditError) as caught:
            credit_service.record(
                self.conn, tenant_id=TENANT, direction="spend",
                amount=10, reason="引用不完整", ref_type="ai_call",
            )
        self.assertEqual(caught.exception.code, "BAD_REF")

    def test_tenant_isolation(self):
        self._grant(1000)
        other = "00000000-0000-0000-0000-999999999999"
        self.assertEqual(credit_service.balance(self.conn, other), 0)
        self.assertEqual(credit_service.entries(self.conn, other), [])


if __name__ == "__main__":
    unittest.main()
