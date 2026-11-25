"""删除监控对象：级联范围、审计留痕、跨租户保护。

这是全系统破坏性最强的接口——一次能带走几千行。三条断言缺一不可：

1. **删干净**：answer / mention / citation / question 都不能留孤儿行；
2. **留得下**：审计里有 tracker.delete，且 before 能回答"删掉了多少行"；
3. **删不动别人的**：租户不匹配时返回 404，且对方数据一行不少。
"""

import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

try:
    from starlette.testclient import TestClient
except ModuleNotFoundError:
    raise unittest.SkipTest("环境里没有 starlette，跳过 HTTP 层测试（用系统 python 跑全套）")

from geo import db
from geo.api import app as app_module
from geo.api.app import app
from geo.services import audit_service, auth_service
from geo.settings import settings
from tests import fixtures
from tests.fixtures import TENANT, TRACKER

FORM = {"Content-Type": "application/x-www-form-urlencoded"}
OTHER_TENANT = "00000000-0000-0000-0000-0000000000ff"
OTHER_TRACKER = "t0000000-0000-0000-0000-0000000000ff"

# 每张表删完之后都应该是 0 行
CASCADE_TABLES = ("answer", "mention", "citation", "answer_block", "question")


class TrackerDeleteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "delete-tracker.db"
        with closing(fixtures.build_minimal_db(self.path)) as conn:
            fixtures.add_plan(conn)
            answer_id = fixtures.add_answer(conn, "2026-11-10T09:00:00+08:00")
            fixtures.add_mention(conn, answer_id)
            conn.execute(
                """INSERT INTO citation(id, answer_id, url, url_hash, domain, media_type, position, cite_type)
                   VALUES ('cit1', ?, 'https://example.com/a', 'h1', 'example.com', 'news', 0, 'reference')""",
                (answer_id,),
            )
            auth_service.ensure_admin_password(conn)

            # 另一个租户的对象：用来验证「知道 id 也删不掉别人的」
            conn.execute(
                "INSERT INTO tenant(id, name, slug, created_at) VALUES (?, '别家', 'other', '2026-11-24T09:00:00+08:00')",
                (OTHER_TENANT,),
            )
            conn.execute(
                "INSERT INTO brand(id, tenant_id, name, created_at) "
                "VALUES ('b-other', ?, '别家品牌', '2026-11-24T09:00:00+08:00')",
                (OTHER_TENANT,),
            )
            conn.execute(
                "INSERT INTO tracker(id, tenant_id, brand_id, name, created_at, updated_at) "
                "VALUES (?, ?, 'b-other', '别家监控', '2026-11-24T09:00:00+08:00', '2026-11-24T09:00:00+08:00')",
                (OTHER_TRACKER, OTHER_TENANT),
            )
            conn.commit()

        # settings 是 frozen dataclass，只能换实例；两个模块各自持有引用，都要换
        self.patched = replace(settings, database_path=str(self.path))
        self._patchers = [
            patch.object(db, "settings", self.patched),
            patch.object(app_module, "settings", self.patched),
        ]
        for p in self._patchers:
            p.start()
        self.client = TestClient(app, raise_server_exceptions=False)
        self.login()

    def tearDown(self):
        for p in self._patchers:
            p.stop()

    def login(self):
        body = f"email={settings.admin_email}&password={settings.admin_password}"
        response = self.client.post("/login", content=body, headers=FORM, follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text)

    def count(self, table: str) -> int:
        with closing(db.connect()) as conn:
            return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def audit_rows(self):
        with closing(db.connect()) as conn:
            return audit_service.recent(conn, TENANT)

    # --- 成功路径 ---

    def test_delete_cascades_every_derived_row(self):
        # 先确认数据确实造出来了，否则「删干净」可能只是因为本来就没有
        self.assertEqual(self.count("answer"), 1)
        self.assertEqual(self.count("mention"), 1)
        self.assertEqual(self.count("citation"), 1)
        self.assertEqual(self.count("question"), 1)

        response = self.client.delete(f"/v1/trackers/{TRACKER}")

        self.assertEqual(response.status_code, 204, response.text)
        for table in CASCADE_TABLES:
            self.assertEqual(self.count(table), 0, f"{table} 还有残留行")
        self.assertEqual(self.count("tracker"), 1, "只该删掉自己的，别家的不能动")

    def test_tracker_is_gone_after_delete(self):
        self.client.delete(f"/v1/trackers/{TRACKER}")

        self.assertEqual(self.client.get(f"/v1/trackers/{TRACKER}").status_code, 404)

    def test_audit_records_how_much_was_deleted(self):
        self.client.delete(f"/v1/trackers/{TRACKER}")

        [row] = self.audit_rows()
        self.assertEqual(row["action"], "tracker.delete")
        self.assertEqual(row["object_type"], "tracker")
        self.assertEqual(row["object_id"], TRACKER)
        self.assertEqual(row["before"]["name"], "测试监控")
        # 只记「删了一个对象」不够：事后要能回答带走了多少行
        self.assertEqual(row["before"]["deleted_rows"]["answer"], 1)
        self.assertEqual(row["before"]["deleted_rows"]["mention"], 1)

    # --- 防护 ---

    def test_cannot_delete_another_tenants_tracker(self):
        response = self.client.delete(f"/v1/trackers/{OTHER_TRACKER}")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.count("tracker"), 2, "跨租户删除必须一行都不动")
        self.assertEqual(self.audit_rows(), [], "没删成功就不该留下审计")

    def test_delete_unknown_tracker_is_404(self):
        response = self.client.delete("/v1/trackers/t-does-not-exist")

        self.assertEqual(response.status_code, 404)

    def test_without_session_is_401(self):
        self.client.cookies.clear()
        response = self.client.delete(f"/v1/trackers/{TRACKER}")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.count("tracker"), 2)


if __name__ == "__main__":
    unittest.main()
