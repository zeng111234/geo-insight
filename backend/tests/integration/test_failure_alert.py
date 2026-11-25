"""T025 告警闭环：采集健康页的失败率与原因分类。

守三件事：
1. 失败率与 config_service.sampling_overview 用同一套口径（同一阈值、同一张表）；
2. 三态正确：没采过不给 0%（0/0 不是 0%）、超阈值、正常；
3. 原因分类加总 = 失败总数，未知错误码不隐藏而是标 known=false。

不读演示库 geo.db：它是运行产物，干净检出里不存在。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from geo.repositories import config_repository as config_repo
from geo.services import sampling_service
from geo.settings import settings
from tests import fixtures


class FailureAlertTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = fixtures.build_minimal_db(Path(self.tmp.name) / "alert.db")
        self.conn.execute(
            "INSERT INTO question_set(id, tenant_id, tracker_id, name, created_at) "
            "VALUES ('qs-alert', ?, ?, '告警测试集', ?)",
            (fixtures.TENANT, fixtures.TRACKER, fixtures.NOW),
        )
        self.conn.execute(
            "UPDATE tracker SET active_question_set_id='qs-alert' WHERE id=?",
            (fixtures.TRACKER,),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _plan(self):
        fixtures.add_plan(self.conn, model_keys='["doubao"]')

    def _add_run_items(self, statuses):
        """statuses: list of (status, error_code)。"""
        run_id = "run-alert-0001"
        self.conn.execute(
            """INSERT INTO run(id, tenant_id, tracker_id, trigger, status, scheduled_for)
               VALUES (?, ?, ?, 'manual', 'completed', '2026-11-10T00:00:00+08:00')""",
            (run_id, fixtures.TENANT, fixtures.TRACKER),
        )
        for i, (status, code) in enumerate(statuses):
            # run_item.status 合法值是 succeeded（不是 success）；
            # (run_id, question, model, channel, sample_idx) 唯一，用多 run + 1..3 槽位分散
            item_run = f"{run_id}-{i // 3}"
            if item_run != run_id:
                self.conn.execute(
                    """INSERT OR IGNORE INTO run(id, tenant_id, tracker_id, trigger, status, scheduled_for)
                       VALUES (?, ?, ?, 'manual', 'completed', '2026-11-10T00:00:00+08:00')""",
                    (item_run, fixtures.TENANT, fixtures.TRACKER),
                )
            self.conn.execute(
                """INSERT INTO run_item(id, run_id, question_id, model_key, channel,
                                        sample_idx, status, error_code, attempted_at)
                   VALUES (?, ?, ?, 'doubao', 'api', ?, ?, ?, ?)""",
                (
                    f"ri-alert-{i}", item_run, fixtures.QUESTION, i % 3 + 1,
                    "succeeded" if status == "success" else status, code,
                    "2026-11-10T10:00:00+08:00",
                ),
            )
        self.conn.commit()

    def _health(self):
        return sampling_service.collection_health(
            self.conn, fixtures.TRACKER, "2026-11-01", "2026-11-30"
        )

    def test_no_attempts_is_insufficient_not_zero_percent(self):
        self._plan()
        data = self._health()
        self.assertEqual(data["failure_status"], "insufficient_data")
        self.assertEqual(data["failure"]["attempted"], 0)
        self.assertIsNone(data["failure"]["rate"])
        self.assertEqual(data["failure_threshold"], settings.sampling_failure_threshold)

    def test_over_threshold_status(self):
        self._plan()
        # 10 次里 3 次失败 = 30%，高于默认 10% 阈值
        self._add_run_items([("success", None)] * 7 + [("failed", "TIMEOUT")] * 3)
        data = self._health()
        self.assertEqual(data["failure_status"], "over_threshold")
        self.assertEqual(data["failure"]["failed"], 3)
        self.assertGreater(data["failure"]["rate"], data["failure_threshold"])

    def test_ok_status_within_threshold(self):
        self._plan()
        # 100 次里 1 次失败 = 1%，在阈值内
        self._add_run_items([("success", None)] * 99 + [("failed", "TIMEOUT")])
        self.assertEqual(self._health()["failure_status"], "ok")

    def test_reasons_sum_to_failed_and_unknown_code_is_flagged(self):
        self._plan()
        self._add_run_items(
            [("failed", "TIMEOUT")] * 3
            + [("failed", "RATE_LIMITED")] * 2
            + [("failed", "AUTH_EXPIRED")]  # 种子里的旧码，不在 ErrorCode 枚举
            + [("success", None)] * 4
        )
        data = self._health()
        reasons = data["failure_reasons"]
        self.assertEqual(sum(r["count"] for r in reasons), data["failure"]["failed"])
        by_code = {r["code"]: r for r in reasons}
        self.assertEqual(by_code["TIMEOUT"]["count"], 3)
        self.assertTrue(by_code["TIMEOUT"]["known"])
        self.assertFalse(by_code["AUTH_EXPIRED"]["known"])

    def test_failure_stats_and_reasons_share_same_denominator(self):
        self._plan()
        self._add_run_items([("failed", "TIMEOUT")] * 2 + [("success", None)] * 3)
        stats = config_repo.failure_stats(
            self.conn, fixtures.TRACKER, "2026-11-01", "2026-11-30"
        )
        data = self._health()
        self.assertEqual(stats["failed"], data["failure"]["failed"])
        self.assertEqual(stats["attempted"], data["failure"]["attempted"])
        self.assertEqual(
            sum(r["count"] for r in data["failure_reasons"]), stats["failed"]
        )


if __name__ == "__main__":
    unittest.main()
