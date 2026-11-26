"""备份文件名解析的行为测试。

真问题来自一次实际部署演练：`backup.py --list` 打印的是裸文件名
（`geo-20261126-135254.db`），照抄到 `--verify` 却报 NOT_FOUND——
因为 verify_backup 按 cwd 解析，备份实际在 backups/ 子目录。
本测试守住"从 list 复制名字就能 verify/restore"这条最短路径。
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.backup import resolve_backup_arg


class ResolveBackupArgTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.backup_dir = self.root / "backups"
        self.backup_dir.mkdir()
        # 换 cwd 到别处，模拟"在项目根/cwd 下敲裸文件名"的场景
        self.cwd = self.root / "elsewhere"
        self.cwd.mkdir()
        self.env = mock.patch.dict(os.environ, {"GEO_BACKUP_DIR": str(self.backup_dir)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.chdir = mock.patch("os.getcwd", return_value=str(self.cwd))
        # 不真 chdir（会影响别的测试），resolve_backup_arg 只用 Path.is_file 查路径

    def test_bare_name_finds_file_in_backup_dir(self):
        name = "geo-20261126-135254.db"
        (self.backup_dir / name).write_bytes(b"x")
        self.assertEqual(resolve_backup_arg(name), str(self.backup_dir / name))

    def test_full_path_passes_through_when_exists(self):
        target = self.root / "custom-place.db"
        target.write_bytes(b"x")
        self.assertEqual(resolve_backup_arg(str(target)), str(target))

    def test_unknown_name_stays_as_typed(self):
        # 找不到时不能悄悄改成 backups/ 下的路径，否则报错里显示的不是用户输入
        unknown = "totally-not-there.db"
        self.assertEqual(resolve_backup_arg(unknown), unknown)

    def test_directory_named_like_backup_is_not_treated_as_file(self):
        # is_file() 而非 exists()：目录不能当备份打开
        (self.backup_dir / "a-dir.db").mkdir()
        self.assertEqual(resolve_backup_arg("a-dir.db"), "a-dir.db")


if __name__ == "__main__":
    unittest.main()
