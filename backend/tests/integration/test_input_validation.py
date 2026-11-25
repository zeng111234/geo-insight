"""畸形输入的接口行为：不 500、错误可追溯、参数钳制生效。

为什么要有这一层：契约 fuzz（Schemathesis）装不了（PyPI 不可达），
但「畸形参数打出一个 500」是真实会发生的回归——新增接口时漏一个 try/except
就能让整页崩掉，而 HTTP 200 + node --check 都拦不住。这里用手写的代表性
畸形输入守住底线，能跑、能复现、不装任何依赖。

守的性质（都是行为，不是实现）：
1. 畸形日期 / 整数 / 枚举 → 400 VALIDATION_ERROR，带 request_id
2. 超长字符串 / 特殊字符 → 不 500（可 200 或 400，但不能是服务器错）
3. 分页参数被钳制：limit=999999999 不会真返回 9 亿条
4. 合法参数仍然 200 —— 反向保证校验没把正常请求也拦了
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
from geo.services import auth_service
from geo.settings import settings
from tests.fixtures import TRACKER, add_plan, build_minimal_db

FORM = {"Content-Type": "application/x-www-form-urlencoded"}
BASE = f"/v1/trackers/{TRACKER}"


@unittest.skipUnless(settings.auth_required, "GEO_AUTH_REQUIRED=0，鉴权被显式关闭")
class InputValidationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = Path(self.tmp.name) / "validation.db"
        with closing(build_minimal_db(path)) as conn:
            add_plan(conn)
            auth_service.ensure_admin_password(conn)
            conn.commit()

        # settings 是 frozen dataclass，db 和 app 两个模块各自持有引用，都要换
        self.patched = replace(settings, database_path=str(path))
        self._patchers = [
            patch.object(db, "settings", self.patched),
            patch.object(app_module, "settings", self.patched),
        ]
        for p in self._patchers:
            p.start()
        self.addCleanup(self._stop_patchers)
        # 不抛服务端异常：500 要作为响应返回，才能被断言"没有 500"
        self.client = TestClient(app, raise_server_exceptions=False)
        body = f"email={settings.admin_email}&password={settings.admin_password}"
        resp = self.client.post("/login", content=body, headers=FORM, follow_redirects=False)
        self.assertEqual(resp.status_code, 303, resp.text)
        self.client.headers.update({"X-CSRF-Token": self.client.cookies.get("geo_csrf", "")})

    def _stop_patchers(self):
        for p in self._patchers:
            p.stop()

    def assert_validation_error(self, resp):
        self.assertEqual(resp.status_code, 400, resp.text[:200])
        payload = resp.json()
        self.assertEqual(payload["code"], "VALIDATION_ERROR")
        self.assertIn("request_id", payload, "错误必须可追溯")

    def assert_not_server_error(self, resp):
        self.assertLess(resp.status_code, 500, resp.text[:200])

    # --- 1. 畸形日期 ---
    def test_bad_date_returns_validation_error(self):
        for bad in ("not-a-date", "9" * 5000, "2026-13-45", "2026-11-01' OR '1'='1"):
            with self.subTest(bad=bad[:20]):
                resp = self.client.get(
                    f"{BASE}/metrics/overview", params={"from": bad, "to": "2026-11-03"}
                )
                self.assert_validation_error(resp)

    # --- 2. 畸形整数 ---
    def test_bad_page_limit_returns_validation_error(self):
        for field in ("page", "limit"):
            with self.subTest(field=field):
                resp = self.client.get(f"{BASE}/answers", params={field: "abc"})
                self.assert_validation_error(resp)

    def test_bad_credits_limit_returns_validation_error(self):
        resp = self.client.get("/v1/credits", params={"limit": "NaN"})
        self.assert_validation_error(resp)

    # --- 3. 非法枚举 ---
    def test_bad_citation_scope_returns_validation_error(self):
        resp = self.client.get(f"{BASE}/answers", params={"citationScope": "everything"})
        self.assert_validation_error(resp)

    # --- 4. 超长 / 特殊字符不 500 ---
    def test_long_and_special_chars_never_return_500(self):
        cases = [
            {"question": "A" * 3000},
            {"q": "<script>alert(1)</script>"},
            {"model": "\x00\x01\x02"},
        ]
        for params in cases:
            with self.subTest(params=str(params)[:30]):
                resp = self.client.get(f"{BASE}/answers", params=params)
                self.assert_not_server_error(resp)

    # --- 5. 分页钳制：不能因 limit 极大而拖垮库 ---
    def test_huge_limit_is_clamped(self):
        resp = self.client.get(f"{BASE}/answers", params={"page": -1, "limit": 999999999})
        self.assert_not_server_error(resp)
        if resp.status_code == 200:
            self.assertLessEqual(len(resp.json()["items"]), 100, "limit 必须钳制到 100")

    # --- 6. 合法参数仍 200（反向保证校验不过头）---
    def test_valid_params_still_work(self):
        resp = self.client.get(
            f"{BASE}/metrics/overview", params={"from": "2026-11-01", "to": "2026-11-03"}
        )
        self.assertEqual(resp.status_code, 200, resp.text[:200])


if __name__ == "__main__":
    unittest.main()
