"""情感按模型拆分、采集健康的口径测试。

两条最要紧的性质：
1. 样本不足的模型不能给出「看起来能比较」的比例；
2. 采多了不算负缺口，达成率不可能超过 100%。
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from geo.services import metric_service, sampling_service
from geo.settings import settings
from tests import fixtures


class SentimentByModelTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = fixtures.build_minimal_db(Path(self.tmp.name) / "geo.db")
        self.addCleanup(self.conn.close)

    def answer(
        self,
        at: str,
        model: str,
        sentiment: str | None = None,
        position: int | None = None,
        *,
        subject_type: str = "own",
        brand_id: str = fixtures.BRAND,
    ) -> str:
        answer_id = fixtures.add_answer(self.conn, at, model_key=model)
        if sentiment is not None:
            fixtures.add_mention(
                self.conn,
                answer_id,
                sentiment=sentiment,
                position=position,
                subject_type=subject_type,
                brand_id=brand_id,
            )
        return answer_id

    def build(self):
        return metric_service.build_sentiment_by_model(
            self.conn, fixtures.TRACKER, "2026-11-02", "2026-11-02"
        )

    def test_model_below_threshold_has_no_ratio(self):
        for i in range(settings.min_samples):
            self.answer(f"2026-11-02T09:0{i}:00+08:00", "doubao", "pos")
        self.answer("2026-11-02T10:00:00+08:00", "kimi", "neg")

        by_model = {it["model_key"]: it for it in self.build()["items"]}
        self.assertEqual(by_model["doubao"]["status"], "ok")
        self.assertAlmostEqual(by_model["doubao"]["pos"], 1.0)
        self.assertEqual(by_model["kimi"]["status"], "insufficient_samples")
        self.assertIsNone(by_model["kimi"]["pos"], "样本不足时不能给比例")
        self.assertEqual(by_model["kimi"]["base"], 1)

    def test_model_without_any_mention_is_still_listed(self):
        """一次都没提及的模型也要出现 —— 「从不提及」本身就是结论。"""
        self.answer("2026-11-02T09:00:00+08:00", "doubao", "pos")
        self.answer("2026-11-02T09:30:00+08:00", "kimi", None)

        by_model = {it["model_key"]: it for it in self.build()["items"]}
        self.assertIn("kimi", by_model)
        self.assertEqual(by_model["kimi"]["mentions"], 0)
        self.assertEqual(by_model["kimi"]["status"], "insufficient_samples")

    def test_total_pools_every_model(self):
        for i in range(settings.min_samples):
            self.answer(f"2026-11-02T09:0{i}:00+08:00", "doubao", "pos")
            self.answer(f"2026-11-02T10:0{i}:00+08:00", "kimi", "neg")

        data = self.build()
        self.assertEqual(data["total"]["base"], settings.min_samples * 2)
        self.assertAlmostEqual(data["total"]["pos"], 0.5)
        self.assertAlmostEqual(data["total"]["neg"], 0.5)
        self.assertEqual(data["total"]["status"], "ok")

    def test_competitor_mentions_are_excluded(self):
        """竞品提及不能进本方情感分母，否则情感分布会被别人的口碑带偏。"""
        competitor_brand = "b0000000-0000-0000-0000-000000000009"
        self.conn.execute(
            "INSERT INTO brand(id, tenant_id, name, kind, created_at) VALUES (?, ?, ?, 'competitor', ?)",
            (competitor_brand, fixtures.TENANT, "华辰教育", fixtures.NOW),
        )
        self.conn.commit()
        for i in range(settings.min_samples):
            self.answer(
                f"2026-11-02T09:0{i}:00+08:00",
                "doubao",
                "neg",
                subject_type="competitor",
                brand_id=competitor_brand,
            )

        data = self.build()
        self.assertEqual(data["items"][0]["mentions"], 0)
        self.assertEqual(data["total"]["base"], 0)

    def test_avg_position_only_counts_ranked_mentions(self):
        self.answer("2026-11-02T09:00:00+08:00", "doubao", "pos", 1)
        self.answer("2026-11-02T09:10:00+08:00", "doubao", "pos", 2)
        self.answer("2026-11-02T09:20:00+08:00", "doubao", "neu", None)

        row = self.build()["items"][0]
        self.assertEqual(row["mentions"], 3)
        self.assertEqual(row["ranked"], 2)
        self.assertAlmostEqual(row["avg_position"], 1.5)

    def test_total_agrees_with_overview(self):
        """合并后必须等于总览的情感分布 —— 两条代码路径不能各算各的。"""
        plan = [("doubao", "pos"), ("doubao", "neu"), ("kimi", "neg"), ("kimi", "neg")]
        for i, (model, sentiment) in enumerate(plan):
            self.answer(f"2026-11-02T09:0{i}:00+08:00", model, sentiment)

        by_model = self.build()["total"]
        overview = metric_service.build_overview(
            self.conn, fixtures.TRACKER, "2026-11-02", "2026-11-02", None, None
        )["cards"]["sentiment"]
        for key in ("pos", "neu", "neg", "base", "status"):
            self.assertEqual(by_model[key], overview[key], f"{key} 与总览不一致")


class CollectionHealthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = fixtures.build_minimal_db(Path(self.tmp.name) / "geo.db")
        self.addCleanup(self.conn.close)
        # 1 个核心问题（夹具自带）+ 1 个长尾问题，2 个模型，核心 3 次 / 长尾 1 次
        # 每天应采 = (3 + 1) × 2 = 8
        self.long_tail = fixtures.add_question(self.conn, "沈阳刑事辩护律师怎么收费")

    def with_plan(self):
        fixtures.add_plan(self.conn, model_keys='["doubao", "kimi"]')

    def health(self, start="2026-11-02", end="2026-11-02"):
        return sampling_service.collection_health(self.conn, fixtures.TRACKER, start, end)

    def test_totals_follow_plan(self):
        self.with_plan()
        data = self.health("2026-11-02", "2026-11-03")
        self.assertTrue(data["configured"])
        # 2 天 × (核心 3 + 长尾 1) × 2 模型
        self.assertEqual(data["totals"]["expected"], 16)
        self.assertEqual(data["totals"]["actual"], 0)
        self.assertEqual(data["totals"]["missing"], 16)
        self.assertEqual(data["plan"]["question_count"], 2)

    def test_actual_is_capped_and_never_negative(self):
        self.with_plan()
        for i in range(5):
            fixtures.add_answer(
                self.conn, f"2026-11-02T09:0{i}:00+08:00", model_key="doubao"
            )

        data = self.health()
        # 核心问题应有 3 条，采了 5 条也只算 3 条
        self.assertEqual(data["totals"]["actual"], 3)
        self.assertEqual(data["totals"]["missing"], 5)  # 另一模型 4 + 长尾 1
        self.assertLessEqual(data["totals"]["rate"], 1.0)
        # 按模型这一层也要封顶，否则分项加起来会超过总数
        by_model = {row["model"]: row for row in data["by_model"]}
        self.assertEqual(by_model["doubao"]["actual"], 3)
        self.assertEqual(
            sum(row["actual"] for row in data["by_model"]), data["totals"]["actual"]
        )

    def test_by_model_and_by_day_sum_to_totals(self):
        self.with_plan()
        fixtures.add_answer(self.conn, "2026-11-02T09:00:00+08:00", model_key="doubao")
        fixtures.add_answer(self.conn, "2026-11-03T09:00:00+08:00", model_key="kimi")

        data = self.health("2026-11-02", "2026-11-03")
        for key in ("expected", "actual", "missing"):
            self.assertEqual(
                sum(row[key] for row in data["by_model"]), data["totals"][key]
            )
            self.assertEqual(
                sum(row[key] for row in data["by_day"]), data["totals"][key]
            )
        self.assertEqual(len(data["by_day"]), 2)

    def test_missing_plan_reports_configured_false(self):
        data = self.health()
        self.assertFalse(data["configured"])
        self.assertIn("采样计划", data["message"])
        self.assertIsNone(data["totals"])

    def test_last_import_at_is_null_without_batches(self):
        self.with_plan()
        self.assertIsNone(self.health()["last_import_at"], "没有导入就返回 None，不造时间")

        self.conn.execute(
            """INSERT INTO import_batch(id, tenant_id, tracker_id, source_name, source_format,
                                        status, row_total, row_inserted, started_at, finished_at)
               VALUES ('ib1', ?, ?, 'a.csv', 'csv', 'completed', 3, 3,
                       '2026-11-02T10:00:00+08:00', '2026-11-02T10:00:05+08:00')""",
            (fixtures.TENANT, fixtures.TRACKER),
        )
        self.conn.commit()
        self.assertEqual(self.health()["last_import_at"], "2026-11-02T10:00:00+08:00")

    def test_failed_batch_does_not_count_as_import(self):
        self.with_plan()
        self.conn.execute(
            """INSERT INTO import_batch(id, tenant_id, tracker_id, source_name, source_format,
                                        status, row_total, row_inserted, started_at)
               VALUES ('ib2', ?, ?, 'bad.csv', 'csv', 'failed', 1, 0, '2026-11-02T11:00:00+08:00')""",
            (fixtures.TENANT, fixtures.TRACKER),
        )
        self.conn.commit()
        self.assertIsNone(self.health()["last_import_at"])


if __name__ == "__main__":
    unittest.main()
