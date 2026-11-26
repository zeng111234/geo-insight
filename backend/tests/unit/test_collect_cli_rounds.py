"""采集 CLI 的 --rounds 行为测试：用假 fill_gaps 验证循环真的会停。

不碰真实 API，也不碰数据库。守的是"定时任务能靠退出码收工"这件事——
原来 --loop 只能靠人按 Ctrl+C，脚本调用时既不知道跑了几轮，也拿不到明确结束信号。
"""

import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parents[2]
SCRIPT = BACKEND / "scripts" / "collect.py"


def load_cli():
    """脚本顶层会 chdir 到 backend，加载后还原 cwd，避免污染别的测试。"""
    cwd = os.getcwd()
    src = str(BACKEND / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    try:
        spec = importlib.util.spec_from_file_location("geo_collect_cli_rounds", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        os.chdir(cwd)


def empty_gap():
    return {
        "date": "2026-11-26",
        "planned_missing": 6,
        "inserted": 6,
        "duplicates": 0,
        "failed": 0,
        "aborted": False,
        "errors": [],
        "remaining_missing": 0,
    }


class RoundsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cli = load_cli()

    def args(self, rounds: int):
        return types.SimpleNamespace(
            rounds=rounds, interval=0, model="deepseek", force=False, collector=None
        )

    def run_with(self, rounds: int, fake, allowed: int):
        calls = []

        def counted(*a, **k):
            calls.append(1)
            if len(calls) > allowed:
                raise AssertionError(f"多跑了第 {len(calls)} 轮（--rounds {rounds}）")
            return fake(*a, **k)

        with mock.patch.object(self.cli.collect_service, "fill_gaps", side_effect=counted), \
             mock.patch("time.sleep"):
            code = self.cli.run_loop(mock.MagicMock(), "tracker-1", self.args(rounds))
        return code, len(calls)

    def test_rounds_one_runs_exactly_once(self):
        code, n = self.run_with(1, lambda *a, **k: empty_gap(), 1)
        self.assertEqual((code, n), (0, 1))

    def test_rounds_two_runs_twice(self):
        code, n = self.run_with(2, lambda *a, **k: empty_gap(), 2)
        self.assertEqual((code, n), (0, 2))

    def test_zero_still_loops_until_interrupt(self):
        # 默认 0 必须保持"一直跑"，不能被新参数误伤。第 3 轮模拟 Ctrl+C。
        seen = []

        def interrupt_on_third(*a, **k):
            seen.append(1)
            if len(seen) >= 3:
                raise KeyboardInterrupt
            return empty_gap()

        code, n = self.run_with(0, interrupt_on_third, 3)
        self.assertEqual((code, n), (0, 3))


if __name__ == "__main__":
    unittest.main()
