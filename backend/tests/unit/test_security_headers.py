"""生产加固契约：安全响应头 + CORS 显式来源。

全栈规范要求生产禁用 CORS 通配符、加基础安全头。系统此前 allow_origins=["*"]、
无任何安全头。这里用真实 TestClient 走中间件栈守住行为（不启外部服务）：
- 三个低风险安全头出现在所有响应上（含 404 错误路径）；
- 任意外部站点拿不到 Access-Control-Allow-Origin（通配符已消除）；
- 本地回环端口仍可跨源（开发调试兼容）；
- 配了 GEO_CORS_ORIGINS 时只放行白名单来源。
不用 mock 服务层——这些是中间件真实行为，集成测就该走真栈。
"""

import dataclasses
import unittest
from unittest import mock

from starlette.testclient import TestClient

import geo.api.app as app_module
from geo.api.app import create_app
from geo.settings import settings


class SecurityHeadersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(create_app())

    def test_nosniff_on_success(self):
        r = self.client.get("/health")
        self.assertEqual(r.headers.get("x-content-type-options"), "nosniff")

    def test_referrer_policy_on_success(self):
        self.assertEqual(
            self.client.get("/health").headers.get("referrer-policy"),
            "strict-origin-when-cross-origin",
        )

    def test_frame_options_sameorigin_not_deny(self):
        """iframe 嵌入场景存在，用 SAMEORIGIN 不用 DENY。"""
        self.assertEqual(
            self.client.get("/health").headers.get("x-frame-options"), "SAMEORIGIN",
        )

    def test_headers_on_error_path(self):
        """安全头最外层，404 等错误响应也带头——不能只在成功路径有。

        用 /static 下不存在的资源造 404：普通未知路径会被鉴权重定向到 login。
        """
        r = self.client.get("/static/nope.css")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.headers.get("x-content-type-options"), "nosniff")


class CorsTest(unittest.TestCase):
    def test_untrusted_origin_gets_no_allow_header(self):
        """外部站点不再拿到 CORS 头——这是消除通配符的核心断言。"""
        r = TestClient(create_app()).get(
            "/health", headers={"Origin": "https://evil.example.com"}
        )
        self.assertNotIn("access-control-allow-origin", r.headers)

    def test_localhost_origin_allowed(self):
        """本地回环端口放行，保留本地调试跨端口打开页面的能力。"""
        r = TestClient(create_app()).get(
            "/health", headers={"Origin": "http://127.0.0.1:8123"}
        )
        self.assertEqual(r.headers.get("access-control-allow-origin"),
                         "http://127.0.0.1:8123")

    def test_explicit_whitelist_overrides_local_regex(self):
        """配了白名单后，只有白名单来源放行，本地端口不再默认通行。"""
        # settings 是 frozen dataclass，不能直接 patch 字段；造一个新实例替换模块引用。
        custom = dataclasses.replace(settings, cors_origins=("https://geo.example.com",))
        with mock.patch.object(app_module, "settings", custom):
            client = TestClient(create_app())
            allowed = client.get("/health", headers={"Origin": "https://geo.example.com"})
            self.assertEqual(allowed.headers.get("access-control-allow-origin"),
                             "https://geo.example.com")
            denied = client.get("/health", headers={"Origin": "http://127.0.0.1:8123"})
            self.assertNotIn("access-control-allow-origin", denied.headers)


if __name__ == "__main__":
    unittest.main()
