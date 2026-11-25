"""采集任务列表 API：真实 run 记录 + 从 run_item 聚合的条目数。"""

import unittest
from contextlib import closing

from geo import db, seed
from geo.services import auth_service
from geo.settings import settings
from starlette.testclient import TestClient


class RunsApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed.seed()
        with closing(db.connect()) as conn:
            auth_service.ensure_admin_password(conn)
            cls.tracker_id = conn.execute("SELECT id FROM tracker LIMIT 1").fetchone()[0]
        from geo.api.app import app

        cls.client = TestClient(app)
        body = f"email={settings.admin_email}&password={settings.admin_password}"
        cls.client.post(
            "/login",
            content=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
        )
        cls.client.headers.update({"X-CSRF-Token": cls.client.cookies.get("geo_csrf", "")})

    def test_runs_report_real_item_counts(self):
        """任务列表的条目数来自 run_item 真聚合，不是 run.stats 的空 {}。"""
        response = self.client.get(
            f"/v1/trackers/{self.tracker_id}/runs?from=2026-11-01&to=2026-11-30"
        )
        self.assertEqual(response.status_code, 200)
        items = response.json()["items"]
        self.assertGreater(len(items), 0)
        row = items[0]
        for key in ("id", "trigger", "status", "scheduled_for", "total", "succeeded", "failed"):
            self.assertIn(key, row)
        self.assertTrue(all(x["succeeded"] + x["failed"] <= x["total"] for x in items))
        self.assertGreater(sum(x["total"] for x in items), 0)
        # 与库里 run_item 实数对得上（同一批 run）
        with closing(db.connect()) as conn:
            ids = [x["id"] for x in items]
            marks = ",".join("?" for _ in ids)
            real = conn.execute(
                f"SELECT COUNT(*) n, SUM(CASE WHEN status='succeeded' THEN 1 ELSE 0 END) s, "
                f"SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) f "
                f"FROM run_item WHERE run_id IN ({marks})", ids,
            ).fetchone()
        self.assertEqual(sum(x["total"] for x in items), real["n"])
        self.assertEqual(sum(x["succeeded"] for x in items), real["s"])
        self.assertEqual(sum(x["failed"] for x in items), real["f"])

    def test_no_runs_returns_empty_not_fabricated(self):
        """没有 run 的周期返回空数组，不编进度数字。"""
        response = self.client.get(
            f"/v1/trackers/{self.tracker_id}/runs?from=2020-01-01&to=2020-01-02"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"], [])

    def test_limit_validation(self):
        response = self.client.get(f"/v1/trackers/{self.tracker_id}/runs?limit=abc")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "VALIDATION_ERROR")
        response = self.client.get(f"/v1/trackers/{self.tracker_id}/runs?limit=999999")
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(response.json()["items"]), 200)

    def test_unknown_tracker_is_404(self):
        response = self.client.get("/v1/trackers/not-a-tracker/runs")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
