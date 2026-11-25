"""删除接口的审计行为（真实 ASGI 栈）。

要锁住的性质只有四条：

1. 删成功 → 一定有审计，且 ``before`` 是删之前那一行
2. 没删到 → 不能留审计（否则日志里会出现"删了但没删"的记录）
3. 别人的数据 → 404 且动不了（删除接口按 id 操作，不校验归属就是越权）
4. 审计写失败 → 删除必须回滚（"删了没记账"比不让删更糟）

第 4 条是这次改造的核心：删除与审计必须同一个事务。
"""

import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

try:
    from starlette.testclient import TestClient
except ModuleNotFoundError:  # 空 venv 里没有 starlette，整模块跳过而不是报错
    raise unittest.SkipTest("环境里没有 starlette，跳过 HTTP 层测试（用系统 python 跑全套）")

from geo import db
from geo.api import app as app_module
from geo.api.app import app
from geo.services import audit_service, auth_service
from geo.settings import settings
from tests.fixtures import BRAND, TENANT, TRACKER, add_plan, build_minimal_db

OTHER_TENANT = "00000000-0000-0000-0000-000000000099"
FORM = {"Content-Type": "application/x-www-form-urlencoded"}


@unittest.skipUnless(settings.auth_required, "GEO_AUTH_REQUIRED=0，鉴权被显式关闭")
class DeleteAuditTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.path = Path(self.tmp.name) / "delete.db"
        with closing(build_minimal_db(self.path)) as conn:
            add_plan(conn)
            auth_service.ensure_admin_password(conn)
            # 另一个租户的同名对象：用来验证按 id 删除时的归属校验
            conn.execute(
                "INSERT INTO tenant(id, name, slug, created_at) VALUES (?, '别家', 'other', '2026-11-24T09:00:00+08:00')",
                (OTHER_TENANT,),
            )
            conn.execute(
                "INSERT INTO brand(id, tenant_id, name, created_at) VALUES ('b-other', ?, '别家品牌', '2026-11-24T09:00:00+08:00')",
                (OTHER_TENANT,),
            )
            conn.execute(
                "INSERT INTO brand_alias(id, brand_id, alias, alias_type) VALUES ('a-other', 'b-other', '别家别名', 'short')"
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
        self.tmp.cleanup()

    def login(self):
        body = f"email={settings.admin_email}&password={settings.admin_password}"
        response = self.client.post("/login", content=body, headers=FORM, follow_redirects=False)
        # CSRF:写请求(DELETE/POST/PATCH)一律带登录下发的 geo_csrf
        self.client.headers.update({"X-CSRF-Token": self.client.cookies.get("geo_csrf", "")})
        self.assertEqual(response.status_code, 303, response.text)

    def audit_rows(self):
        with closing(db.connect()) as conn:
            return audit_service.recent(conn, TENANT)

    # --- 成功路径 ---
    def test_delete_alias_writes_audit_with_before_and_actor(self):
        response = self.client.delete(f"/v1/brands/{BRAND}/aliases/a1")

        self.assertEqual(response.status_code, 204, response.text)
        [row] = self.audit_rows()
        self.assertEqual(row["action"], "alias.delete")
        self.assertEqual(row["object_type"], "brand_alias")
        self.assertEqual(row["object_id"], "a1")
        self.assertEqual(row["before"]["alias"], "辽征律所")
        self.assertEqual(row["before"]["alias_type"], "short")
        self.assertIsNone(row["after"])
        # 谁删的：登录态里的用户 id，不是"system"
        self.assertIsNotNone(row["actor_id"])
        self.assertEqual(row["actor_email"], settings.admin_email)

    def test_delete_competitor_records_version_bump(self):
        response = self.client.delete(f"/v1/trackers/{TRACKER}/competitors/cp1")

        self.assertEqual(response.status_code, 204, response.text)
        [row] = self.audit_rows()
        self.assertEqual(row["action"], "competitor.delete")
        self.assertIn("盈科", row["before"]["name"])
        with closing(db.connect()) as conn:
            version = conn.execute(
                "SELECT version FROM competitor_set WHERE id = "
                "(SELECT competitor_set_id FROM tracker WHERE id = ?)",
                (TRACKER,),
            ).fetchone()[0]
        # 版本号既要在库里涨，也要能被审计读者看到，否则查日志时不知道当时是哪一版
        self.assertEqual(version, 2)
        self.assertEqual(row["before"]["set_version_after"], 2)

    # --- 失败路径不留痕 ---
    def test_delete_missing_alias_leaves_no_audit(self):
        response = self.client.delete(f"/v1/brands/{BRAND}/aliases/nope")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.audit_rows(), [])

    def test_cannot_delete_alias_of_other_tenant(self):
        response = self.client.delete(f"/v1/brands/b-other/aliases/a-other")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.audit_rows(), [])
        with closing(db.connect()) as conn:
            alive = conn.execute(
                "SELECT COUNT(*) FROM brand_alias WHERE id = 'a-other'"
            ).fetchone()[0]
        self.assertEqual(alive, 1)

    # --- 事务性 ---
    def test_audit_failure_rolls_back_delete(self):
        with patch.object(
            app_module.audit_service, "record", side_effect=RuntimeError("审计库炸了")
        ):
            response = self.client.delete(f"/v1/brands/{BRAND}/aliases/a1")

        self.assertEqual(response.status_code, 500)
        with closing(db.connect()) as conn:
            alive = conn.execute("SELECT COUNT(*) FROM brand_alias WHERE id = 'a1'").fetchone()[0]
        # 宁可删不掉，也不能删了没记账
        self.assertEqual(alive, 1)
        self.assertEqual(self.audit_rows(), [])

    # --- 鉴权前置 ---
    def test_delete_requires_login(self):
        self.client.cookies.clear()
        response = self.client.delete(f"/v1/brands/{BRAND}/aliases/a1")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.audit_rows(), [])


if __name__ == "__main__":
    unittest.main()
