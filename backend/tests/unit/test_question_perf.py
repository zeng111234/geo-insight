"""分问题表现的口径测试。

要点：
1. 0 条样本 = 未采集，和「样本不足」不是一回事，都不能显示成 0%；
2. 分问题加起来必须等于总览（同一个周期、同一批样本）。
"""

import tempfile
import unittest
from pathlib import Path

from geo.services import metric_service
from geo.settings import settings
from tests import fixtures

START, END = "2026-11-02", "2026-11-02"


class QuestionPerformanceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = fixtures.build_minimal_db(Path(self.tmp.name) / "geo.db")
        self.addCleanup(self.conn.close)

    def answer(self, at: str, mentioned: bool, position: int | None = None) -> str:
        answer_id = fixtures.add_answer(self.conn, at, model_key="doubao")
        if mentioned:
            fixtures.add_mention(self.conn, answer_id, sentiment="pos", position=position)
        return answer_id

    def build(self):
        return metric_service.build_question_performance(
            self.conn, fixtures.TRACKER, START, END
        )

    def test_question_without_samples_is_marked_no_samples(self):
        second = fixtures.add_question(self.conn, "沈阳离婚律师收费", tier="long_tail")
        self.answer("2026-11-02T09:00:00+08:00", True)

        by_id = {it["question_id"]: it for it in self.build()["items"]}
        self.assertEqual(by_id[second]["samples"], 0)
        self.assertEqual(by_id[second]["status"], "no_samples")
        self.assertIsNone(by_id[second]["mention_rate"], "没采集不能算成 0%")

    def test_below_threshold_has_no_rate(self):
        for i in range(settings.min_samples - 1):
            self.answer(f"2026-11-02T09:0{i}:00+08:00", True)

        row = self.build()["items"][0]
        self.assertEqual(row["status"], "insufficient_samples")
        self.assertIsNone(row["mention_rate"])

    def test_rate_and_rank_use_their_own_denominators(self):
        self.answer("2026-11-02T09:00:00+08:00", True, 1)
        self.answer("2026-11-02T09:10:00+08:00", True, 3)
        # 提到了但没有给出列表位次：进提及率分母，不进平均位次分母
        self.answer("2026-11-02T09:20:00+08:00", True, None)
        self.answer("2026-11-02T09:30:00+08:00", False)

        row = self.build()["items"][0]
        self.assertEqual(row["samples"], 4)
        self.assertEqual(row["mentions"], 3)
        self.assertAlmostEqual(row["mention_rate"], 0.75)
        self.assertEqual(row["ranked"], 2, "没有位次的提及不能算进平均位次")
        self.assertAlmostEqual(row["avg_rank"], 2.0)

    def test_totals_agree_with_overview(self):
        """分问题汇总 = 总览。两条路径各算各的，必须对得上。"""
        self.answer("2026-11-02T09:00:00+08:00", True, 2)
        self.answer("2026-11-02T09:10:00+08:00", True, 4)
        self.answer("2026-11-02T09:20:00+08:00", False)
        self.answer("2026-11-02T09:30:00+08:00", False)

        perf = self.build()["totals"]
        overview = metric_service.build_overview(
            self.conn, fixtures.TRACKER, START, END, None, None
        )
        self.assertEqual(perf["samples"], overview["valid_total"])
        self.assertEqual(perf["mentions"], overview["cards"]["mention_count"])
        self.assertAlmostEqual(
            perf["mention_rate"], overview["cards"]["mention_rate"]["value"]
        )

    def test_coverage_counts_only_questions_with_samples(self):
        fixtures.add_question(self.conn, "沈阳离婚律师收费", tier="long_tail")
        self.answer("2026-11-02T09:00:00+08:00", True)

        totals = self.build()["totals"]
        self.assertEqual(totals["questions"], 2)
        self.assertEqual(totals["covered"], 1)


if __name__ == "__main__":
    unittest.main()
