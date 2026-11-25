"""文章创建入口的验收。

断点：系统里原本只有种子文章，手写的试点稿子进不了发布/归因/判定链路。
这条测试守的是「能建出来，且问题归属不能串」——绑错问题比没有文章更糟，
因为归因会拿别人的样本算成这篇的功劳。
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

TRACKER2 = "t0000000-0000-0000-0000-000000000002"
QSET2 = "q0000000-0000-0000-0000-000000000002"
Q2 = "q1000000-0000-0000-0000-000000000002"

PAYLOAD = {
    "title": "沈阳离婚房产分割：三个可核实的判断标准",
    "body": "开头先给结论，后面用事实、日期和可核验来源支撑。",
}


class CreateArticleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = fixtures.build_minimal_db(Path(self.tmp.name) / "article.db")
        self.conn.execute(
            """INSERT INTO tracker(id, tenant_id, brand_id, competitor_set_id, name,
                                   status, settings, keywords, created_at, updated_at)
               VALUES (?, ?, ?, ?, '另一个对象', 'active', '{}', '', ?, ?)""",
            (
                TRACKER2,
                fixtures.TENANT,
                fixtures.BRAND,
                fixtures.COMPETITOR_SET,
                fixtures.NOW,
                fixtures.NOW,
            ),
        )
        self.conn.execute(
            """INSERT INTO question_set(id, tenant_id, tracker_id, name, created_at, keywords)
               VALUES (?, ?, ?, '另一套问题', ?, '')""",
            (QSET2, fixtures.TENANT, TRACKER2, fixtures.NOW),
        )
        self.conn.execute("UPDATE tracker SET active_question_set_id = ? WHERE id = ?", (QSET2, TRACKER2))
        self.conn.execute(
            """INSERT INTO question(id, tenant_id, question_set_id, text, normalized_text,
                                    tier, intent, status, created_at)
               VALUES (?, ?, ?, '别的对象的题', '别的对象的题', 'core', 'commercial', 'active', ?)""",
            (Q2, fixtures.TENANT, QSET2, fixtures.NOW),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_create_draft_with_target_question(self):
        payload = {**PAYLOAD, "question_id": fixtures.QUESTION}
        article = content_service.create_article(
            self.conn, fixtures.TRACKER, fixtures.TENANT, payload
        )
        self.assertEqual(article["status"], "draft")
        self.assertEqual(article["version"], 1)
        self.assertEqual(article["question_id"], fixtures.QUESTION)
        self.assertEqual(article["tenant_id"], fixtures.TENANT)
        row = self.conn.execute(
            "SELECT title, body, tracker_id FROM article WHERE id = ?", (article["id"],)
        ).fetchone()
        self.assertEqual(row["title"], PAYLOAD["title"])
        self.assertEqual(row["body"], PAYLOAD["body"])
        self.assertEqual(row["tracker_id"], fixtures.TRACKER)

    def test_question_can_be_bound_later(self):
        article = content_service.create_article(
            self.conn, fixtures.TRACKER, fixtures.TENANT, PAYLOAD
        )
        self.assertIsNone(article["question_id"])

    def test_empty_fields_are_rejected(self):
        for payload in (
            {"title": "", "body": "正文"},
            {"title": "标题", "body": "   "},
            {"title": "x" * 201, "body": "正文"},
        ):
            with self.assertRaises(content_service.ArticleInputError):
                content_service.create_article(
                    self.conn, fixtures.TRACKER, fixtures.TENANT, payload
                )

    def test_question_from_another_tracker_is_rejected(self):
        payload = {**PAYLOAD, "question_id": Q2}
        with self.assertRaises(content_service.QuestionNotFoundError):
            content_service.create_article(
                self.conn, fixtures.TRACKER, fixtures.TENANT, payload
            )
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM article").fetchone()[0], 0)

    def test_unknown_question_is_rejected(self):
        payload = {**PAYLOAD, "question_id": "q-does-not-exist"}
        with self.assertRaises(content_service.QuestionNotFoundError):
            content_service.create_article(
                self.conn, fixtures.TRACKER, fixtures.TENANT, payload
            )


@unittest.skipIf(TestClient is None, "starlette TestClient 不可用")
class CreateArticleHttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed.seed()
        with closing(db.connect()) as conn:
            from geo.services import auth_service

            auth_service.ensure_admin_password(conn)
            question_id = conn.execute(
                "SELECT q.id FROM question q JOIN question_set qs ON qs.id = q.question_set_id "
                "JOIN tracker t ON t.active_question_set_id = qs.id LIMIT 1"
            ).fetchone()[0]
        cls.question_id = question_id
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

    def test_create_then_list(self):
        response = self.client.post(
            f"/v1/trackers/{seed.TRACKER}/articles",
            json={**PAYLOAD, "question_id": self.question_id},
        )
        self.assertEqual(response.status_code, 201, response.text)
        created = response.json()
        self.assertEqual(created["status"], "draft")
        listed = self.client.get(f"/v1/trackers/{seed.TRACKER}/articles").json()
        self.assertTrue(any(a["id"] == created["id"] for a in listed["items"]))

    def test_bad_input_is_rejected(self):
        bad = self.client.post(f"/v1/trackers/{seed.TRACKER}/articles", json={"title": "", "body": "x"})
        self.assertEqual(bad.status_code, 400)
        missing = self.client.post(
            f"/v1/trackers/{seed.TRACKER}/articles",
            json={**PAYLOAD, "question_id": "q-does-not-exist"},
        )
        self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()
