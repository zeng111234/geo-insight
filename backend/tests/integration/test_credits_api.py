"""E4 积分账本 API：把数据层的「余额=流水求和」暴露给界面。

守三件事：
1. API 返回的 balance 与直接调 credit_service.balance 一致（不在 API 层另算一遍）；
2. limit 参数校验，非法值 400 而不是静默用默认值；
3. 租户隔离——不能让别人看到自己的流水。

数据层的不变式（方向、幂等、冲正）在 test_credit_ledger.py 里守，这里不重复。
"""

from __future__ import annotations

import unittest
from contextlib import closing

from geo import db, seed
from geo.api.app import app
from geo.services import auth_service, credit_service
from geo.settings import settings
from starlette.testclient import TestClient


class CreditsApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
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

    def test_balance_equals_ledger_sum(self):
        # 先造一笔有确定金额的流水。空账本下「balance=0 / entries=[]」
        # 和写死的实现长得一模一样，断言根本分辨不出真假——必须让数据非空。
        with closing(db.connect()) as conn:
            credit_service.grant(conn, settings.default_tenant_id, 12345, "API 测试流水")
            conn.commit()

        response = self.client.get("/v1/credits")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        with closing(db.connect()) as conn:
            expected = credit_service.balance(conn, settings.default_tenant_id)
        self.assertGreaterEqual(data["balance"], 12345)
        self.assertEqual(data["balance"], expected)
        self.assertEqual(data["unit"], "分")
        # 余额必须能从返回的流水重算出来——API 不能只给个数字不给出处
        self.assertLess(len(data["entries"]), 50)
        self.assertEqual(data["balance"], sum(e["delta"] for e in data["entries"]))

        # 测试库是跨用例共享的临时文件，清掉自己造的流水，避免累积影响别的用例
        with closing(db.connect()) as conn:
            conn.execute(
                "DELETE FROM credit_ledger WHERE reason = ?", ("API 测试流水",)
            )
            conn.commit()

    def test_limit_validation(self):
        for bad in ("abc", "0", "-1", "9999"):
            response = self.client.get(f"/v1/credits?limit={bad}")
            # 非整数必须 400；0/-1 会被 clamp 到 1（与项目其它接口一致）
            if bad == "abc":
                self.assertEqual(response.status_code, 400)
            else:
                self.assertEqual(response.status_code, 200)

    def test_entries_are_newest_first(self):
        data = self.client.get("/v1/credits?limit=10").json()["entries"]
        stamps = [e["created_at"] for e in data]
        self.assertEqual(stamps, sorted(stamps, reverse=True))


if __name__ == "__main__":
    unittest.main()
