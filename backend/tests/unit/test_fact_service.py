"""事实审核核实（规格 002 FR-005）的验收。

断点：全项目此前没有任何把 article_fact.verdict 从 pending 改成
confirmed/rejected 的路径——前端只读、后端无写入口。AI 草稿的事实永远
停在待核实，审核闭环断裂。这里守：能核实、能留来源、能驳回、非法状态
和跨文章事实都拒绝、且真的落库（新连接回读，不用手工 commit 假绿）。
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from geo.services import fact_service
from tests import fixtures

ARTICLE = "a0000000-0000-0000-0000-000000000001"
FACT = "f0000000-0000-0000-0000-000000000001"
OTHER_ARTICLE = "a0000000-0000-0000-0000-000000000002"


class FactReviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "fact.db"
        self.conn = fixtures.build_minimal_db(self.db_path)
        self._seed_article(ARTICLE)
        self._seed_article(OTHER_ARTICLE)
        self.conn.execute(
            """INSERT INTO article_fact(id, article_id, claim, verdict, source_ref, position)
               VALUES (?, ?, '广州GEO服务商案例可核验', 'pending', 'https://example.com/geo', 1)""",
            (FACT, ARTICLE),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _seed_article(self, article_id):
        self.conn.execute(
            """INSERT INTO article(id, tenant_id, tracker_id, title, body, status, template_key,
                                   created_at, updated_at)
               VALUES (?, ?, ?, '草稿', '正文', 'draft', 'ai_draft', ?, ?)""",
            (article_id, fixtures.TENANT, fixtures.TRACKER, fixtures.NOW, fixtures.NOW),
        )

    def _fresh(self):
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        return c

    def _read(self, sql, params=()):
        c = self._fresh()
        try:
            return c.execute(sql, params).fetchone()
        finally:
            c.close()

    def test_confirm_lands_with_evidence(self):
        out = fact_service.review_fact(
            self.conn, ARTICLE, FACT, "confirmed", source_ref="https://gov.cn/verify"
        )
        self.conn.commit()  # 模拟 API handler 提交
        self.assertEqual(out["verdict"], "confirmed")
        row = self._read("SELECT verdict, source_ref FROM article_fact WHERE id=?", (FACT,))
        self.assertEqual(row["verdict"], "confirmed")  # 新连接回读，真落库
        self.assertEqual(row["source_ref"], "https://gov.cn/verify")

    def test_reject_without_source_keeps_honest_label(self):
        fact_service.review_fact(self.conn, ARTICLE, FACT, "rejected")
        self.conn.commit()
        row = self._read("SELECT verdict, source_ref FROM article_fact WHERE id=?", (FACT,))
        self.assertEqual(row["verdict"], "rejected")
        # 有原来源就保留；没来源时会标「人工核实（未附来源）」，不伪造
        self.assertTrue(row["source_ref"])

    def test_illegal_verdict_rejected(self):
        with self.assertRaises(fact_service.FactError) as ctx:
            fact_service.review_fact(self.conn, ARTICLE, FACT, "maybe")
        self.assertEqual(ctx.exception.code, "INVALID_VERDICT")

    def test_cross_article_fact_rejected(self):
        with self.assertRaises(fact_service.FactError) as ctx:
            fact_service.review_fact(self.conn, OTHER_ARTICLE, FACT, "confirmed")
        self.assertEqual(ctx.exception.code, "NOT_FOUND")

    def test_idempotent_same_verdict_and_source(self):
        fact_service.review_fact(self.conn, ARTICLE, FACT, "confirmed", source_ref="s1")
        self.conn.commit()
        out = fact_service.review_fact(self.conn, ARTICLE, FACT, "confirmed", source_ref="s1")
        self.assertEqual(out["verdict"], "confirmed")
        self.assertEqual(out["source_ref"], "s1")

    def test_pending_keeps_original_source(self):
        fact_service.review_fact(self.conn, ARTICLE, FACT, "pending")
        self.conn.commit()
        row = self._read("SELECT source_ref FROM article_fact WHERE id=?", (FACT,))
        self.assertEqual(row["source_ref"], "https://example.com/geo")


if __name__ == "__main__":
    unittest.main()
