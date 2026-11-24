"""趋势的按周聚合。

两条必须成立的性质：
1. 周的量值 = 该周各日之和（不是各日平均值再平均）；
2. 周的平均位次 = 位次和 ÷ 位次数，不是「日均位次」再平均——
   各天条数不同时，后者会算错。
"""

import tempfile
import unittest
from pathlib import Path

from geo.services import metric_service
from tests import fixtures

START, END = "2026-11-01", "2026-11-30"


class TrendWeekTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = fixtures.build_minimal_db(Path(self.tmp.name) / "geo.db")
        self.addCleanup(self.conn.close)
        self._seq = 0

    def answer(self, day: str, mentioned: bool = False, position: int | None = None) -> None:
        self._seq += 1
        answer_id = fixtures.add_answer(
            self.conn, f"{day}T09:{self._seq:02d}:00+08:00", model_key="doubao"
        )
        if mentioned:
            fixtures.add_mention(self.conn, answer_id, sentiment="pos", position=position)

    def trend(self, granularity: str = "day"):
        return metric_service.build_trend(
            self.conn, fixtures.TRACKER, START, END, None, None, granularity
        )

    def seed_two_weeks(self):
        # 11-02（周一）：3 条，1 条提及（位次 4）
        for _ in range(2):
            self.answer("2026-11-02")
        self.answer("2026-11-02", True, 4)
        # 11-03：6 条，3 条提及（位次都是 2）
        for _ in range(3):
            self.answer("2026-11-03")
        for _ in range(3):
            self.answer("2026-11-03", True, 2)
        # 11-09（下一周）：3 条，无提及
        for _ in range(3):
            self.answer("2026-11-09")

    def test_week_is_sum_of_days(self):
        self.seed_two_weeks()
        day, week = self.trend("day")["points"], self.trend("week")["points"]

        for field in ("valid_total", "mention_total"):
            self.assertEqual(
                sum(p[field] for p in day), sum(p[field] for p in week), field
            )
        first = week[0]
        self.assertEqual(first["date"], "2026-11-02")
        self.assertEqual(first["days"], 2)
        self.assertEqual(first["valid_total"], 9)
        self.assertEqual(first["mention_total"], 4)
        self.assertEqual([p["days"] for p in week], [2, 1])

    def test_week_rate_is_ratio_of_sums_not_mean_of_rates(self):
        self.seed_two_weeks()
        week = self.trend("week")["points"]
        # 4/9 = 0.4444；若按日平均会是 (0.3333 + 0.5) / 2 = 0.4167
        self.assertAlmostEqual(week[0]["mention_rate"], 0.4444)
        self.assertNotAlmostEqual(week[0]["mention_rate"], 0.4167, places=3)

    def test_week_avg_rank_is_weighted_by_ranked_counts(self):
        self.seed_two_weeks()
        day, week = self.trend("day")["points"], self.trend("week")["points"]

        # 11-02 只有 1 条有位次，低于阈值 -> 不给数字；11-03 有 3 条 -> 2.0
        self.assertIsNone(day[0]["avg_rank"])
        self.assertAlmostEqual(day[1]["avg_rank"], 2.0)
        # 周：位次和 (4 + 2×3) / 4 = 2.5；若把日平均值再平均会得到 3.0
        self.assertAlmostEqual(week[0]["avg_rank"], 2.5)
        self.assertNotAlmostEqual(week[0]["avg_rank"], 3.0, places=2)

    def test_week_without_mentions_has_zero_rate(self):
        self.seed_two_weeks()
        week = self.trend("week")["points"]
        self.assertEqual(week[1]["mention_total"], 0)
        self.assertEqual(week[1]["mention_rate"], 0.0)
        self.assertIsNone(week[1]["avg_rank"])

    def test_sunday_belongs_to_previous_iso_week(self):
        """2026-11-01 是周日，ISO 周应从 10-26（周一）算起。"""
        self.answer("2026-11-01", True, 1)
        week = self.trend("week")["points"]
        self.assertEqual(week[0]["date"], "2026-10-26")
        self.assertEqual(week[0]["days"], 1)

    def test_invalid_granularity_raises(self):
        with self.assertRaises(ValueError):
            self.trend("month")

    def test_empty_period_returns_no_points(self):
        self.assertEqual(self.trend("week")["points"], [])


if __name__ == "__main__":
    unittest.main()
