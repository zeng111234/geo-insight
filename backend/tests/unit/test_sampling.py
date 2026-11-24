"""采样缺口：应有 vs 实际。

断言的重点是口径本身——层级不同次数不同、采多了不算负缺口、渠道不影响统计、
凌晨样本算当天。这些错了，缺口清单就会和看板打架。
"""

import tempfile
import unittest
from pathlib import Path

from geo.services import sampling_service
from tests.fixtures import (
    QUESTION,
    add_answer,
    add_plan,
    add_question,
    build_minimal_db,
)


class GapTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.conn = build_minimal_db(Path(self._tmp.name) / "geo.db")
        self.addCleanup(self.conn.close)

    def gaps(self, start: str, end: str | None = None, model: str | None = None) -> dict:
        return sampling_service.compute_gaps(
            self.conn, "t0000000-0000-0000-0000-000000000001", start, end or start, model=model
        )

    def test_expected_comes_from_plan_by_tier(self):
        add_plan(self.conn, core_times=3, long_tail_times=1)
        add_question(self.conn, "长尾问题", tier="long_tail")

        gaps = self.gaps("2026-11-20")

        by_tier = {}
        for row in gaps["rows"]:
            by_tier[row["tier"]] = row["expected"]
        self.assertEqual(by_tier, {"core": 3, "long_tail": 1})
        self.assertEqual(gaps["totals"]["expected"], 4)

    def test_missing_is_expected_minus_actual(self):
        add_plan(self.conn, core_times=3)
        add_answer(self.conn, "2026-11-20T09:00:00+08:00")

        gaps = self.gaps("2026-11-20")

        self.assertEqual(gaps["rows"][0]["actual"], 1)
        self.assertEqual(gaps["rows"][0]["missing"], 2)
        self.assertEqual(gaps["totals"]["missing"], 2)

    def test_extra_samples_do_not_produce_negative_gap(self):
        add_plan(self.conn, core_times=3)
        for hour in (9, 10, 11, 12, 13):
            add_answer(self.conn, f"2026-11-20T{hour}:00:00+08:00")

        gaps = self.gaps("2026-11-20")

        self.assertEqual(gaps["rows"][0]["actual"], 5)
        self.assertEqual(gaps["rows"][0]["missing"], 0)
        # 总数里的"已有"按应有封顶，避免出现"已有 5 / 应有 3"
        self.assertEqual(gaps["totals"]["actual"], 3)
        self.assertEqual(gaps["totals"]["missing"], 0)

    def test_channel_does_not_matter(self):
        add_plan(self.conn, core_times=1)
        add_answer(self.conn, "2026-11-20T09:00:00+08:00", channel="web")

        self.assertEqual(self.gaps("2026-11-20")["totals"]["missing"], 0)

    def test_early_morning_sample_counts_on_local_day(self):
        add_plan(self.conn, core_times=1)
        add_answer(self.conn, "2026-11-20T00:30:00+08:00")

        self.assertEqual(self.gaps("2026-11-20")["totals"]["missing"], 0)
        self.assertEqual(self.gaps("2026-11-19")["totals"]["missing"], 1)

    def test_multi_day_and_multi_model(self):
        add_plan(self.conn, model_keys='["doubao", "kimi"]', core_times=2)

        gaps = self.gaps("2026-11-20", "2026-11-21")

        self.assertEqual(len(gaps["rows"]), 4)  # 2 天 × 1 问题 × 2 模型
        self.assertEqual(gaps["totals"]["expected"], 8)
        self.assertEqual(gaps["totals"]["missing"], 8)

    def test_model_filter_shrinks_scope(self):
        add_plan(self.conn, model_keys='["doubao", "kimi"]', core_times=2)

        gaps = self.gaps("2026-11-20", model="kimi")

        self.assertEqual(gaps["models"], ["kimi"])
        self.assertEqual(gaps["totals"]["expected"], 2)

    def test_plan_required(self):
        with self.assertRaises(ValueError) as ctx:
            self.gaps("2026-11-20")
        self.assertIn("采样计划", str(ctx.exception))

    def test_model_outside_plan_is_refused(self):
        add_plan(self.conn)
        with self.assertRaises(ValueError) as ctx:
            self.gaps("2026-11-20", model="gpt")
        self.assertIn("不在采样计划", str(ctx.exception))

    def test_bad_range_is_refused(self):
        add_plan(self.conn)
        with self.assertRaises(ValueError):
            self.gaps("2026-11-21", "2026-11-20")
        with self.assertRaises(ValueError):
            self.gaps("2026-13-01")


class TicketTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.conn = build_minimal_db(Path(self._tmp.name) / "geo.db")
        self.addCleanup(self.conn.close)

    def test_one_ticket_per_missing_sample(self):
        add_plan(self.conn, core_times=3)
        add_answer(self.conn, "2026-11-20T09:00:00+08:00")
        gaps = sampling_service.compute_gaps(
            self.conn, "t0000000-0000-0000-0000-000000000001", "2026-11-20", "2026-11-20"
        )

        tickets = sampling_service.gap_tickets(gaps)

        self.assertEqual(len(tickets), 2)  # 应 3 已有 1，缺 2
        self.assertEqual([t["slot"] for t in tickets], ["2/3", "3/3"])
        # 采集时间预填，人工只需填答案；已是导入通道认的时间格式
        self.assertEqual(tickets[0]["collected_at"], "2026-11-20T09:00:00")
        self.assertEqual(tickets[0]["question"], gaps["rows"][0]["question_text"])
        self.assertEqual(tickets[0]["answer"], "")


if __name__ == "__main__":
    unittest.main()
