"""备份 / 恢复的验收测试。

核心要证明的不是"能拷出一个文件"，而是三件容易错的事：

1. **备份是一致快照**：未提交的事务不能进去（证明了用的不是文件复制）。
2. **恢复真的回到那个时点**：恢复后再塞的数据消失，且 -wal/-shm 不会把旧数据带回来。
3. **恢复失败要明确拒绝**：服务占用时宁可报 BUSY，也不能写到一半。

另外两条是安全网：恢复前自动存一份、prune 不动 pre-restore-*。
安全网本身失效时最危险——你以为能回退，其实回退不了。
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from geo.services import backup_service
from geo.services.backup_service import BackupError
from tests import fixtures


class BackupTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "geo.db"
        self.conn = fixtures.build_minimal_db(self.db)
        self.backups = self.root / "backups"
        self.backups.mkdir()

        self.env = mock.patch.dict(os.environ, {"GEO_BACKUP_DIR": str(self.backups)})
        self.env.start()

        self.addCleanup(self.env.stop)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self._close_conn)

    def _close_conn(self):
        try:
            self.conn.close()
        except sqlite3.Error:
            pass

    def add_answer(self, text: str) -> str:
        # fixtures.add_answer 只插入不提交（它服务的是"同一连接内可见"的测试）。
        # 备份相关的断言全在**另一个连接**上读，所以这里必须显式提交。
        answer_id = fixtures.add_answer(self.conn, fixtures.NOW, text=text)
        self.conn.commit()
        return answer_id

    def count_answers(self, path: Path | None = None) -> int:
        target = path or self.db
        with closing(sqlite3.connect(target)) as conn:
            return conn.execute("SELECT COUNT(*) FROM answer").fetchone()[0]


class BackupTests(BackupTestBase):
    def test_backup_contains_committed_rows(self):
        self.add_answer("第一条")
        self.add_answer("第二条")

        info = backup_service.create_backup(self.db)

        self.assertTrue(info["ok"])
        self.assertEqual(info["answers"], 2)
        self.assertEqual(info["integrity"], "ok")
        self.assertTrue(Path(info["path"]).is_file())

    def test_backup_includes_committed_data_still_in_wal(self):
        """WAL 模式下，已提交的数据可能还在 -wal 里没写进主文件。

        这是 ``VACUUM INTO`` 相对文件复制**唯一但有决定性**的优势，也是这条测试
        存在的理由：``shutil.copy`` 只拿走主文件，主文件里没有那条刚提交的数据。
        备份能正常打开、能正常查询，只是少了最近的记录——用户不会察觉，
        直到需要这份备份的那天。

        构造：切成 WAL → 写一条并提交（默认 auto-checkpoint 阈值是 1000 页，
        一条记录远达不到，所以数据必然还在 -wal 里），然后分别拷文件与做快照。
        """
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.add_answer("刚提交还在 wal 里的一条")

        # 先证明数据确实不在主文件里：单独拷主文件，另一个连接数出 0
        naive = self.root / "naive-copy.db"
        naive.write_bytes(self.db.read_bytes())
        with closing(sqlite3.connect(naive)) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM answer").fetchone()[0],
                0,
                "数据应当还在 -wal 里；这里不是 0 说明环境自动 checkpoint 了，测试前提失效",
            )

        # VACUUM INTO 走读事务，能看到已提交的全部数据
        info = backup_service.create_backup(self.db)
        self.assertEqual(info["answers"], 1)

    def test_backup_goes_to_configured_dir(self):
        info = backup_service.create_backup(self.db)
        self.assertEqual(Path(info["path"]).parent, self.backups)
        self.assertRegex(Path(info["path"]).name, r"^geo-\d{8}-\d{6}\.db$")

    def test_verify_rejects_non_database(self):
        bad = self.root / "bad.db"
        bad.write_bytes(b"this is not a sqlite file at all")

        with self.assertRaises(BackupError) as caught:
            backup_service.verify_backup(bad)
        self.assertIn(caught.exception.code, {"NOT_A_DATABASE", "UNREADABLE"})

    def test_verify_reports_missing_file(self):
        with self.assertRaises(BackupError) as caught:
            backup_service.verify_backup(self.root / "nope.db")
        self.assertEqual(caught.exception.code, "NOT_FOUND")

    def test_verify_does_not_create_file(self):
        """校验一个不存在的路径不能"顺手建出来"——否则打错路径会得到一个空备份。"""
        missing = self.root / "typo.db"
        with self.assertRaises(BackupError):
            backup_service.verify_backup(missing)
        self.assertFalse(missing.exists())


class RestoreTests(BackupTestBase):
    def test_restore_returns_to_snapshot(self):
        self.add_answer("备份时的这一条")
        snapshot = backup_service.create_backup(self.db)
        self.add_answer("快照之后才写的")

        self.assertEqual(self.count_answers(), 2)
        # 恢复要求独占：先把测试自己的连接关掉，模拟"服务已停"
        self.conn.close()

        result = backup_service.restore_backup(snapshot["path"], self.db)

        self.assertEqual(result["before"]["answers"], 2)
        self.assertEqual(result["after"]["answers"], 1)
        self.assertEqual(self.count_answers(), 1)
        with closing(sqlite3.connect(self.db)) as conn:
            texts = [row[0] for row in conn.execute("SELECT text FROM answer")]
        self.assertEqual(texts, ["备份时的这一条"])

    def test_restore_makes_safety_copy(self):
        self.add_answer("A")
        snapshot = backup_service.create_backup(self.db)
        self.add_answer("B")
        self.conn.close()

        result = backup_service.restore_backup(snapshot["path"], self.db)

        self.assertIsNotNone(result["safety_copy"])
        safety = Path(result["safety_copy"])
        self.assertTrue(safety.name.startswith("pre-restore-"))
        # 安全副本里应当有恢复前的两条，否则"回退"是假的
        with closing(sqlite3.connect(safety)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM answer").fetchone()[0], 2)

    def test_restore_refuses_when_database_is_busy(self):
        self.add_answer("A")
        snapshot = backup_service.create_backup(self.db)
        self.conn.close()

        holder = sqlite3.connect(self.db, timeout=0)
        try:
            holder.execute("BEGIN EXCLUSIVE")
            holder.execute(
                """INSERT INTO answer(id, tenant_id, tracker_id, question_id, model_key,
                                      channel, raw_uri, text, text_hash, collected_at, row_hash)
                   VALUES ('held', ?, ?, ?, 'doubao', 'api', 'test://y', '占用',
                           'h2', ?, 'r2')""",
                (fixtures.TENANT, fixtures.TRACKER, fixtures.QUESTION, fixtures.NOW),
            )
            with self.assertRaises(BackupError) as caught:
                backup_service.restore_backup(snapshot["path"], self.db)
            self.assertEqual(caught.exception.code, "BUSY")
        finally:
            holder.rollback()
            holder.close()

        # 被拒绝的恢复不能留下半成品：库还是原来那份（1 条已提交 + 无 held）
        self.assertEqual(self.count_answers(), 1)

    # 曾经有一条 test_restore_drops_stale_wal：往 -wal 写垃圾，恢复后断言它消失。
    # 变异验证时发现把 _drop_sidecars 注释掉，测试照样通过——因为 SQLite 在整体
    # 替换主文件后会自己忽略/清掉不匹配的 WAL。也就是说那条测试测的是 SQLite 的行为，
    # 不是我们的代码，删掉。_drop_sidecars 仍保留为防御措施（见其 docstring）。

    def test_restore_migrates_old_schema(self):
        """备份可能是旧结构。恢复后必须升到最新，否则新代码查不存在的列。"""
        self.add_answer("A")
        snapshot = backup_service.create_backup(self.db)
        self.conn.close()

        # 把备份的迁移记录往回退，模拟"旧版本备份"
        # 注意：closing() 只关连接不提交，改完必须显式 commit
        with closing(sqlite3.connect(snapshot["path"])) as conn:
            conn.execute("DELETE FROM schema_migration WHERE version >= 14")
            conn.commit()

        result = backup_service.restore_backup(snapshot["path"], self.db)

        self.assertIsNotNone(result["migrations_applied"])
        self.assertIn("014", result["migrations_applied"])
        # 迁移真的跑到了最新
        with closing(sqlite3.connect(self.db)) as conn:
            version = conn.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0]
        self.assertGreaterEqual(version, 15)
        self.assertEqual(self.count_answers(), 1)

    def test_restore_rejects_bad_backup_before_touching_target(self):
        self.add_answer("要保住的数据")
        bad = self.root / "bad.db"
        bad.write_bytes(b"garbage")
        self.conn.close()

        with self.assertRaises(BackupError):
            backup_service.restore_backup(bad, self.db)

        # 目标库一点没动
        self.assertEqual(self.count_answers(), 1)


class PruneTests(BackupTestBase):
    def _touch_backups(self, prefix: str, count: int) -> list[Path]:
        paths = []
        for index in range(count):
            path = self.backups / f"{prefix}-{index:02d}.db"
            path.write_bytes(b"x")
            # 让 mtime 有先后，prune 按时间排序
            os.utime(path, (1000 + index, 1000 + index))
            paths.append(path)
        return paths

    def test_prune_keeps_newest(self):
        self._touch_backups("geo", 5)
        removed = backup_service.prune_backups(keep=2, directory=self.backups)

        self.assertEqual(len(removed), 3)
        remaining = sorted(p.name for p in self.backups.glob("geo-*.db"))
        self.assertEqual(remaining, ["geo-03.db", "geo-04.db"])

    def test_prune_never_deletes_safety_copies(self):
        """pre-restore-* 是恢复出错时唯一能回退的东西，不能自动删。"""
        self._touch_backups("geo", 3)
        safety = self._touch_backups("pre-restore", 3)

        backup_service.prune_backups(keep=1, directory=self.backups)

        for path in safety:
            self.assertTrue(path.exists(), f"{path.name} 不该被 prune 删掉")

    def test_list_marks_safety_copies(self):
        self._touch_backups("geo", 1)
        self._touch_backups("pre-restore", 1)

        items = backup_service.list_backups(self.backups)
        flags = {item["name"]: item["is_safety"] for item in items}
        self.assertFalse(flags["geo-00.db"])
        self.assertTrue(flags["pre-restore-00.db"])


if __name__ == "__main__":
    unittest.main()
