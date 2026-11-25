"""E2:模型 × 端 16 渠道矩阵。

守三件事（决策 009 验收点）：
1. 清单固定 16 项，其中 13 项 available=false 且带「未接入」；
2. 同日同模型不同端必须分开统计，不能合成一行；
3. 老数据回填 client='web'，既有口径不因为加列而改变。

不读演示库 geo.db：它是运行产物，干净检出里不存在。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from contextlib import closing

from geo.domain import channels as channels_domain
from geo.repositories import sqlite_repository as repo
from geo.services import metric_service
from tests import fixtures


class ChannelCatalogTest(unittest.TestCase):
    def test_catalog_has_16_channels_and_13_unavailable(self):
        items = channels_domain.channel_list()
        self.assertEqual(len(items), 16)
        self.assertEqual(len({x["key"] for x in items}), 16)
        self.assertEqual(sum(1 for x in items if x["available"]), 3)
        self.assertEqual(sum(1 for x in items if not x["available"]), 13)
        for item in items:
            if not item["available"]:
                self.assertEqual(item["note"], "未接入")
            self.assertIn(item["client"], ("web", "mobile"))

    def test_catalog_is_a_copy(self):
        first = channels_domain.channel_list()
        first[0]["available"] = False
        self.assertTrue(channels_domain.channel_list()[0]["available"])


class AnswerClientMigrationTest(unittest.TestCase):
    """迁移本身要单独验：新插入的默认值证明不了老行被回填。"""

    def test_migration_backfills_existing_rows_and_is_repeatable(self):
        import importlib.util
        import sqlite3

        from geo import migrate

        migration = (
            Path(__file__).resolve().parents[2]
            / "migrations"
            / "016_answer_client.py"
        )
        spec = importlib.util.spec_from_file_location("m016", migration)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as tmp:
            conn = sqlite3.connect(Path(tmp) / "old.db")
            conn.execute("CREATE TABLE answer(id TEXT, model_key TEXT)")
            conn.execute("INSERT INTO answer VALUES ('old1', 'doubao')")
            conn.commit()
            module.apply(conn)
            self.assertEqual(
                conn.execute("SELECT client FROM answer WHERE id='old1'").fetchone()[0],
                "web",
            )
            # 中途失败重跑不能炸（迁移链的硬规则）
            module.apply(conn)
            conn.close()


class ChannelMatrixTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = fixtures.build_minimal_db(Path(self.tmp.name) / "channels.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _matrix(self):
        return metric_service.build_channel_matrix(
            self.conn, fixtures.TRACKER, "2026-11-01", "2026-11-30"
        )

    def test_old_answers_default_to_web(self):
        # 夹具不传 client，走的就是老数据路径
        answer_id = fixtures.add_answer(
            self.conn, "2026-11-10T10:00:00+08:00", model_key="deepseek"
        )
        row = self.conn.execute(
            "SELECT client FROM answer WHERE id = ?", (answer_id,)
        ).fetchone()
        self.assertEqual(row["client"], "web")
        item = next(
            x for x in self._matrix()["items"] if x["key"] == "deepseek:web"
        )
        self.assertEqual(item["samples"], 1)

    def test_same_day_same_model_different_clients_are_separate(self):
        day = "2026-11-12"
        web = fixtures.add_answer(
            self.conn, f"{day}T10:00:00+08:00", model_key="doubao", client="web"
        )
        mobile = fixtures.add_answer(
            self.conn, f"{day}T10:05:00+08:00", model_key="doubao", client="mobile"
        )
        fixtures.add_mention(self.conn, web, position=1)
        matrix = self._matrix()
        by_key = {x["key"]: x for x in matrix["items"]}
        self.assertEqual(by_key["doubao:web"]["samples"], 1)
        self.assertEqual(by_key["doubao:web"]["mentions"], 1)
        self.assertEqual(by_key["doubao:mobile"]["samples"], 1)
        self.assertEqual(by_key["doubao:mobile"]["mentions"], 0)

    def test_unavailable_channels_are_explicit_even_with_stray_data(self):
        # 即使库里偶然有一条未接入渠道的导入数据，也不能冒充「采集线通了」
        stray = fixtures.add_answer(
            self.conn, "2026-11-12T11:00:00+08:00", model_key="qwen", client="web"
        )
        fixtures.add_mention(self.conn, stray)
        item = next(x for x in self._matrix()["items"] if x["key"] == "qwen:web")
        self.assertFalse(item["available"])
        self.assertEqual(item["status"], "not_available")
        self.assertEqual(item["note"], "未接入")
        self.assertEqual(item["samples"], 1)  # 数字照实报，不藏

    def test_matrix_counts_and_window(self):
        for i in range(3):
            fixtures.add_answer(
                self.conn, f"2026-11-13T1{i}:00:00+08:00", model_key="kimi"
            )
        matrix = self._matrix()
        self.assertEqual(matrix["total"], 16)
        self.assertEqual(matrix["available"], 3)
        self.assertEqual(matrix["unavailable"], 13)
        self.assertEqual(matrix["window"], {"from": "2026-11-01", "to": "2026-11-30"})
        self.assertEqual(
            len([x for x in matrix["items"] if x["status"] == "not_available"]), 13
        )


class ChannelHttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from geo import db, seed
        from geo.services import auth_service
        from geo.settings import settings
        from geo.api.app import app
        from starlette.testclient import TestClient

        seed.seed()
        cls.tracker_id = seed.TRACKER  # seed 的对象 ID，不是 fixtures 的临时库 ID
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

    def test_meta_channels_over_http(self):
        response = self.client.get("/v1/meta/channels")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["total"], 16)
        self.assertEqual(data["unavailable"], 13)
        self.assertEqual(
            len([x for x in data["items"] if x["note"] == "未接入"]), 13
        )

    def test_matrix_over_http(self):
        response = self.client.get(
            f"/v1/trackers/{self.tracker_id}/metrics/channels"
            "?from=2026-11-01&to=2026-11-30"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["items"]), 16)
        self.assertEqual(data["unavailable"], 13)


if __name__ == "__main__":
    unittest.main()
