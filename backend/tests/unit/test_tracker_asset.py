"""客户资料归档（SOP 第一步照片）的验收。

断点：系统此前完全没有文件归档能力，SOP 要的「形象照10张/荣誉活动照30张+」
无处可落。这里守：上传真落库+文件在、分类清单能算达标、租户隔离、
以及安全边界（文件名服务端生成、文件头校验、目录穿越拒绝）。
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path

from geo.repositories import asset_repository as asset_repo
from geo.services import tracker_asset_service as asset_svc
from tests import fixtures

TID = fixtures.TRACKER
TENANT = fixtures.TENANT
PNG = b"\x89PNG\r\n\x1a\n" + b"fake-image-bytes-for-test"


class TrackerAssetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._orig_dir = asset_svc.ASSET_DIR
        # 隔离图片目录，别污染 backend/data
        asset_svc.ASSET_DIR = Path(self.tmp.name) / "assets"
        self.addCleanup(setattr, asset_svc, "ASSET_DIR", self._orig_dir)
        self.db_path = Path(self.tmp.name) / "asset.db"
        self.conn = fixtures.build_minimal_db(self.db_path)
        self.addCleanup(self.conn.close)

    def _add(self, category="portrait", data=PNG, filename="photo.png"):
        name, size = asset_svc.save(data, filename, category)
        return asset_repo.add_asset(
            self.conn,
            asset_id=str(uuid.uuid4()),
            tenant_id=TENANT,
            tracker_id=TID,
            category=category,
            filename=name,
            original_name=filename,
            size_bytes=size,
            now=fixtures.NOW,
        )

    def test_upload_lands_file_and_row(self):
        a = self._add()
        self.assertTrue(asset_svc.path_for(a["filename"]).is_file(), "文件真落盘")
        self.conn.commit()  # 模拟 handler 提交
        c = sqlite3.connect(self.db_path)
        try:
            n = c.execute("SELECT COUNT(*) FROM tracker_asset WHERE id=?", (a["id"],)).fetchone()[0]
        finally:
            c.close()
        self.assertEqual(n, 1, "新连接回读，真落库")

    def test_filename_is_server_generated_not_from_caller(self):
        a = self._add(filename="../../evil.png")
        self.assertNotIn("..", a["filename"])
        self.assertEqual(Path(a["filename"]).name, a["filename"], "库里只存纯文件名")
        self.assertTrue(asset_svc.path_for(a["filename"]).is_file())

    def test_path_for_rejects_traversal(self):
        with self.assertRaises(asset_svc.AssetError):
            asset_svc.path_for("../../secret.png")

    def test_fake_png_rejected(self):
        with self.assertRaises(asset_svc.AssetError):
            asset_svc.save(b"not a png", "evil.png", "portrait")

    def test_bad_category_rejected(self):
        with self.assertRaises(asset_svc.AssetError):
            asset_svc.save(PNG, "x.png", "unknown")

    def test_checklist_counts_toward_sop_targets(self):
        for _ in range(3):
            self._add("portrait")
        for _ in range(2):
            self._add("honor")
        self.conn.commit()
        cl = asset_repo.checklist(self.conn, TID, TENANT)
        portrait = next(x for x in cl["items"] if x["category"] == "portrait")
        self.assertEqual(portrait["count"], 3)
        self.assertEqual(portrait["target"], 10)
        self.assertFalse(portrait["met"])
        he = next(x for x in cl["items"] if x["category"] == "honor_event")
        self.assertEqual(he["count"], 2)

    def test_delete_removes_file_and_row(self):
        a = self._add()
        path = asset_svc.path_for(a["filename"])
        asset_repo.delete_asset(self.conn, a["id"], TENANT)
        asset_svc.delete(a["filename"])
        self.conn.commit()
        self.assertFalse(path.exists())
        self.assertIsNone(asset_repo.get_asset(self.conn, a["id"], TENANT))

    def test_cross_tenant_cannot_read_or_delete(self):
        a = self._add()
        other = "00000000-0000-0000-0000-999999999999"
        self.assertIsNone(asset_repo.get_asset(self.conn, a["id"], other))
        self.assertIsNone(asset_repo.delete_asset(self.conn, a["id"], other))
        self.assertEqual(len(asset_repo.list_assets(self.conn, TID, other)), 0)


if __name__ == "__main__":
    unittest.main()
