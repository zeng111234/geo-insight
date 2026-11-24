import unittest

from geo import db, seed
from geo.repositories import sqlite_repository as repo
from geo.services import content_service

CONNECTED = seed.CHANNELS[0][0]
DISCONNECTED = seed.CHANNELS[2][0]


class ContentStudioTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed.seed()
        cls.conn = db.connect()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_publish_impact_matches_hand_calculation(self):
        payload = content_service.publish_impact(self.conn, seed.ART_PUBLISHED)

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["published_at"], seed.PUBLISHED_AT)
        # 发布前 11-01~11-03：9 个有效样本、2 次提及
        self.assertEqual(payload["before"]["from"], "2026-11-01")
        self.assertEqual(payload["before"]["to"], "2026-11-03")
        self.assertEqual(payload["before"]["valid_total"], 9)
        self.assertEqual(payload["before"]["mention_total"], 2)
        self.assertAlmostEqual(payload["before"]["mention_rate"]["value"], 0.2222, places=4)
        # 发布后 11-04~11-06：数据只到 11-05，5 个样本、2 次提及
        self.assertEqual(payload["after"]["from"], "2026-11-04")
        self.assertEqual(payload["after"]["valid_total"], 5)
        self.assertAlmostEqual(payload["after"]["mention_rate"]["value"], 0.4, places=4)
        self.assertEqual(payload["delta_pp"], 17.8)
        self.assertTrue(payload["comparable"])
        # 数据已覆盖 11-06 观察日，不再提示「尚未满 3 天」；但提示逻辑本身要有回归保护，
        # 下面用 14 天窗口（发布后窗口延伸到 11-30 之后）验证提示仍会出现。
        self.assertFalse(any("尚未满" in n for n in payload["notes"]))

        wide = content_service.publish_impact(self.conn, seed.ART_PUB3, window_days=14)
        self.assertEqual(wide["status"], "ok")
        self.assertTrue(any("尚未满" in n for n in wide["notes"]), wide["notes"])

    def test_unpublished_article_has_no_comparison(self):
        payload = content_service.publish_impact(self.conn, seed.ART_DRAFT)

        self.assertEqual(payload["status"], "not_published")
        self.assertIsNone(payload["before"])
        self.assertIsNone(payload["after"])
        self.assertIsNone(payload["delta_pp"])
        self.assertFalse(payload["comparable"])

    def test_missing_article_returns_not_found_status(self):
        self.assertEqual(content_service.publish_impact(self.conn, "no-such-article")["status"], "not_found")

    def test_window_with_two_samples_reports_insufficient(self):
        # 11-05 只有 2 个有效样本，低于阈值 3，必须只给状态、不给数字
        window = content_service._window(self.conn, seed.TRACKER, "2026-11-05", "2026-11-05")

        self.assertEqual(window["valid_total"], 2)
        self.assertEqual(window["mention_rate"]["status"], "insufficient_samples")
        self.assertIsNone(window["mention_rate"]["value"])

    def test_publish_is_idempotent_per_channel(self):
        article = seed.ART_REVIEW
        channel = repo.get_channel(self.conn, CONNECTED)
        created_first, existing_first = repo.create_publications(
            self.conn, article, [channel], "2026-11-11T10:00:00+08:00"
        )
        created_second, existing_second = repo.create_publications(
            self.conn, article, [channel], "2026-11-11T10:01:00+08:00"
        )

        self.assertEqual(len(created_first), 1)
        self.assertEqual(existing_first, [])
        self.assertEqual(created_second, [])
        self.assertEqual(existing_second, created_first)

        rows = self.conn.execute("SELECT COUNT(*) FROM publication WHERE article_id = ?", (article,)).fetchone()
        self.assertEqual(rows[0], 1)

    def test_saving_body_bumps_version(self):
        before = repo.get_article(self.conn, seed.ART_DRAFT)["version"]

        updated = repo.update_article(self.conn, seed.ART_DRAFT, "新标题", "新正文", "2026-11-11T11:00:00+08:00")

        self.assertEqual(updated["version"], before + 1)
        self.assertEqual(updated["title"], "新标题")
        self.assertEqual(updated["body"], "新正文")

    def test_pending_facts_sort_first(self):
        article = repo.get_article(self.conn, seed.ART_PUBLISHED)
        verdicts = [f["verdict"] for f in article["facts"]]

        self.assertIn("pending", verdicts)
        self.assertEqual(verdicts[0], "pending", "待核实事实必须排在最前")

    def test_disconnected_channel_is_not_publishable(self):
        channel = repo.get_channel(self.conn, DISCONNECTED)

        self.assertNotEqual(channel["status"], "connected")


if __name__ == "__main__":
    unittest.main()
