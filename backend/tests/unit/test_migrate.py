"""迁移链的行为测试。

关注点是"出错时会怎样"，而不是"能建表"：能建表只证明代码能跑，
版本表、checksum、老库认领这些边界才是迁移链存在的理由。
"""

import importlib.util
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


class ImportBatchRebuildTest(unittest.TestCase):
    """004 重建 import_batch：SQLite 改 CHECK 只能重建表，三件事都不能丢。

    数据（批次历史）、外键（answer 还指着它）、可重跑（重建不重复执行）。
    """

    OLD_DEFINITION = """
        CREATE TABLE import_batch (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            tracker_id TEXT,
            source_name TEXT NOT NULL,
            source_format TEXT NOT NULL CHECK (source_format IN ('csv', 'jsonl')),
            status TEXT NOT NULL DEFAULT 'running'
                CHECK (status IN ('running', 'completed', 'failed')),
            row_total INTEGER NOT NULL DEFAULT 0,
            row_inserted INTEGER NOT NULL DEFAULT 0,
            row_skipped INTEGER NOT NULL DEFAULT 0,
            row_failed INTEGER NOT NULL DEFAULT 0,
            report_uri TEXT,
            error TEXT,
            started_at TEXT NOT NULL,
            finished_at TEXT
        );
        CREATE TABLE answer (id TEXT PRIMARY KEY, import_batch_id TEXT REFERENCES import_batch(id));
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.conn = sqlite3.connect(Path(self._tmp.name) / "t.db")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.addCleanup(self.conn.close)

    def migration(self):
        path = Path(__file__).resolve().parents[2] / "migrations" / "004_import_batch_rollback.py"
        spec = importlib.util.spec_from_file_location("m004", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def prepare(self):
        self.conn.executescript(self.OLD_DEFINITION)
        self.conn.execute(
            """INSERT INTO import_batch(id, tenant_id, source_name, source_format, started_at, status, row_inserted)
               VALUES ('b1', 't1', 'f.csv', 'csv', '2026-11-24T10:00:00+08:00', 'completed', 7)"""
        )
        self.conn.execute("INSERT INTO answer(id, import_batch_id) VALUES ('a1', 'b1')")
        self.conn.commit()

    def test_rebuild_keeps_rows_and_allows_rolled_back(self):
        self.prepare()
        self.migration().apply(self.conn)

        row = self.conn.execute("SELECT * FROM import_batch").fetchone()
        self.assertEqual(row["row_inserted"], 7)
        self.assertEqual(row["source_name"], "f.csv")
        # 新约束生效
        self.conn.execute("UPDATE import_batch SET status = 'rolled_back' WHERE id = 'b1'")
        self.conn.commit()

    def test_rebuild_keeps_foreign_key_enforced(self):
        self.prepare()
        self.migration().apply(self.conn)

        # 迁移结束必须把 foreign_keys 打开，否则后续写入全部失去保护
        self.assertEqual(self.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO answer(id, import_batch_id) VALUES ('a2', 'no-such-batch')")

    def test_rebuild_is_rerunnable(self):
        self.prepare()
        module = self.migration()
        module.apply(self.conn)
        module.apply(self.conn)

        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM import_batch").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM answer").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
