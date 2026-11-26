"""恢复演练的纯函数测试。

守的是"演练的前提判据"：能把旧备份找出来、能正确读出 schema 版本。
不跑真实恢复（那是脚本的职责，已在真库上演练过），只测这两处不会因为
版本号写死或读错而让演练变成假绿。
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.restore_rehearse import _counts, _schema_version


class RestoreRehearseHelpersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "t.db"
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE schema_migration(version INTEGER)")
        conn.executemany("INSERT INTO schema_migration VALUES (?)", [(1,), (15,)])
        conn.execute("CREATE TABLE answer(id TEXT)")
        conn.execute("INSERT INTO answer VALUES ('a1')")
        conn.commit()
        conn.close()

    def test_reads_max_schema_version(self):
        self.assertEqual(_schema_version(self.db), 15)

    def test_no_schema_table_reads_zero(self):
        empty = self.root / "empty.db"
        sqlite3.connect(empty).close()
        self.assertEqual(_schema_version(empty), 0)

    def test_counts_answers(self):
        self.assertEqual(_counts(self.db)["answers"], 1)

    def test_counts_missing_tables_as_zero(self):
        self.assertEqual(_counts(self.root / "none.db"), {"answers": 0, "mentions": 0, "citations": 0})


if __name__ == "__main__":
    unittest.main()
