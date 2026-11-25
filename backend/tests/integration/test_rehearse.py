"""全链路演练脚本的验收。

演练脚本不是给客户看的，是给改代码的人用的回归闸门：
key 没批、渠道没扫码时，也能验证"建对象→采集→识别→判定→备份"没断。

这条测试做两件事：
  1. 用子进程跑 rehearse.py，断言六步都通过（退出码 0 + 关键输出）；
  2. 断言真实 geo.db 没有被演练污染（答案数、演练对象数不变）。

第 2 条比第 1 条重要：演练脚本最危险的失败不是报错，而是悄悄写到真实库。
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "backend" / "scripts" / "rehearse.py"
REAL_DB = ROOT / "geo.db"


def _real_counts() -> tuple[int, int]:
    if not REAL_DB.is_file():
        return (-1, -1)
    conn = sqlite3.connect(REAL_DB)
    try:
        answers = conn.execute("SELECT COUNT(*) FROM answer").fetchone()[0]
        rehearsal = conn.execute(
            "SELECT COUNT(*) FROM tracker WHERE name = '演练试点'"
        ).fetchone()[0]
        return answers, rehearsal
    finally:
        conn.close()


class RehearseTests(unittest.TestCase):
    def test_rehearsal_passes_all_six_steps(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT / "backend" / "src")
        proc = subprocess.run(
            [sys.executable, str(SCRIPT)],
            cwd=ROOT / "backend",
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        for needle in (
            "[1/6] 建试点对象",
            "[2/6] 填 3 天基线",
            "[3/6] 增量识别与全量重算对拍",
            "[4/6] 造已发布文章",
            "[5/6] 判定",
            "[6/6] 备份",
            "全链路演练通过",
        ):
            self.assertIn(needle, proc.stdout, f"演练输出缺少 {needle}")
        self.assertIn("样本不足时不下结论: insufficient", proc.stdout)

    def test_rehearsal_does_not_touch_real_db(self):
        before = _real_counts()
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT / "backend" / "src")
        proc = subprocess.run(
            [sys.executable, str(SCRIPT)],
            cwd=ROOT / "backend",
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(_real_counts(), before, "演练污染了真实 geo.db")


if __name__ == "__main__":
    unittest.main()
