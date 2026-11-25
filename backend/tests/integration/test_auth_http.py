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
        # 登录限流是进程内状态,错误密码的用例会累计计数,每条用例前清零
        auth_service._reset_login_failures_for_tests()

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

    def test_login_hint_is_for_viewers_not_operators(self):
        """登录页是演示观众看到的，不是运维文档。

        环境变量名、README 路径、重启后重登这类实现细节出现在登录页上，
        观众既用不上又会困惑；观众需要知道的只有「密码去哪找」。
        """
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 200)
        self.assertIn("密码见启动时终端打印的提示", response.text)
        for internal in ("GEO_ADMIN_PASSWORD", "README", "服务重启后需要重新登录"):
            self.assertNotIn(internal, response.text)

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

    def test_https_login_sets_cross_site_cookie(self):
        """线上（HTTPS，反向代理后面）必须发 SameSite=None; Secure。

        为什么单列一条：发布后的应用是在应用卡的 iframe 里打开的，
        SameSite=lax 的 cookie 在第三方上下文里浏览器根本不发送，
        于是登录成功也会立刻被弹回登录页，用户看到的是一页空白。
        本地 http 仍用 lax——浏览器不接受给 http 设 None。
        """
        body = f"email={settings.admin_email}&password={settings.admin_password}"
        response = self.client.post(
            "/login",
            content=body,
            headers={**FORM, "x-forwarded-proto": "https"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        cookie = response.headers["set-cookie"]
        self.assertIn("samesite=none", cookie.lower())
        self.assertIn("secure", cookie.lower())

    def test_http_login_keeps_lax(self):
        """本地双击 start.bat 走 http，cookie 必须是 lax 且不带 Secure。

        带 Secure 的 cookie 在 http 下会被浏览器直接丢掉，本地就登不进去了。
        """
        response = self.login()

        cookie = response.headers["set-cookie"]
        self.assertIn("samesite=lax", cookie.lower())
        # 只看属性段，避免 token 本身碰巧含这个词造成误判
        self.assertNotIn("; secure", cookie.lower())

    def test_login_then_api_works(self):
        self.login()
        self.assertEqual(self.client.get("/v1/trackers").status_code, 200)
        self.assertEqual(self.client.get("/").status_code, 200)

    # --- 登出 ---
    def test_logout_invalidates_session(self):
        self.login()
        self.assertEqual(self.client.get("/v1/trackers").status_code, 200)

        response = self.client.post("/logout", follow_redirects=False)

        self.assertEqual(response.status_code, 403)  # 缺 X-CSRF-Token,先被 CSRF 拒
        self.assertEqual(response.json()["code"], "CSRF_REJECTED")
        self.assertEqual(self.client.get("/v1/trackers").status_code, 200)  # 会话还在

        token = self.client.cookies.get("geo_csrf")
        response = self.client.post("/logout", headers={"X-CSRF-Token": token}, follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.client.get("/v1/trackers").status_code, 401)

    # --- CSRF(003 FR-005,补决策 010 的洞) ---
    def test_write_without_csrf_token_is_403(self):
        # SameSite=None 的线上环境里,拿到会话 cookie 的第三方页面发起的写请求
        # 没有 token 头——必须 403,数据不动
        self.login()
        response = self.client.post("/logout", follow_redirects=False)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "CSRF_REJECTED")
        self.assertEqual(self.client.get("/v1/trackers").status_code, 200)

    def test_write_with_wrong_csrf_token_is_403(self):
        self.login()
        response = self.client.post("/logout", headers={"X-CSRF-Token": "forged"}, follow_redirects=False)

        self.assertEqual(response.status_code, 403)

    def test_csrf_cookie_is_readable_by_js_and_matches_session(self):
        # 双提交的前提:geo_csrf 非 HttpOnly(JS 读得到)且与会话 token 一致
        # 直接看原始 Set-Cookie 头,同时用这次登录的会话比对,不经过 login() 两次
        response = self.client.post(
            "/login",
            content=f"email={settings.admin_email}&password={settings.admin_password}",
            headers=FORM,
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        raw = response.headers.get("set-cookie", "")
        # httpx 会把多个 set-cookie 合并;geo_csrf 那一段不能带 HttpOnly
        self.assertIn("geo_csrf=", raw)
        csrf_segment = [seg for seg in raw.split(", ") if seg.startswith("geo_csrf=")]
        self.assertTrue(csrf_segment, raw)
        self.assertNotIn("HttpOnly", csrf_segment[0])
        cookie = self.client.cookies.get("geo_csrf")
        session = auth_service.get_session(self.client.cookies.get(settings.cookie_name))
        self.assertEqual(session["csrf_token"], cookie)

    # --- 登录限流(003 FR-006) ---
    def test_login_rate_limit_locks_after_consecutive_failures(self):
        for _ in range(settings.login_max_failures):
            self.login(password="wrong-password")
        # 锁定期内:正确密码也进不去,重定向带 error=locked
        response = self.login()
        self.assertIn("error=locked", response.headers.get("location", ""))
        # 未登录态保持
        self.assertEqual(self.client.get("/v1/trackers").status_code, 401)

    def test_login_lockout_expires_and_allows_login(self):
        old = settings.login_lockout_s
        object.__setattr__(settings, "login_lockout_s", 1)
        try:
            for _ in range(settings.login_max_failures):
                self.login(password="wrong-password")
            self.assertIn("error=locked", self.login().headers.get("location", ""))
            import time as _time

            _time.sleep(1.2)  # 过锁定期
            response = self.login()
            self.assertEqual(response.status_code, 303)
            self.assertNotIn("error", response.headers.get("location", ""))
            self.assertEqual(self.client.get("/v1/trackers").status_code, 200)
        finally:
            object.__setattr__(settings, "login_lockout_s", old)

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
