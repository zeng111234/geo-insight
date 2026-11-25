"""发布前基线校验的验收。

守的是一句判断：**基线窗口一旦过去就补不回来**，所以「样本够不够」必须在发布前
就说清楚，而不是发完了再在结案页写一句「样本不足」。
"""

from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from pathlib import Path

try:
    from starlette.testclient import TestClient
except ModuleNotFoundError:
    TestClient = None

from geo import db, seed
from geo.services import content_service
from geo.settings import settings
from tests import fixtures

ARTICLE = "a3000000-0000-0000-0000-000000000003"
Q2 = "q4000000-0000-0000-0000-000000000004"
Q2_TEXT = "沈阳合同纠纷律师"
DAYS = ("2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04",
        "2026-11-05", "2026-11-06", "2026-11-07")
WINDOW = 7


def _fill(conn, question_id: str, per_day: int) -> None:
    for day in DAYS:
        for index in range(per_day):
            fixtures.add_answer(
                conn, f"{day}T{9 + index % 12:02d}:{index:02d}:00+08:00", question_id=question_id
            )


class BaselinePreviewTest(unittest.TestCase):
    def _run(self, *, per_day: int, extra_question_samples: int = 0) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            conn = fixtures.build_minimal_db(Path(tmp) / "base.db")
            try:
                conn.execute(
                    """INSERT INTO article(id, tenant_id, tracker_id, question_id, title, body,
                                           status, version, created_at, updated_at)
                       VALUES (?, ?, ?, ?, '还没发的稿子', '正文', 'review', 1, ?, ?)""",
                    (
                        ARTICLE,
                        fixtures.TENANT,
                        fixtures.TRACKER,
                        fixtures.QUESTION,
                        fixtures.NOW,
                        fixtures.NOW,
                    ),
                )
                _fill(conn, fixtures.QUESTION, per_day)
                if extra_question_samples:
                    fixtures.add_question(conn, Q2_TEXT, question_id=Q2)
                    _fill(conn, Q2, extra_question_samples)
                conn.commit()
                return content_service.publish_impact(
                    conn, ARTICLE, WINDOW, scope="question"
                )
            finally:
                conn.close()

    def test_baseline_counts_only_the_target_question(self):
        payload = self._run(per_day=3, extra_question_samples=4)
        baseline = payload["baseline"]
        self.assertEqual(payload["status"], "not_published")
        self.assertEqual(baseline["samples"], 21)  # 7 天 x 3 条，不含另一个问题的 28 条
        self.assertEqual(baseline["from"], "2026-11-01")
        self.assertEqual(baseline["to"], "2026-11-07")

    def test_baseline_is_ready_exactly_at_threshold(self):
        payload = self._run(per_day=3)  # 21 条，正好等于下限
        self.assertTrue(payload["baseline"]["ready"])
        self.assertEqual(payload["baseline"]["shortfall"], 0)

    def test_baseline_shortfall_is_reported(self):
        payload = self._run(per_day=1)  # 7 条
        baseline = payload["baseline"]
        self.assertEqual(baseline["samples"], 7)
        self.assertFalse(baseline["ready"])
        self.assertEqual(baseline["shortfall"], 14)

    def test_baseline_without_any_sample(self):
        payload = self._run(per_day=0)
        self.assertEqual(payload["baseline"]["samples"], 0)
        self.assertEqual(payload["baseline"]["shortfall"], 21)


@unittest.skipIf(TestClient is None, "starlette TestClient 不可用")
class BaselineHttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed.seed()
        with closing(db.connect()) as conn:
            from geo.services import auth_service

            auth_service.ensure_admin_password(conn)
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

    def test_draft_article_returns_baseline(self):
        with closing(db.connect()) as conn:
            article_id = conn.execute(
                "SELECT id FROM article WHERE tracker_id = ? AND status != 'published' LIMIT 1",
                (seed.TRACKER,),
            ).fetchone()
        if article_id is None:
            self.skipTest("种子库里没有未发布的文章")
        response = self.client.get(
            f"/v1/articles/{article_id[0]}/impact?scope=question&windowDays=7"
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "not_published")
        self.assertIn("baseline", payload)
        self.assertIn("needed", payload["baseline"])


if __name__ == "__main__":
    unittest.main()
