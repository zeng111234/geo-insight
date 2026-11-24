"""鉴权的 HTTP 行为：401 / 302 / cookie / 登出。

用 Starlette 的 TestClient 走真实 ASGI 栈（不是直接调函数），
所以中间件顺序、cookie 下发、重定向目标这些"只有过一遍 HTTP 才暴露"的问题都能测到。
"""

import unittest
from contextlib import closing

try:
    from starlette.testclient import TestClient
except ModuleNotFoundError:  # 空 venv 里没有依赖，整模块跳过而不是报错
    raise unittest.SkipTest("环境里没有 starlette，跳过 HTTP 层测试（用系统 python 跑全套）")

from geo import db
from geo.api.app import app
from geo.services import auth_service
from geo.settings import settings

FORM = {"Content-Type": "application/x-www-form-urlencoded"}


@unittest.skipUnless(settings.auth_required, "GEO_AUTH_REQUIRED=0，鉴权被显式关闭")
class AuthHttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 测试库可能被别的用例重建过，先保证演示账号有密码
        with closing(db.connect()) as conn:
            auth_service.ensure_admin_password(conn)
        cls.client = TestClient(app)

    def setUp(self):
        self.client.cookies.clear()

    def login(self, password: str | None = None, extra: str = ""):
        body = f"email={settings.admin_email}&password={password or settings.admin_password}{extra}"
        return self.client.post(
            "/login", content=body, headers=FORM, follow_redirects=False
        )

    # --- 未登录 ---
    def test_api_without_session_is_401_json(self):
        response = self.client.get("/v1/trackers")

        self.assertEqual(response.status_code, 401)
        payload = response.json()
        self.assertEqual(payload["code"], "UNAUTHORIZED")
        # 接口给 JSON 而不是 302：调用方是脚本，跟着跳转拿到 HTML 更难排查
        self.assertIn("request_id", payload)

    def test_page_without_session_redirects_to_login(self):
        response = self.client.get("/", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["location"], "/login?next=/")

    def test_static_assets_stay_public(self):
        # 登录页自己的样式表必须能取到，否则登录页会裸奔
        self.assertEqual(self.client.get("/static/app.css").status_code, 200)
        self.assertEqual(self.client.get("/static/theme.css").status_code, 200)
        self.assertEqual(self.client.get("/health").status_code, 200)

    def test_login_page_renders(self):
        response = self.client.get("/login")

        self.assertEqual(response.status_code, 200)
        self.assertIn("GEO Insight", response.text)
        self.assertIn('action="/login"', response.text)

    # --- 登录 ---
    def test_wrong_password_does_not_set_cookie(self):
        response = self.login("definitely-wrong")

        self.assertEqual(response.status_code, 303)
        self.assertIn("/login?error=1", response.headers["location"])
        self.assertNotIn(settings.cookie_name, self.client.cookies)

    def test_successful_login_sets_hardened_cookie(self):
        response = self.login()

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/")
        cookie = response.headers["set-cookie"]
        self.assertIn(settings.cookie_name, cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=lax", cookie)
        self.assertIn("Path=/", cookie)

    def test_login_then_api_works(self):
        self.login()
        self.assertEqual(self.client.get("/v1/trackers").status_code, 200)
        self.assertEqual(self.client.get("/").status_code, 200)

    # --- 登出 ---
    def test_logout_invalidates_session(self):
        self.login()
        self.assertEqual(self.client.get("/v1/trackers").status_code, 200)

        response = self.client.post("/logout", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.client.get("/v1/trackers").status_code, 401)

    # --- 安全边界 ---
    def test_open_redirect_is_blocked(self):
        response = self.login(extra="&next=//evil.example.com")

        self.assertEqual(response.headers["location"], "/")
        # 必须落在站内
        self.assertFalse(self.login(extra="&next=https://evil.example.com").headers["location"].startswith("http"))

    def test_tenant_comes_from_session_not_header(self):
        """登录后不能靠请求头切换租户，否则任何用户都能看别人家的数据。"""
        self.login()
        normal = self.client.get("/v1/trackers").json()
        spoofed = self.client.get(
            "/v1/trackers", headers={"X-Tenant-Id": "00000000-0000-0000-0000-0000000000ff"}
        )

        self.assertEqual(spoofed.status_code, 200)
        self.assertEqual(spoofed.json(), normal)


if __name__ == "__main__":
    unittest.main()
