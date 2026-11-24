import unittest

TMP_DB = None  # 由 tests/__init__.py 统一指向临时库，见该文件的说明

from geo import db, seed  # noqa: E402
from geo.services import metric_service  # noqa: E402


class MetricServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed.seed()
        cls.conn = db.connect()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def overview(self, start, end):
        return metric_service.build_overview(self.conn, seed.TRACKER, start, end, None, None)

    def test_two_of_nine(self):
        payload = self.overview("2026-11-01", "2026-11-03")
        self.assertEqual(payload["valid_total"], 9)
        self.assertEqual(payload["cards"]["mention_count"], 2)
        self.assertEqual(payload["cards"]["mention_rate"]["value"], 0.2222)
        self.assertEqual(payload["cards"]["mention_rate"]["status"], "ok")

    def test_avg_rank_insufficient_with_two_ranked_samples(self):
        payload = self.overview("2026-11-01", "2026-11-03")
        self.assertEqual(payload["cards"]["avg_rank"]["sample_base"], 2)
        self.assertEqual(payload["cards"]["avg_rank"]["status"], "insufficient_samples")
        self.assertEqual(payload["no_list_samples"], 0)

    def test_no_list_sample_excluded_from_rank(self):
        payload = self.overview("2026-11-01", "2026-11-04")
        self.assertEqual(payload["valid_total"], 12)
        self.assertEqual(payload["cards"]["mention_count"], 3)
        self.assertEqual(payload["no_list_samples"], 1)
        self.assertEqual(payload["cards"]["avg_rank"]["sample_base"], 2)

    def test_two_samples_is_insufficient(self):
        payload = self.overview("2026-11-05", "2026-11-05")
        self.assertEqual(payload["valid_total"], 2)
        self.assertEqual(payload["cards"]["mention_rate"]["status"], "insufficient_samples")
        self.assertIsNone(payload["cards"]["mention_rate"]["value"])

    def test_sentiment_base_equals_mentions(self):
        payload = self.overview("2026-11-01", "2026-11-03")
        self.assertEqual(payload["cards"]["sentiment"]["base"], 2)
        self.assertEqual(payload["cards"]["sentiment"]["status"], "insufficient_samples")


if __name__ == "__main__":
    unittest.main()
