"""配置面写路径的集成测试（FR-001 ~ FR-012）。

断言来自 spec，不是从实现反推：
- 导入重复问题只保留一条（FR-005）；
- 层级变更次日生效、历史不变（FR-012）；
- 采样计划 = 核心 ×3 + 长尾 ×1，按模型展开（FR-008）；
- 失败项不进任何指标分母，只体现在失败率（FR-010）；
- 竞品集合变更必须递增版本（FR-003）。
"""

import unittest
from datetime import date, timedelta

from geo import db, seed
from geo.repositories import config_repository as config
from geo.services import config_service

TRACKER = "11111111-1111-1111-1111-111111111111"
TENANT = "00000000-0000-0000-0000-000000000001"
NOW = "2026-11-11T10:00:00+08:00"


class ConfigFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = seed.seed()
        cls.conn = db.connect()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_question_import_dedupes_exact_duplicates(self):
        items = [
            {"text": "沈阳哪一家律所最专业", "tier": "core"},
            {"text": "沈阳 哪一家律所最专业", "tier": "long_tail"},
            {"text": "沈阳离婚律师怎么收费", "tier": "long_tail"},
        ]
        result = config.import_questions(self.conn, TRACKER, TENANT, items, NOW)
        self.assertEqual(len(result["created"]), 1)
        self.assertEqual(len(result["duplicates"]), 2)

    def test_tier_change_effective_tomorrow_and_history_untouched(self):
        questions = config.list_questions(self.conn, TRACKER)["items"]
        target = next(q for q in questions if q["tier"] == "long_tail")
        updated = config.update_question_tier(self.conn, target["id"], "core", NOW)
        expected = (date.today() + timedelta(days=1)).isoformat()
        self.assertEqual(updated["effective_from"], expected)
        self.assertTrue(updated["history_unchanged"])
        self.assertEqual(updated["previous_tier"], "long_tail")

    def test_sampling_plan_expands_by_model(self):
        plan = config_service.sampling_overview(self.conn, TRACKER)
        summary = plan["questions"]
        self.assertEqual(plan["daily_planned"], summary["core"] * 3 * len(plan["models"]) + summary["long_tail"] * 1 * len(plan["models"]))
        # 1516 = 16（黄金窗口）+ 1500（富周期 25 天 x 60 槽位）；失败 214 = 2 + 2×60（中断日）+ 23×4（散失败）
        self.assertEqual(plan["failure"]["attempted"], 1516)
        self.assertEqual(plan["failure"]["failed"], 214)
        self.assertEqual(plan["failure"]["status"], "over_threshold")

    def test_failed_run_items_never_enter_metric_denominator(self):
        from geo.repositories import sqlite_repository as repo

        counts = repo.overview_counts(self.conn, TRACKER, "2026-11-05", "2026-11-05", None, None)
        self.assertEqual(counts["valid_total"], 2)
        self.assertEqual(counts["sample_total"], 2)

    def test_competitor_version_increments(self):
        before = config.list_competitors(self.conn, TRACKER)["version"]
        config.add_competitor(self.conn, TRACKER, "测试竞品 · 临时")
        after = config.list_competitors(self.conn, TRACKER)["version"]
        self.assertEqual(after, before + 1)

    def test_alias_rejects_duplicates(self):
        profile = config.get_brand_profile(self.conn, TRACKER, TENANT)
        with self.assertRaises(ValueError):
            config.add_alias(self.conn, profile["id"], "辽征", "short")

    def test_archived_tracker_cannot_be_reactivated(self):
        created = config_service.create(
            self.conn,
            TENANT,
            {"name": "临时对象", "brand_name": "临时品牌"},
            NOW,
        )
        ok, _ = config.set_tracker_status(self.conn, created["tracker_id"], TENANT, "archived", NOW)
        self.assertTrue(ok)
        ok, message = config.set_tracker_status(self.conn, created["tracker_id"], TENANT, "active", NOW)
        self.assertFalse(ok)
        self.assertIn("归档", message)

    def test_tracker_without_questions_reports_zero_plan(self):
        created = config_service.create(
            self.conn,
            TENANT,
            {"name": "空对象", "brand_name": "空品牌", "brand_aliases": ["空"], "competitor_names": ["竞品"]},
            NOW,
        )
        plan = config_service.sampling_overview(self.conn, created["tracker_id"])
        self.assertEqual(plan["daily_planned"], 0)
        self.assertTrue(any("尚未导入问题" in n for n in plan["notes"]))


if __name__ == "__main__":
    unittest.main()
