"""E3 文章六阶段：状态落库、刷新恢复、推进与打回。

守四件事：
1. 六阶段清单只有一份权威定义，顺序固定；
2. 阶段状态写库，不是前端 localStorage——重新读就是上次的状态；
3. 同时只有一个 active，完成当前阶段会推进到下一阶段；
4. 已完成的阶段不能直接点回退（要重走得显式走完整流程）。

不读演示库 geo.db：它是运行产物，干净检出里不存在。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from geo.domain import article_stage as stages
from geo.services import article_stage_service as svc
from tests import fixtures


class StageCatalogTest(unittest.TestCase):
    def test_six_stages_in_order(self):
        keys = [k for k, _ in stages.STAGES]
        self.assertEqual(
            keys, ["research", "evidence", "draft", "review", "channel", "publish"]
        )
        self.assertEqual(len(stages.STAGE_LABELS), 6)
        self.assertIsNone(stages.next_stage("publish"))
        self.assertEqual(stages.next_stage("draft"), "review")

    def test_published_articles_start_all_done(self):
        for key, _ in stages.STAGES:
            self.assertEqual(stages.initial_status("published", key), stages.DONE)
        self.assertEqual(stages.initial_status("draft", "research"), stages.ACTIVE)
        self.assertEqual(stages.initial_status("draft", "evidence"), stages.PENDING)


class ArticleStageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = fixtures.build_minimal_db(Path(self.tmp.name) / "stages.db")
        self.article_id = "art-stage-0001"
        self._add_article(self.article_id, "draft")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _add_article(self, article_id, status):
        now = fixtures.NOW
        self.conn.execute(
            """INSERT INTO article(id, tenant_id, tracker_id, title, body, status,
                                   version, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            (
                article_id, fixtures.TENANT, fixtures.TRACKER,
                f"文章 {article_id}", "正文", status, now, now,
            ),
        )
        self.conn.commit()

    def test_initial_state_has_one_active(self):
        state = svc.get_stages(self.conn, self.article_id)
        # get_stages 是纯读，不会写库；阶段由 create_article / ensure_stages 初始化
        self.assertEqual(state["total"], 6)
        self.assertIsNone(state["current"])
        svc.ensure_stages(self.conn, self.article_id)
        state = svc.get_stages(self.conn, self.article_id)
        self.assertEqual(state["current"], "research")
        self.assertEqual(state["completed"], 0)
        self.assertEqual(
            len([x for x in state["items"] if x["status"] == stages.ACTIVE]), 1
        )

    def test_complete_advances_to_next_stage(self):
        svc.ensure_stages(self.conn, self.article_id)
        self.assertEqual(svc.complete_current(self.conn, self.article_id)["current"], "evidence")
        self.assertEqual(svc.complete_current(self.conn, self.article_id)["current"], "draft")
        state = svc.get_stages(self.conn, self.article_id)
        self.assertEqual(state["completed"], 2)
        # 刷新后重读还是同一状态
        self.assertEqual(svc.get_stages(self.conn, self.article_id)["current"], "draft")

    def test_set_current_rejects_done_stage(self):
        svc.ensure_stages(self.conn, self.article_id)
        svc.complete_current(self.conn, self.article_id)  # research done
        with self.assertRaises(svc.StageError) as ctx:
            svc.set_current(self.conn, self.article_id, "research")
        self.assertEqual(ctx.exception.code, "STAGE_DONE")

    def test_set_current_allows_going_back_to_pending(self):
        svc.ensure_stages(self.conn, self.article_id)
        for _ in range(3):
            svc.complete_current(self.conn, self.article_id)
        self.assertEqual(svc.get_stages(self.conn, self.article_id)["current"], "review")
        # 打回到未开始的渠道稿阶段：该阶段是 pending，允许
        self.assertEqual(svc.set_current(self.conn, self.article_id, "channel")["current"], "channel")
        self.assertEqual(
            len([x for x in svc.get_stages(self.conn, self.article_id)["items"]
                 if x["status"] == stages.ACTIVE]), 1
        )

    def test_unknown_stage_rejected(self):
        svc.ensure_stages(self.conn, self.article_id)
        with self.assertRaises(svc.StageError) as ctx:
            svc.set_current(self.conn, self.article_id, "bogus")
        self.assertEqual(ctx.exception.code, "UNKNOWN_STAGE")

    def test_all_six_complete_leaves_no_current(self):
        svc.ensure_stages(self.conn, self.article_id)
        for _ in range(6):
            state = svc.complete_current(self.conn, self.article_id)
        self.assertIsNone(state["current"])
        self.assertEqual(state["completed"], 6)


class StageHttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from contextlib import closing

        from geo import db, seed
        from geo.api.app import app
        from geo.services import auth_service
        from geo.settings import settings
        from starlette.testclient import TestClient

        seed.seed()
        with closing(db.connect()) as conn:
            auth_service.ensure_admin_password(conn)
        cls.client = TestClient(app)
        body = f"email={settings.admin_email}&password={settings.admin_password}"
        cls.client.post(
            "/login",
            content=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
        )
        cls.client.headers.update({"X-CSRF-Token": cls.client.cookies.get("geo_csrf", "")})
        from geo import seed as seed_module

        cls.article_id = seed_module.ART_DRAFT

    def test_get_and_complete_over_http(self):
        response = self.client.get(f"/v1/articles/{self.article_id}/stages")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["items"]), 6)

        response = self.client.post(
            f"/v1/articles/{self.article_id}/stages",
            json={"action": "complete"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["completed"], 1)

    def test_unknown_stage_over_http(self):
        response = self.client.post(
            f"/v1/articles/{self.article_id}/stages",
            json={"action": "set", "stage": "bogus"},
        )
        self.assertEqual(response.status_code, 404)


class StageMigrationTest(unittest.TestCase):
    def test_migration_initializes_existing_articles(self):
        import importlib.util

        from geo import migrate

        path = (
            Path(__file__).resolve().parents[2]
            / "migrations"
            / "018_article_stage.py"
        )
        spec = importlib.util.spec_from_file_location("m018", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as tmp:
            conn = fixtures.build_minimal_db(Path(tmp) / "old.db")
            # 去掉刚建的阶段行，再造一篇已发布、一篇草稿，模拟迁移前的老库
            conn.execute("DELETE FROM article_stage")
            now = fixtures.NOW
            for article_id, status in (("art-old-pub", "published"), ("art-old-draft", "draft")):
                conn.execute(
                    """INSERT INTO article(id, tenant_id, tracker_id, title, body, status,
                                           version, created_at, updated_at)
                       VALUES (?, ?, ?, ?, '正文', ?, 1, ?, ?)""",
                    (article_id, fixtures.TENANT, fixtures.TRACKER, article_id, status, now, now),
                )
            conn.commit()
            module.apply(conn)
            pub = dict(
                conn.execute(
                    "SELECT stage, status FROM article_stage WHERE article_id='art-old-pub'"
                ).fetchall()
            )
            draft = dict(
                conn.execute(
                    "SELECT stage, status FROM article_stage WHERE article_id='art-old-draft'"
                ).fetchall()
            )
            self.assertEqual(len(pub), 6)
            self.assertTrue(all(v == stages.DONE for v in pub.values()))
            self.assertEqual(draft["research"], stages.ACTIVE)
            self.assertEqual(draft["publish"], stages.PENDING)
            # 重跑不重复插
            module.apply(conn)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM article_stage").fetchone()[0], 12
            )
            conn.close()


if __name__ == "__main__":
    unittest.main()
