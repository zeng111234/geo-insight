"""collect.py --all 批量采集入口的契约测试。

systemd 每日任务用 --all 遍历所有监控对象，不再把对象 ID 硬编码进定时任务。
这里用 mock 的 repo.list_trackers / fill_gaps 验证三件事（不碰真 API、不花钱）：
1. GEO_COLLECT_EXCLUDE 排除的对象不会被采集（演示基线不污染）；
2. list 里没被排除的对象全都会被采集（新对象自动纳入，不用改脚本）；
3. 单个对象的 fill_gaps 抛错不影响其它对象，退出码只在真有失败时非 0。
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
    cwd = os.getcwd()
    src = str(BACKEND / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    try:
        spec = importlib.util.spec_from_file_location("geo_collect_cli_all", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        os.chdir(cwd)


def gap(inserted=1):
    return {
        "date": "2026-11-27", "planned_missing": inserted, "inserted": inserted,
        "duplicates": 0, "failed": 0, "aborted": False, "errors": [],
        "remaining_missing": 0,
    }


class CollectAllTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cli = load_cli()

    def args(self):
        return types.SimpleNamespace(model="deepseek", force=False, collector=None)

    def trackers(self):
        return [
            {"id": "demo-1", "name": "演示对象"},
            {"id": "real-1", "name": "真实对象A"},
            {"id": "new-1", "name": "新接入对象"},
        ]

    def run_all(self, exclude, fill_side_effect=None):
        calls = []
        def fake_fill(conn, *, tracker_id, **kwargs):
            calls.append(tracker_id)
            if fill_side_effect:
                return fill_side_effect(tracker_id)
            return gap()
        with mock.patch.dict(os.environ, {"GEO_COLLECT_EXCLUDE": exclude}), \
             mock.patch.object(self.cli.repo, "list_trackers", return_value=self.trackers()), \
             mock.patch.object(self.cli.collect_service, "fill_gaps", side_effect=fake_fill):
            code = self.cli.run_all(mock.MagicMock(), self.args())
        return code, calls

    def test_excluded_tracker_not_collected(self):
        """被排除的演示对象不采集，但其它对象照常采。"""
        code, calls = self.run_all("demo-1")
        self.assertEqual(code, 0)
        self.assertNotIn("demo-1", calls)
        self.assertEqual(sorted(calls), ["new-1", "real-1"])

    def test_new_tracker_automatically_included(self):
        """没被排除的对象全纳入——新加对象不用改脚本或定时任务。"""
        _, calls = self.run_all("")
        self.assertEqual(sorted(calls), ["demo-1", "new-1", "real-1"])

    def test_one_tracker_error_does_not_stop_others(self):
        """某对象没采样计划抛 ValueError，其它对象仍采，退出码 0。"""
        def fill(tid):
            if tid == "real-1":
                raise ValueError("没有启用中的问题")
            return gap()
        code, calls = self.run_all("demo-1", fill)
        self.assertEqual(code, 0)
        self.assertIn("new-1", calls)  # 后面的对象照采

    def test_failed_items_make_exit_code_two(self):
        """有采集失败项时退出码 2，systemd 记为失败可报警。"""
        def fill(tid):
            g = gap()
            g["failed"] = 1
            return g
        code, _ = self.run_all("demo-1", fill)
        self.assertEqual(code, 2)

    def test_all_excluded_returns_one(self):
        """全部被排除时明确报错，不静默返回 0。"""
        with mock.patch.dict(os.environ, {"GEO_COLLECT_EXCLUDE": "demo-1,real-1,new-1"}), \
             mock.patch.object(self.cli.repo, "list_trackers", return_value=self.trackers()):
            code = self.cli.run_all(mock.MagicMock(), self.args())
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
