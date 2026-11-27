"""服务台账与资料归档存 tracker.settings JSON，浅合并、不丢 website。

轻量内部版（决策 012）：单租户单对象，不加列、不加 migration、不做多客户隔离。
真缺口：此前只有 website 存 settings，台账（套餐/价格/开始时间/负责人）
与表一/表二/表三资料无处可存，SOP 第一步在系统里落不了地。
"""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from geo.repositories import config_repository as repo
from geo.services import config_service
from tests import fixtures


class TrackerLedgerSettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "t.db"
        self.conn = fixtures.build_minimal_db(self.db_path)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.conn.close)
        created = config_service.create(
            self.conn,
            fixtures.TENANT,
            {"name": "辽征 · 律所", "brand_name": "辽征", "website": "https://example.com"},
            fixtures.NOW,
        )
        self.tid = created["tracker_id"]

    def _reload(self):
        """新连接回读——模拟 API 关连接后的真实视角，不靠同连接缓存。"""
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        try:
            row = c.execute("SELECT settings FROM tracker WHERE id = ?", (self.tid,)).fetchone()
            return json.loads(row["settings"] or "{}")
        finally:
            c.close()

    def test_merge_keeps_existing_website(self):
        repo.merge_tracker_settings(
            self.conn, self.tid, fixtures.TENANT,
            {"service": {"package": "标准版", "price": 9800, "start_date": "2026-11-27"}},
            fixtures.NOW,
        )
        saved = self._reload()
        self.assertEqual(saved["website"], "https://example.com", "合并不能丢已有官网")
        self.assertEqual(saved["service"]["package"], "标准版")
        self.assertEqual(saved["service"]["price"], 9800)

    def test_merge_is_idempotent_per_key(self):
        patch1 = {"assets": {"table1": {"intro": "律所简介"}, "table2": {"accounts": []}}}
        patch2 = {"assets": {"table3": {"geo_words": "律所,刑事辩护"}}}
        repo.merge_tracker_settings(self.conn, self.tid, fixtures.TENANT, patch1, fixtures.NOW)
        repo.merge_tracker_settings(self.conn, self.tid, fixtures.TENANT, patch2, fixtures.NOW)
        saved = self._reload()
        # 浅合并：同一次写入的两个表都在
        self.assertEqual(saved["assets"]["table1"]["intro"], "律所简介")
        self.assertEqual(saved["assets"]["table3"]["geo_words"], "律所,刑事辩护")

    def test_tracker_detail_exposes_settings(self):
        repo.merge_tracker_settings(
            self.conn, self.tid, fixtures.TENANT,
            {"service": {"owner": "运营A"}}, fixtures.NOW,
        )
        detail = config_service.tracker_detail(self.conn, self.tid, fixtures.TENANT)
        self.assertEqual(detail["settings"]["service"]["owner"], "运营A")
        self.assertEqual(detail["website"], "https://example.com", "旧字段仍要兼容")

    def test_bad_settings_json_does_not_crash_detail(self):
        self.conn.execute(
            "UPDATE tracker SET settings = ? WHERE id = ?", ("{bad json", self.tid)
        )
        self.conn.commit()
        detail = config_service.tracker_detail(self.conn, self.tid, fixtures.TENANT)
        self.assertEqual(detail["settings"], {})
        self.assertEqual(detail["website"], "")


if __name__ == "__main__":
    unittest.main()
