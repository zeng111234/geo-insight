"""数据来源判定的行为测试。

核心承诺：真实采集才能标 real，拿不准一律 unknown——
绝不把演示数据标成真实采集（那正是在制造"真真假假"）。
"""

import unittest

from geo import data_provenance as dp


class SourceOfTest(unittest.TestCase):
    def test_real_platform_collection(self):
        self.assertEqual(dp.source_of({"raw_uri": "collect://raw/2026-11-26/deepseek-132952.json"}), dp.REAL)
        self.assertTrue(dp.is_real({"raw_uri": "collect://raw/2026-11-26/deepseek-1.json"}))

    def test_demo_seed_data(self):
        self.assertEqual(dp.source_of({"raw_uri": "s3://geo-raw/demo/abc.json"}), dp.DEMO)
        self.assertFalse(dp.is_real({"raw_uri": "s3://geo-raw/demo/abc.json"}))

    def test_offline_and_rehearse(self):
        for uri in ("collect://csv_bank/answers.jsonl", "collect://rehearse", "collect://deepseek"):
            self.assertEqual(dp.source_of({"raw_uri": uri}), dp.OFFLINE, uri)

    def test_import_batch(self):
        self.assertEqual(dp.source_of({"raw_uri": "https://example.com/a", "import_batch_id": "b1"}), dp.IMPORT)

    def test_unknown_never_claimed_real(self):
        # 拿不准必须 unknown，绝不猜成真实——这是本模块最重要的诚实原则。
        for answer in ({}, {"raw_uri": ""}, {"raw_uri": "https://example.com/no-batch"},
                       {"raw_uri": None, "import_batch_id": None}):
            kind = dp.source_of(answer)
            self.assertEqual(kind, dp.UNKNOWN, answer)
            self.assertFalse(dp.is_real(answer))

    def test_labels_and_styles_exist_for_every_kind(self):
        for kind in (dp.REAL, dp.OFFLINE, dp.DEMO, dp.IMPORT, dp.UNKNOWN):
            self.assertTrue(dp.label(kind))
            self.assertTrue(dp.style(kind))


class SummarizeTest(unittest.TestCase):
    def test_mixed_detection(self):
        answers = [
            {"raw_uri": "collect://raw/2026-11-26/deepseek-1.json"},
            {"raw_uri": "s3://geo-raw/demo/a.json"},
            {"raw_uri": "s3://geo-raw/demo/b.json"},
        ]
        result = dp.summarize(answers)
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["by_kind"], {dp.REAL: 1, dp.DEMO: 2})
        self.assertEqual(result["real_count"], 1)
        self.assertTrue(result["has_mixed"])

    def test_all_real_not_mixed(self):
        result = dp.summarize([{"raw_uri": "collect://raw/2026-11-26/deepseek-1.json"}] * 3)
        self.assertEqual(result["real_count"], 3)
        self.assertFalse(result["has_mixed"])

    def test_all_demo_not_mixed(self):
        result = dp.summarize([{"raw_uri": "s3://geo-raw/demo/a.json"}] * 3)
        self.assertEqual(result["real_count"], 0)
        self.assertFalse(result["has_mixed"])


if __name__ == "__main__":
    unittest.main()
