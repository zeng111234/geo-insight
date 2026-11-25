"""可观测的 HTTP 接线验证：/health 真的暴露 ops，中间件真的记请求。

纯单测只证明 RequestWindow 的逻辑对，证明不了 app.py 的中间件真的接了它。
这里走真实 ASGI 栈：打几个请求，看 /health 的 ops.requests 是否增长。
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
from geo.api.app import app, request_window
from geo.services import auth_service
from geo.settings import settings
from tests.fixtures import TRACKER, add_plan, build_minimal_db

FORM = {"Content-Type": "application/x-www-form-urlencoded"}


@unittest.skipUnless(settings.auth_required, "GEO_AUTH_REQUIRED=0，鉴权被显式关闭")
class ObservabilityHttpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = Path(self.tmp.name) / "obs.db"
        with closing(build_minimal_db(path)) as conn:
            add_plan(conn)
            auth_service.ensure_admin_password(conn)
            conn.commit()

        self.patched = replace(settings, database_path=str(path))
        self._patchers = [
            patch.object(db, "settings", self.patched),
            patch.object(app_module, "settings", self.patched),
        ]
        for p in self._patchers:
            p.start()
        self.addCleanup(self._stop_patchers)
        self.client = TestClient(app, raise_server_exceptions=False)
        body = f"email={settings.admin_email}&password={settings.admin_password}"
        resp = self.client.post("/login", content=body, headers=FORM, follow_redirects=False)
        self.assertEqual(resp.status_code, 303, resp.text)
        self.client.headers.update({"X-CSRF-Token": self.client.cookies.get("geo_csrf", "")})
        # 清掉登录等 ALE 产生的记录，从确定状态开始计数
        request_window._rows.clear()

    def _stop_patchers(self):
        for p in self._patchers:
            p.stop()

    def test_health_exposes_ops_shape(self):
        body = self.client.get("/health").json()
        self.assertIn("ops", body)
        ops = body["ops"]
        for key in ("requests", "errors", "error_rate", "slow", "slow_threshold_ms", "p95_ms"):
            self.assertIn(key, ops)

    def test_real_requests_increment_window(self):
        before = self.client.get("/health").json()["ops"]["requests"]
        # 打两个会被记的请求；/health 自己不进窗口
        self.client.get(f"/v1/trackers/{TRACKER}/metrics/overview?from=2026-11-01&to=2026-11-03")
        self.client.get("/v1/trackers")
        after = self.client.get("/health").json()["ops"]["requests"]
        self.assertEqual(after - before, 2, "两个请求都要进窗口，/health 自己不算")

    def test_401_is_not_counted_as_server_error(self):
        self.client.cookies.clear()
        before = self.client.get("/health").json()["ops"]
        self.client.get("/v1/trackers")  # 未登录 → 401
        after = self.client.get("/health").json()["ops"]
        self.assertEqual(after["requests"] - before["requests"], 1)
        self.assertEqual(after["errors"], before["errors"], "401 不算服务错误")


if __name__ == "__main__":
    unittest.main()
