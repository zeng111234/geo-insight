"""AI 助手计费闭环单测：成功扣分、失败冲正、账本真落库。

守三件事（都踩过坑）：
1. record 不 commit——不提交的话连接一关流水回滚，账本丢；
2. 试点未预充额度时允许负余额（决策 009），不能像 spend() 那样余额不足就挡住；
3. LLM 失败必须冲正，不能让用户为失败买单；冲正后余额回到原值。
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from geo.services import assistant_billing_service as billing
from geo.services import assistant_chat_service as chat
from geo.services import credit_service
from tests import fixtures

TENANT = fixtures.TENANT
TRACKER = fixtures.TRACKER


class AssistantBillingServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "t.db"
        self.conn = fixtures.build_minimal_db(self.db_path)
        self.addCleanup(self.conn.close)
        self._old_available = chat.available
        chat.available = lambda: True

    def tearDown(self):
        chat.available = self._old_available

    def _fresh_balance(self):
        """新连接回读——不靠同连接缓存，验流水真的落库。"""
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        try:
            return credit_service.balance(c, TENANT)
        finally:
            c.close()

    def _entries(self):
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        try:
            return credit_service.entries(c, TENANT, limit=50)
        finally:
            c.close()

    def test_success_spends_and_commits(self):
        result = billing.ask_with_billing(
            self.conn, TENANT, TRACKER, "这期提及率如何？",
            chat_fn=lambda snap, q: {"answer": "22.22%", "token_in": 1, "token_out": 2},
        )
        self.assertEqual(result["answer"], "22.22%")
        self.assertEqual(result["cost"], 3)
        self.assertEqual(result["balance"], -3)  # 未预充，允许负（决策 009）
        self.assertEqual(self._fresh_balance(), -3, "流水必须真的落库")
        entries = self._entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["ref_type"], "assistant")

    def test_failure_reverses_no_charge(self):
        def boom(snap, q):
            raise chat.ChatError("BAD_RESPONSE", "LLM 挂了")

        with self.assertRaises(billing.BillingError) as ctx:
            billing.ask_with_billing(self.conn, TENANT, TRACKER, "问题", chat_fn=boom)
        self.assertEqual(ctx.exception.code, "BAD_RESPONSE")
        self.assertEqual(self._fresh_balance(), 0, "失败后必须冲正回原值")
        entries = self._entries()
        self.assertEqual(len(entries), 2, "SPEND + REVERSAL 两笔都留痕，不删原流水")
        self.assertEqual(
            sorted(e["direction"] for e in entries), ["reversal", "spend"],
            "既留支出也留冲正，不是删掉原流水",
        )

    def test_tracker_not_found(self):
        with self.assertRaises(billing.BillingError) as ctx:
            billing.ask_with_billing(
                self.conn, TENANT, "no-such-tracker", "问题",
                chat_fn=lambda s, q: {"answer": "x"},
            )
        self.assertEqual(ctx.exception.code, "NOT_FOUND")

    def test_no_key_blocks_before_charging(self):
        chat.available = lambda: False
        with self.assertRaises(billing.BillingError) as ctx:
            billing.ask_with_billing(
                self.conn, TENANT, TRACKER, "问题",
                chat_fn=lambda s, q: {"answer": "x"},
            )
        self.assertEqual(ctx.exception.code, "NOT_CONFIGURED")
        self.assertEqual(self._fresh_balance(), 0, "无 key 不该扣钱")


if __name__ == "__main__":
    unittest.main()
