"""配置里的路径必须和「从哪个目录启动」无关。

为什么专门守这条：2026-11-25 实际踩到——`database_path` 默认值是相对路径 "geo.db"，
而 start.bat 会先 `cd backend` 再启动，命令行调试则常在项目根启动。结果是同一台机器上
长出两个库（backend/geo.db 和 geo/geo.db），迁移版本一个 v10 一个没有版本表。
用户看到的现象是「数据丢了」，实际上是看错了文件。

这个仓库要在两台电脑上轮流跑，换机器时 cwd 更难保证一致，所以把「路径不跟 cwd 走」
变成断言，而不是写在注释里靠人记。
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "backend" / "src"
# 用绝对路径插 sys.path：这个探针会在不同 cwd 下执行，相对 'src' 只在 backend/ 里成立
PROBE = (
    "import sys; sys.path.insert(0, " + repr(str(SRC)) + "); "
    "from geo.settings import settings; print(settings.database_path)"
)


def _run_from(cwd: Path) -> str:
    env = {k: v for k, v in os.environ.items() if k != "GEO_DB_PATH"}
    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


class SettingsPathTest(unittest.TestCase):
    def test_database_path_same_from_any_cwd(self):
        """在项目根和 backend/ 两个目录启动，必须指向同一个库文件。"""
        from_root = _run_from(ROOT)
        from_backend = _run_from(ROOT / "backend")

        self.assertEqual(
            from_root, from_backend,
            f"库路径跟着 cwd 变了：根目录启动={from_root}，backend 启动={from_backend}",
        )

    def test_database_path_is_absolute(self):
        """相对路径是分叉的根源，默认值必须是绝对路径。"""
        path = Path(_run_from(ROOT))
        self.assertTrue(path.is_absolute(), f"默认库路径是相对的：{path}")

    def test_database_path_points_at_project_root(self):
        """落在项目根，而不是 backend/ 或其它子目录。"""
        path = Path(_run_from(ROOT))
        self.assertEqual(path.name, "geo.db")
        self.assertEqual(path.parent, ROOT)

    def test_env_override_still_wins(self):
        """GEO_DB_PATH 仍然能覆盖——测试正是靠它指向临时库。"""
        env = {**os.environ, "GEO_DB_PATH": str(ROOT / "custom-test.db")}
        result = subprocess.run(
            [sys.executable, "-c", PROBE],
            cwd=str(ROOT), capture_output=True, text=True, env=env, timeout=30,
        )
        self.assertEqual(result.stdout.strip(), str(ROOT / "custom-test.db"))


if __name__ == "__main__":
    unittest.main()
