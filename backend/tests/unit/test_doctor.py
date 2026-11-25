"""换机器自检脚本的回归保护：数据库分叉判定（决策 013 的守门逻辑）。

doctor.py 在 scripts/ 目录、不是包，用 importlib 从文件路径加载。
只测纯函数 stray_verdict —— 它决定「backend/geo.db 到底要不要警告」，
一旦判错，换机器时要么漏报（数据看起来丢了却没人提醒），
要么误报（空壳文件也喊分叉），两种都会误导人。
"""

import importlib.util
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

DOCTOR = Path(__file__).resolve().parents[2] / "scripts" / "doctor.py"
_spec = importlib.util.spec_from_file_location("geo_doctor_under_test", DOCTOR)
doctor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(doctor)


class StrayVerdictTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_no_stray_file_is_ok(self):
        db = self.root / "geo.db"
        stray = self.root / "backend" / "geo.db"
        level, item, detail = doctor.stray_verdict(db, stray)
        self.assertEqual(level, doctor.OK)
        self.assertEqual(item, "数据库位置")
        self.assertEqual(detail, str(db))

    def test_same_file_different_path_is_ok(self):
        """当前库就指到 backend/geo.db 时不能自己警告自己。"""
        db = self.root / "geo.db"
        db.write_bytes(b"data")
        level, item, _detail = doctor.stray_verdict(db, db)
        self.assertEqual(level, doctor.OK)
        self.assertEqual(item, "数据库位置")

    def test_empty_stray_file_is_warn_with_clear_advice(self):
        db = self.root / "geo.db"
        db.write_bytes(b"data")
        stray = self.root / "backend" / "geo.db"
        stray.parent.mkdir()
        stray.write_bytes(b"")
        level, item, detail = doctor.stray_verdict(db, stray)
        self.assertEqual(level, doctor.WARN)
        self.assertEqual(item, "数据库")
        self.assertIn("0 字节", detail)
        self.assertIn("可以", detail)  # 说清能删，不是含糊地让人自己猜

    def test_nonempty_stray_file_warns_about_fork(self):
        db = self.root / "geo.db"
        db.write_bytes(b"main data")
        stray = self.root / "backend" / "geo.db"
        stray.parent.mkdir()
        stray.write_bytes(b"old data")
        level, item, detail = doctor.stray_verdict(db, stray)
        self.assertEqual(level, doctor.WARN)
        self.assertEqual(item, "数据库分叉")
        self.assertIn("确认哪份是最新", detail)
        self.assertIn("不同数据", detail)

    def test_stray_verdict_does_not_modify_files(self):
        """自检脚本只读——测完文件必须原样。"""
        db = self.root / "geo.db"
        db.write_bytes(b"main")
        stray = self.root / "backend" / "geo.db"
        stray.parent.mkdir()
        stray.write_bytes(b"old")
        before = (db.read_bytes(), stray.read_bytes())
        doctor.stray_verdict(db, stray)
        self.assertEqual((db.read_bytes(), stray.read_bytes()), before)


if __name__ == "__main__":
    unittest.main()
