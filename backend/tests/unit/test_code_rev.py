"""服务代码版本标识：进程启动时读 HEAD，非 git 环境不崩、不造假。"""

import subprocess
import unittest
from unittest.mock import patch

from geo import code_rev


class CodeRevTest(unittest.TestCase):
    def test_returns_short_head(self):
        # 真实检出里必须读到一个短 hash，不能是空串或 unknown
        self.assertNotEqual(code_rev.code_rev(), "unknown")
        self.assertLessEqual(len(code_rev.code_rev()), 12)

    def test_reads_from_project_root_not_cwd(self):
        """换 cwd 不影响结果——两台电脑轮流跑，启动方式各异。"""
        called = {}

        def fake_run(cmd, **kwargs):
            called["cwd"] = kwargs.get("cwd")
            return subprocess.CompletedProcess(cmd, 0, "abc1234\n", "")

        with patch.object(code_rev.subprocess, "run", side_effect=fake_run):
            code_rev.code_rev.cache_clear()
            try:
                self.assertEqual(code_rev.code_rev(), "abc1234")
            finally:
                code_rev.code_rev.cache_clear()
        self.assertIsNotNone(called["cwd"], "必须显式传 cwd，不能依赖进程当前目录")

    def test_non_git_checkout_returns_unknown_without_crashing(self):
        with patch.object(code_rev.subprocess, "run", side_effect=FileNotFoundError("no git")):
            code_rev.code_rev.cache_clear()
            try:
                self.assertEqual(code_rev.code_rev(), "unknown")
            finally:
                code_rev.code_rev.cache_clear()

    def test_git_failure_returns_unknown(self):
        def fail(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 128, "", "not a git repo")

        with patch.object(code_rev.subprocess, "run", side_effect=fail):
            code_rev.code_rev.cache_clear()
            try:
                self.assertEqual(code_rev.code_rev(), "unknown")
            finally:
                code_rev.code_rev.cache_clear()

    def test_cached_across_calls(self):
        """进程启动读一次即可；运行期间 HEAD 变了也不改返回值。"""
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(1)
            return subprocess.CompletedProcess(cmd, 0, "deadbeef\n", "")

        with patch.object(code_rev.subprocess, "run", side_effect=fake_run):
            code_rev.code_rev.cache_clear()
            try:
                self.assertEqual(code_rev.code_rev(), "deadbeef")
                self.assertEqual(code_rev.code_rev(), "deadbeef")
                self.assertEqual(len(calls), 1)
            finally:
                code_rev.code_rev.cache_clear()


if __name__ == "__main__":
    unittest.main()
