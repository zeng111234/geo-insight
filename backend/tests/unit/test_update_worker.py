"""更新应用器的交换逻辑测试（update_worker.swap_tree）。

不测杀进程与重启（那是独立进程的活），只锁交换语义：
- 数据与本地配置原地不动（数据赢代码）
- 旧代码整体让位给新代码
- 成功后 .update-old 清掉（失败恢复路径靠日志，不在这里造故障）
"""

import importlib.util
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType

from geo.settings import BACKEND_DIR

WORKER_PATH = BACKEND_DIR / "scripts" / "update_worker.py"


def _load_worker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("update_worker_test", WORKER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SwapTreeTest(unittest.TestCase):
    def setUp(self):
        self.worker = _load_worker()
        self.tmp = TemporaryDirectory()
        base = Path(self.tmp.name)

        # 「正在运行的旧版本」：代码 + 数据 + 本地态
        self.root = base / "root"
        (self.root / "backend" / "src").mkdir(parents=True)
        (self.root / "backend" / "run.py").write_text("# old code", encoding="utf-8")
        (self.root / "backend" / "src" / "app.py").write_text("# old app", encoding="utf-8")
        (self.root / "backend" / "geo.db").write_bytes(b"REAL DATA")
        (self.root / "backend" / "data").mkdir()
        (self.root / "backend" / "data" / "update.local.json").write_text(
            '{"source_url": "https://example.com/update.json"}', encoding="utf-8"
        )
        (self.root / "frontend" / "static").mkdir(parents=True)
        (self.root / "frontend" / "static" / "index.html").write_text("old page", encoding="utf-8")
        (self.root / "start-team.bat").write_text("@echo off\r\n", encoding="utf-8")
        (self.root / ".git").mkdir()
        (self.root / ".git" / "config").write_text("[core]", encoding="utf-8")

        # 「暂存区的新版本」：仓库根形状
        self.staging = base / "staging"
        (self.staging / "backend" / "src").mkdir(parents=True)
        (self.staging / "backend" / "run.py").write_text("# new code", encoding="utf-8")
        (self.staging / "backend" / "src" / "app.py").write_text("# new app", encoding="utf-8")
        (self.staging / "backend" / "src" / "new_module.py").write_text("# new file", encoding="utf-8")
        (self.staging / "frontend" / "static").mkdir(parents=True)
        (self.staging / "frontend" / "static" / "index.html").write_text("new page", encoding="utf-8")
        (self.staging / "VERSION").write_text("9.9.9\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_swap_replaces_code_and_keeps_data(self):
        self.worker.swap_tree(self.root, self.staging)

        # 新代码就位，新增文件也进来
        self.assertEqual(
            (self.root / "backend" / "run.py").read_text(encoding="utf-8"), "# new code"
        )
        self.assertEqual(
            (self.root / "backend" / "src" / "app.py").read_text(encoding="utf-8"), "# new app"
        )
        self.assertEqual(
            (self.root / "backend" / "src" / "new_module.py").read_text(encoding="utf-8"),
            "# new file",
        )
        self.assertEqual(
            (self.root / "frontend" / "static" / "index.html").read_text(encoding="utf-8"),
            "new page",
        )
        self.assertEqual((self.root / "VERSION").read_text(encoding="utf-8").strip(), "9.9.9")

        # 数据与本地配置原地不动
        self.assertEqual((self.root / "backend" / "geo.db").read_bytes(), b"REAL DATA")
        self.assertIn(
            "example.com",
            (self.root / "backend" / "data" / "update.local.json").read_text(encoding="utf-8"),
        )
        # git 仓库保留（开发机上做实验也不至于把仓库拆了）
        self.assertTrue((self.root / ".git" / "config").exists())

        # 成功收尾：暂存区搬空，旧代码清掉
        self.assertFalse(list(self.staging.iterdir()))
        self.assertFalse((self.root / ".update-old").exists())

    def test_local_data_wins_when_package_illegally_contains_it(self):
        # 防御口径：包里出现 geo.db（打包端 bug）也不能覆盖本地数据
        (self.staging / "backend" / "geo.db").write_bytes(b"EVIL PAYLOAD")
        self.worker.swap_tree(self.root, self.staging)
        self.assertEqual((self.root / "backend" / "geo.db").read_bytes(), b"REAL DATA")

    def test_worker_main_as_subprocess(self):
        # update_service.apply_update 用分离进程调这套参数跑 worker——
        # CLI 契约（参数名、退出码、日志落点）要在真实 subprocess 里锁住
        import subprocess
        import sys

        result = subprocess.run(
            [
                sys.executable,
                str(WORKER_PATH),
                "--root",
                str(self.root),
                "--staging",
                str(self.staging),
                "--pid",
                "0",
                "--wait",
                "0",
                "--no-kill",
                "--no-restart",
            ],
            capture_output=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "ignore"))
        self.assertEqual(
            (self.root / "backend" / "run.py").read_text(encoding="utf-8"), "# new code"
        )
        self.assertTrue((self.root / "backend" / "data" / "update.log").exists())
