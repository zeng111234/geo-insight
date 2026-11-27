"""信源落地页组装的验收（SOP 第三步 AI 转化通道）。

守的是：落地页展示的信息必须来自真实登记，读不到就明确标「待补充」，
不写死、不臆造、不拿演示数据冒充。键名与服务台账录入端(settings.html)一致。
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from geo.services import site_service
from tests import fixtures

TID = "t0000000-0000-0000-0000-000000000001"


class SiteProfileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "site.db"
        self.conn = fixtures.build_minimal_db(self.db_path)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.conn.close)

    def _set_settings(self, settings: dict):
        self.conn.execute(
            "UPDATE tracker SET settings = ? WHERE id = ?",
            (json.dumps(settings, ensure_ascii=False), TID),
        )
        self.conn.commit()

    def test_real_registered_fields_are_exposed(self):
        self._set_settings({
            "service": {"phone": "020-12345678", "website": "https://huice.example", "package": "标准版"},
            "assets": {
                "table1": {"intro": "惠策做GEO服务", "cases": ["案例A", "案例B"]},
                "table2": {"accounts": [{"platform": "公众号", "name": "惠策"}]},
                "table3": {"geo_words": "GEO优化,AI搜索"},
            },
        })
        p = site_service.build_site_profile(self.conn, TID, fixtures.TENANT)
        self.assertEqual(p["phone"], "020-12345678")
        self.assertEqual(p["website"], "https://huice.example")
        self.assertEqual(p["intro"], "惠策做GEO服务")
        self.assertEqual(p["cases"], ["案例A", "案例B"])
        self.assertEqual(p["accounts"], [{"platform": "公众号", "name": "惠策"}])
        self.assertEqual(p["geo_words"], "GEO优化,AI搜索")
        self.assertNotIn("phone", p["missing"])

    def test_missing_fields_marked_placeholder(self):
        self._set_settings({"service": {"package": "标准版"}})
        p = site_service.build_site_profile(self.conn, TID, fixtures.TENANT)
        self.assertEqual(p["phone"], "")
        self.assertIn("phone", p["missing"])
        self.assertIn("intro", p["missing"])
        self.assertEqual(p["placeholder"], "待补充")

    def test_bad_json_does_not_crash(self):
        self.conn.execute("UPDATE tracker SET settings = ? WHERE id = ?", ("{bad json", TID))
        self.conn.commit()
        p = site_service.build_site_profile(self.conn, TID, fixtures.TENANT)
        self.assertIsNotNone(p)
        self.assertEqual(p["phone"], "")
        self.assertTrue(p["missing"])

    def test_legacy_website_fallback(self):
        self._set_settings({"website": "https://old.example"})
        p = site_service.build_site_profile(self.conn, TID, fixtures.TENANT)
        self.assertEqual(p["website"], "https://old.example")

    def test_wrong_tenant_returns_none(self):
        p = site_service.build_site_profile(self.conn, TID, "00000000-0000-0000-0000-999999999999")
        self.assertIsNone(p)

    def test_no_fake_data_when_empty(self):
        self._set_settings({})
        p = site_service.build_site_profile(self.conn, TID, fixtures.TENANT)
        self.assertEqual(p["cases"], [])
        self.assertEqual(p["accounts"], [])
        # 关键：空设置不能凭空冒出电话/官网
        self.assertEqual(p["phone"], "")
        self.assertEqual(p["website"], "")


if __name__ == "__main__":
    unittest.main()
