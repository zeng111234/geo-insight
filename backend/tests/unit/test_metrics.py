import unittest

from geo.domain import metrics


class MetricDefinitionTest(unittest.TestCase):
    def test_mention_rate_uses_valid_samples_as_denominator(self):
        result = metrics.mention_rate(2, 9)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.sample_base, 9)
        self.assertAlmostEqual(result.value, 0.2222, places=4)

    def test_mention_rate_is_insufficient_below_three_samples(self):
        result = metrics.mention_rate(1, 2)
        self.assertEqual(result.status, "insufficient_samples")
        self.assertIsNone(result.value)
        self.assertEqual(result.sample_base, 2)

    def test_avg_rank_ignores_samples_without_list_index(self):
        result = metrics.avg_rank([2, 5])
        self.assertEqual(result.status, "insufficient_samples")
        self.assertEqual(result.sample_base, 2)

    def test_avg_rank_uses_only_ranked_positions(self):
        result = metrics.avg_rank([2, 5, 8])
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.sample_base, 3)
        self.assertAlmostEqual(result.value, 5.0, places=2)

    def test_top3_denominator_is_ranked_samples(self):
        result = metrics.top_n_rate([2, 5, 9], 3)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.sample_base, 3)
        self.assertAlmostEqual(result.value, 0.3333, places=4)

    def test_sentiment_base_is_mention_samples(self):
        result = metrics.sentiment_distribution(["pos", "neu", "neg"])
        self.assertEqual(result["base"], 3)
        self.assertEqual(result["status"], "ok")
        self.assertAlmostEqual(result["neu"], 0.3333, places=4)

    def test_sentiment_is_insufficient_below_three(self):
        result = metrics.sentiment_distribution(["neu", "neu"])
        self.assertEqual(result["status"], "insufficient_samples")
        self.assertIsNone(result["neu"])
        self.assertEqual(result["base"], 2)

    def test_coverage_uses_active_questions(self):
        result = metrics.coverage_rate(2, 4)
        self.assertEqual(result.status, "ok")
        self.assertAlmostEqual(result.value, 0.5, places=4)


if __name__ == "__main__":
    unittest.main()
