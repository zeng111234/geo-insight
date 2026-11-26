"""服务器就绪判据的行为测试。

这些判据都是"本地能跑、上线才炸"的那类坑，测试用纯函数造出本地造不出的
场景（Linux 无 DISPLAY、64MB shm、演示密码上线），不真的改环境、不启动浏览器。
"""

import unittest

from scripts.doctor import (
    disk_verdict,
    headless_verdict,
    security_verdict,
    shm_verdict,
)

DEMO = "geo-demo-2026"


class SecurityVerdictTest(unittest.TestCase):
    def test_demo_password_on_public_server_fails(self):
        level, item, detail = security_verdict(DEMO, True, DEMO)
        self.assertEqual(level, "FAIL")
        self.assertEqual(item, "管理员密码")
        self.assertIn("GEO_ADMIN_PASSWORD", detail)

    def test_auth_off_fails_even_with_custom_password(self):
        # 两个坑各自独立：换了密码但忘了开鉴权，一样是裸奔
        level, item, _ = security_verdict("a-strong-password", False, DEMO)
        self.assertEqual((level, item), ("FAIL", "鉴权"))

    def test_custom_password_with_auth_passes(self):
        level, _item, _ = security_verdict("a-strong-password", True, DEMO)
        self.assertEqual(level, "OK")


class HeadlessVerdictTest(unittest.TestCase):
    def test_linux_without_display_must_be_headless(self):
        level, item, detail = headless_verdict(True, False, False)
        self.assertEqual((level, item), ("FAIL", "无头发布"))
        self.assertIn("GEO_PUBLISH_HEADLESS=1", detail)

    def test_linux_with_display_warns(self):
        level, _item, _ = headless_verdict(True, False, True)
        self.assertEqual(level, "WARN")

    def test_headless_enabled_is_ok(self):
        level, _item, _ = headless_verdict(True, True, False)
        self.assertEqual(level, "OK")

    def test_windows_stays_as_is(self):
        # 本机有头可盯，是既有行为，不能被服务器判据误伤成 FAIL
        level, _item, _ = headless_verdict(False, False, False)
        self.assertEqual(level, "OK")


class ResourceVerdictTest(unittest.TestCase):
    def test_tiny_shm_fails(self):
        level, _item, detail = shm_verdict(16)
        self.assertEqual(level, "FAIL")
        self.assertIn("shm-size", detail)

    def test_unreadable_shm_is_warn_not_fake_ok(self):
        level, _item, _ = shm_verdict(-1)
        self.assertEqual(level, "WARN")

    def test_roomy_shm_passes(self):
        self.assertEqual(shm_verdict(512)[0], "OK")

    def test_low_disk_fails(self):
        self.assertEqual(disk_verdict(0.5)[0], "FAIL")

    def test_tight_disk_warns(self):
        self.assertEqual(disk_verdict(2.0)[0], "WARN")

    def test_enough_disk_passes(self):
        self.assertEqual(disk_verdict(40.0)[0], "OK")


if __name__ == "__main__":
    unittest.main()
