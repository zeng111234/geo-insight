# -*- coding: utf-8 -*-
"""报告可比性的口径一致性：非生效问题集的样本不得混进来。

models_in_period 原来漏挂了「当前生效问题集」谓词,而所有指标查询都挂了。
结果两期指标明明口径一致(比如都是 3 条 doubao),只因非生效集里有一条 kimi,
就被判成「模型集合不一致」→ comparable=False、delta_pp 被置空。
报告是产品对外讲效果的核心,这种误判比少算一条样本严重。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from geo.repositories import sqlite_repository as repo
from geo.services import report_service
from tests import fixtures

TRACKER = fixtures.TRACKER
TENANT = fixtures.TENANT
NOW = "2026-11-11T10:00:00+08:00"


class ReportComparabilityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = fixtures.build_minimal_db(Path(self.tmp.name) / "t.db")
        self.addCleanup(self.conn.close)

    def add_inactive_question_set_answer(self, collected_at: str, model_key: str):
        """在另一个(非生效)问题集里造一条答案,模拟历史遗留样本。"""
        conn = self.conn
        conn.execute(
            """INSERT INTO question_set(id, tenant_id, tracker_id, name, created_at)
               VALUES ('qs-inactive', ?, ?, '旧问题集', ?)""",
            (TENANT, TRACKER, NOW),
        )
        conn.execute(
            """INSERT INTO question(id, tenant_id, question_set_id, text, normalized_text,
                                     tier, created_at)
               VALUES ('q-inactive', ?, 'qs-inactive', '旧问题', '旧问题', 'core', ?)""",
            (TENANT, NOW),
        )
        conn.commit()
        fixtures.add_answer(
            conn, collected_at, model_key=model_key, question_id="q-inactive"
        )

    def test_inactive_question_set_model_does_not_break_comparability(self):
        """两期生效集都只有 doubao;非生效集的 kimi 不能让报告判成不可比。"""
        conn = self.conn
        # 本期(11-04~11-05)与上期(11-02~11-03)都放生效集的 doubao 样本,
        # 每期 3 条以满足 min_samples=3,免得被"样本不足"挡住、掩盖真正的判据。
        plan = [
            ("2026-11-02", 3), ("2026-11-03", 3),
            ("2026-11-04", 3), ("2026-11-05", 3),
        ]
        for day, count in plan:
            for i in range(count):
                fixtures.add_answer(
                    conn, f"{day}T{10 + i:02d}:00:00+08:00", model_key="doubao"
                )
        # 非生效集在本期插一条 kimi:修复前 models_in_period 会把它算进来
        self.add_inactive_question_set_answer("2026-11-04T12:00:00+08:00", "kimi")

        cur = repo.models_in_period(conn, TRACKER, "2026-11-04", "2026-11-05")
        prev = repo.models_in_period(conn, TRACKER, "2026-11-02", "2026-11-03")
        self.assertEqual(cur, ["doubao"], "模型集合只能来自生效问题集")
        self.assertEqual(prev, ["doubao"])

        report = report_service.generate(
            conn, TRACKER, TENANT, "2026-11-04", "2026-11-05", NOW
        )
        comparison = report["payload"]["comparison"]
        self.assertTrue(comparison["comparable"], comparison["incomparable_reasons"])
        self.assertIsNotNone(comparison["delta_pp"])

    def test_models_in_period_empty_when_only_inactive_samples(self):
        """库里只有非生效集的样本时,模型集合应为空,不能凭空多出可比性判断的素材。"""
        self.add_inactive_question_set_answer("2026-11-04T12:00:00+08:00", "kimi")
        models = repo.models_in_period(self.conn, TRACKER, "2026-11-04", "2026-11-05")
        self.assertEqual(models, [])


if __name__ == "__main__":
    unittest.main()
