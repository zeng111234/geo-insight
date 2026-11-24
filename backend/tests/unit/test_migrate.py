"""迁移链的行为测试。

关注点是"出错时会怎样"，而不是"能建表"：能建表只证明代码能跑，
版本表、checksum、老库认领这些边界才是迁移链存在的理由。
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from geo import migrate


class MigrateTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.dir = self.root / "migrations"
        self.dir.mkdir()
        self.conn = sqlite3.connect(self.root / "t.db")
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)

    # --- 工具 ---
    def write(self, name: str, text: str) -> Path:
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def apply(self):
        return migrate.apply(self.conn, directory=self.dir)

    def tables(self) -> set[str]:
        rows = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        return {r[0] for r in rows}

    def versions(self) -> list[int]:
        rows = self.conn.execute("SELECT version FROM schema_migration ORDER BY version").fetchall()
        return [r[0] for r in rows]

    def baseline(self):
        return self.write("001_init.sql", "CREATE TABLE IF NOT EXISTS a (id TEXT PRIMARY KEY);")

    # --- 正常路径 ---
    def test_fresh_db_applies_in_version_order(self):
        self.baseline()
        self.write("002_b.sql", "CREATE TABLE IF NOT EXISTS b (id TEXT PRIMARY KEY);")
        self.write("010_c.sql", "CREATE TABLE IF NOT EXISTS c (id TEXT PRIMARY KEY);")

        self.assertEqual(self.apply(), ["001_init", "002_b", "010_c"])
        self.assertTrue({"a", "b", "c"} <= self.tables())
        self.assertEqual(self.versions(), [1, 2, 10])
        # 外部工具看 PRAGMA 就能知道当前版本
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], 10)

    def test_second_apply_is_noop(self):
        self.baseline()
        self.apply()
        self.assertEqual(self.apply(), [])
        self.assertEqual(self.versions(), [1])

    def test_py_migration_runs(self):
        self.baseline()
        self.write(
            "002_py.py",
            "def apply(conn):\n    conn.execute('CREATE TABLE IF NOT EXISTS py_t (id TEXT)')\n",
        )
        self.assertEqual(self.apply(), ["001_init", "002_py"])
        self.assertIn("py_t", self.tables())

    # --- 老库认领 ---
    def test_legacy_db_is_adopted_without_rerun(self):
        # 模拟旧 schema.sql 建出来的库：有表，没有版本表
        self.baseline()
        sql = (self.dir / "001_init.sql").read_text(encoding="utf-8")
        self.conn.executescript(sql)
        self.conn.execute("INSERT INTO a(id) VALUES ('keep-me')")
        self.conn.commit()
        self.write("002_b.sql", "CREATE TABLE IF NOT EXISTS b (id TEXT PRIMARY KEY);")

        self.assertEqual(self.apply(), ["001_init" + migrate.ADOPT_MARK, "002_b"])
        self.assertEqual(self.versions(), [1, 2])
        # 认领不等于重建：原有数据必须还在
        self.assertEqual(self.conn.execute("SELECT id FROM a").fetchone()[0], "keep-me")

    def test_legacy_db_missing_tables_is_refused(self):
        self.baseline()
        self.conn.executescript("CREATE TABLE IF NOT EXISTS something_else (id TEXT);")
        self.conn.commit()

        with self.assertRaises(migrate.MigrationError) as ctx:
            self.apply()
        message = str(ctx.exception)
        self.assertIn("缺少", message)
        self.assertIn("a", message)
        # 拒绝时不能留下半个版本记录
        self.assertEqual(self.versions(), [])

    # --- 已应用迁移被改动 ---
    def test_tampered_migration_is_refused(self):
        path = self.baseline()
        self.apply()
        path.write_text("CREATE TABLE IF NOT EXISTS a (id TEXT PRIMARY KEY);\n-- 事后加的一行\n", encoding="utf-8")

        with self.assertRaises(migrate.MigrationError) as ctx:
            self.apply()
        self.assertIn("checksum", str(ctx.exception))

    def test_missing_migration_file_is_refused(self):
        path = self.baseline()
        self.apply()
        path.unlink()

        with self.assertRaises(migrate.MigrationError) as ctx:
            self.apply()
        self.assertIn("找不到", str(ctx.exception))

    # --- 文件名与版本号 ---
    def test_bad_filename_is_reported(self):
        self.write("001 init.sql", "CREATE TABLE IF NOT EXISTS a (id TEXT);")
        with self.assertRaises(migrate.MigrationError) as ctx:
            self.apply()
        self.assertIn("文件名不合规", str(ctx.exception))

    def test_duplicate_version_is_reported(self):
        self.baseline()
        self.write("001_again.sql", "CREATE TABLE IF NOT EXISTS z (id TEXT);")
        with self.assertRaises(migrate.MigrationError) as ctx:
            self.apply()
        self.assertIn("重复", str(ctx.exception))

    def test_empty_dir_is_reported(self):
        with self.assertRaises(migrate.MigrationError) as ctx:
            self.apply()
        self.assertIn("没有任何迁移文件", str(ctx.exception))

    # --- 中途失败可修复重跑（这是允许 executescript 的前提）---
    def test_failed_migration_can_be_fixed_and_rerun(self):
        self.baseline()
        broken = self.write(
            "002_bad.sql",
            "CREATE TABLE IF NOT EXISTS b (id TEXT);\nINSERT INTO no_such_table VALUES (1);\n",
        )
        with self.assertRaises(sqlite3.Error):
            self.apply()
        # 失败的那条不应被记成已应用
        self.assertEqual(self.versions(), [1])

        broken.write_text("CREATE TABLE IF NOT EXISTS b (id TEXT);\n", encoding="utf-8")
        self.assertEqual(self.apply(), ["002_bad"])
        self.assertIn("b", self.tables())


class ShippedMigrationsTest(unittest.TestCase):
    """对仓库里真实的迁移文件做一次冒烟，避免测试只验证了自己造的假迁移。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.conn = sqlite3.connect(Path(self._tmp.name) / "real.db")
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)

    def test_real_chain_creates_core_tables(self):
        newly = migrate.apply(self.conn)
        self.assertIn("001_init", newly)
        tables = {
            r[0]
            for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        for expected in ("answer", "mention", "citation", "tracker", "question"):
            self.assertIn(expected, tables)

    def test_real_chain_is_idempotent(self):
        migrate.apply(self.conn)
        self.assertEqual(migrate.apply(self.conn), [])


if __name__ == "__main__":
    unittest.main()
