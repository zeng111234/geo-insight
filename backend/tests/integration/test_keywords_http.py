"""核心词的 HTTP 行为：保存规范化、超限拒绝、答案列表的跑题标记。

这里把鉴权显式关掉（patch settings.auth_required=False）——这个文件测的是核心词，
不是登录；鉴权另有 test_auth_http 覆盖。
"""

import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

try:
    from starlette.testclient import TestClient
except ModuleNotFoundError:  # 空 venv 里没有 starlette
    raise unittest.SkipTest("环境里没有 starlette，跳过 HTTP 层测试（用系统 python 跑全套）")

from geo import db
from geo.api import app as app_module
from geo.api.app import app
from geo.settings import settings
from tests.fixtures import QUESTION, TRACKER, add_answer, build_minimal_db


class KeywordHttpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.path = Path(self.tmp.name) / "kw.db"
        with closing(build_minimal_db(self.path)) as conn:
            add_answer(conn, "2026-11-02T09:00:00+08:00", text="沈阳比较好的律所推荐")
            add_answer(conn, "2026-11-02T09:10:00+08:00", text="今天天气不错，适合散步")
            add_answer(conn, "2026-11-02T09:20:00+08:00", text="关于律师费用的说明")
            conn.commit()

        self.patched = replace(settings, database_path=str(self.path), auth_required=False)
        self._patchers = [
            patch.object(db, "settings", self.patched),
            patch.object(app_module, "settings", self.patched),
        ]
        for p in self._patchers:
            p.start()
        self.client = TestClient(app, raise_server_exceptions=False)

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        self.tmp.cleanup()

    def patch_keywords(self, value):
        return self.client.patch(f"/v1/trackers/{TRACKER}", json={"keywords": value})

    def answers(self):
        response = self.client.get(
            f"/v1/trackers/{TRACKER}/answers?from=2026-11-02&to=2026-11-02&limit=20"
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_patch_normalizes_and_persists(self):
        response = self.patch_keywords([" 律所 ", "律师", "律所", ""])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["keywords"], ["律所", "律师"])

        detail = self.client.get(f"/v1/trackers/{TRACKER}").json()
        self.assertEqual(detail["keywords"], ["律所", "律师"])

    def test_patch_accepts_comma_string(self):
        response = self.patch_keywords("律所，律师")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["keywords"], ["律所", "律师"])

    def test_patch_rejects_too_many(self):
        response = self.patch_keywords([f"词{i}" for i in range(21)])
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["code"], "VALIDATION_ERROR")
        # 被拒绝时不能把原有配置改掉
        self.assertEqual(self.client.get(f"/v1/trackers/{TRACKER}").json()["keywords"], [])

    def test_answers_mark_off_topic_only_when_keywords_configured(self):
        before = self.answers()
        self.assertEqual(before["keywords"], [])
        self.assertTrue(
            all(item["off_topic"] is None for item in before["items"]),
            "没配核心词时必须是「未判定」，不能默认成 False",
        )

        self.assertEqual(self.patch_keywords(["律所", "律师"]).status_code, 200)
        after = self.answers()
        flags = {item["text"]: item["off_topic"] for item in after["items"]}
        self.assertFalse(flags["沈阳比较好的律所推荐"])
        self.assertFalse(flags["关于律师费用的说明"])
        self.assertTrue(flags["今天天气不错，适合散步"])

    def test_clearing_keywords_restores_undetermined(self):
        self.assertEqual(self.patch_keywords(["律所"]).status_code, 200)
        self.assertTrue(self.answers()["items"][0]["off_topic"] in (True, False))

        self.assertEqual(self.patch_keywords([]).status_code, 200)
        self.assertTrue(all(item["off_topic"] is None for item in self.answers()["items"]))


if __name__ == "__main__":
    unittest.main()
