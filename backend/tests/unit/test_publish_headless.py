# -*- coding: utf-8 -*-
"""上云启动参数的行为测试。

覆盖两件"服务器能不能跑起来"的硬条件:
1. worker 在 GEO_PUBLISH_HEADLESS=1 时,给浏览器渠道追加 --headless,
   且参数顺序对(sys.argv[2] 必须是 draft 路径);API 渠道永远不追加;
2. chrome_args 只在 Linux 生成沙箱参数,Windows/macOS 为空,开关可保留沙箱。

为什么用真的 dispatch_publication + 假 _run_cli:参数是在 worker 里拼的,
只测 CHANNEL_SUPPORT 的字段等于没测——字段对了但拼错位置照样崩。
"""
from __future__ import annotations

import pathlib
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

from geo.publishers import _base
from geo.services import publish_service

ARTICLE = {"title": "一篇测试文章", "body": "正文一段。"}


class _CliCapture:
    """假 CLI:立刻返回,记录每次调用的 (script, args)。"""

    def __init__(self):
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def __call__(self, script, *args, **kwargs):
        self.calls.append((script, args))
        return subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout='{"status":"published","final_url":"http://x"}', stderr="",
        )


class HeadlessArgTest(unittest.TestCase):
    def setUp(self):
        publish_service.reset_publish_slots()
        self.drafts = tempfile.TemporaryDirectory()
        self.capture = _CliCapture()
        self.patches = [
            mock.patch.object(publish_service, "_run_cli", self.capture),
            mock.patch.object(publish_service.repo, "update_publication_status"),
            mock.patch.object(publish_service.db, "connect", return_value=mock.MagicMock()),
            mock.patch.object(publish_service, "release_channel_lock"),
            mock.patch.object(publish_service, "DRAFT_DIR", pathlib.Path(self.drafts.name)),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.drafts.cleanup()
        publish_service.reset_publish_slots()

    def _publish(self, key: str, headless: bool):
        # settings 是 frozen dataclass,不能 setattr;用 dataclasses.replace 造一个
        # 只改 publish_headless 的副本,再整体替换 worker 读的 settings 模块变量
        from dataclasses import replace
        fake_settings = replace(publish_service.settings, publish_headless=headless)
        with mock.patch.object(publish_service, "settings", fake_settings):
            publish_service.dispatch_publication(f"pub-{key}", key, ARTICLE)

    def test_headless_flag_added_for_non_sohu_browser_channels(self):
        """服务器模式下 5 个渠道拿到 --headless,且在 draft 路径之后。"""
        for key in ("zhihu", "wechat", "douyin", "toutiao", "xiaohongshu"):
            self.capture.calls.clear()
            self._publish(key, headless=True)
            self.assertEqual(len(self.capture.calls), 1, f"{key} 应调用一次 CLI")
            _, args = self.capture.calls[0]
            self.assertEqual(args[0], "publish")
            self.assertTrue(args[1].endswith(f"pub-{key}.json"), "argv[2] 必须是 draft 路径")
            self.assertIn("--headless", args[2:], f"{key} 服务器模式应追加 --headless")

    def test_sohu_needs_no_flag_because_default_headless(self):
        """搜狐 main 默认就无头,追加空元组,不能画蛇添足。"""
        self._publish("sohu", headless=True)
        _, args = self.capture.calls[0]
        self.assertNotIn("--headless", args)

    def test_api_channel_never_gets_headless_flag(self):
        """百家号 CLI 不认 --headless,追加会直接报错。"""
        self._publish("baijiahao", headless=True)
        _, args = self.capture.calls[0]
        self.assertEqual(args[0], "--publish")
        self.assertNotIn("--headless", args)

    def test_headless_flag_absent_when_switch_off(self):
        """默认(本机开发)不追加,保持有头可盯的行为。"""
        self._publish("zhihu", headless=False)
        _, args = self.capture.calls[0]
        self.assertNotIn("--headless", args)


class ChromeArgsTest(unittest.TestCase):
    def test_windows_and_macos_get_no_args(self):
        for platform in ("win32", "darwin"):
            with mock.patch.object(_base.sys, "platform", platform):
                self.assertEqual(_base.chrome_args(), [])

    def test_linux_gets_both_args_by_default(self):
        with mock.patch.object(_base.sys, "platform", "linux"):
            with mock.patch.dict(_base.os.environ, {}, clear=False):
                _base.os.environ.pop("GEO_CHROME_SANDBOX", None)
                args = _base.chrome_args()
        self.assertIn("--no-sandbox", args)
        self.assertIn("--disable-dev-shm-usage", args)

    def test_sandbox_kept_when_opted_in(self):
        """非 root 容器可保留沙箱:设 GEO_CHROME_SANDBOX=1 后只加 shm 参数。"""
        with mock.patch.object(_base.sys, "platform", "linux"):
            with mock.patch.dict(_base.os.environ, {"GEO_CHROME_SANDBOX": "1"}):
                args = _base.chrome_args()
        self.assertNotIn("--no-sandbox", args)
        self.assertIn("--disable-dev-shm-usage", args)

    def test_launch_passes_args_to_context(self):
        """chrome_args 生成后真的进了 launch_persistent_context 的 kwargs。"""
        fake_pw = mock.MagicMock()
        with mock.patch.object(_base, "chrome_args", return_value=["--no-sandbox"]):
            with mock.patch.object(_base, "find_chrome", return_value=None):
                _base.launch(fake_pw, "test-channel", headless=True)
        kwargs = fake_pw.chromium.launch_persistent_context.call_args.kwargs
        self.assertEqual(kwargs["args"], ["--no-sandbox"])
        self.assertTrue(kwargs["headless"])


if __name__ == "__main__":
    unittest.main()
