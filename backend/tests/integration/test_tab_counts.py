"""E1d 工作台 tab 计数：一次请求给全 10 个 tab，筛选口径统一。

守三件事：
1. 切问题/模型后数字真的变（不是十张写死的卡片）；
2. 所有数字共用同一套样本条件，tab 之间分母一致；
3. 未接入的模型筛不出东西，返回 0 而不是报错。

不读演示库 geo.db：它是运行产物，干净检出里不存在。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from geo.repositories import sqlite_repository as repo
from tests import fixtures


class TabCountTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = fixtures.build_minimal_db(Path(self.tmp.name) / "tabs.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _counts(self, **kwargs):
        return repo.tab_count_rows(
            self.conn, fixtures.TRACKER, "2026-11-01", "2026-11-30", **kwargs
        )

    def test_all_tabs_share_the_same_sample_filter(self):
        for i in range(3):
            answer_id = fixtures.add_answer(
                self.conn, f"2026-11-10T1{i}:00:00+08:00", model_key="doubao"
            )
            fixtures.add_mention(self.conn, answer_id)
        row = self._counts()
        self.assertEqual(row["samples"], 3)
        self.assertEqual(row["mentions"], 3)
        self.assertEqual(row["days"], 1)
        self.assertEqual(row["questions"], 1)

    def test_model_filter_changes_counts(self):
        for model, n in (("doubao", 3), ("kimi", 2)):
            for i in range(n):
                fixtures.add_answer(
                    self.conn, f"2026-11-1{i}:00:00+08:00", model_key=model
                )
        self.assertEqual(self._counts()["samples"], 5)
        self.assertEqual(self._counts(model="kimi")["samples"], 2)
        self.assertEqual(self._counts(model="qwen")["samples"], 0)

    def test_question_filter_changes_counts(self):
        other = fixtures.add_question(self.conn, "沈阳交通事故律师怎么选")
        fixtures.add_answer(self.conn, "2026-11-10T10:00:00+08:00")
        fixtures.add_answer(
            self.conn,
            "2026-11-10T11:00:00+08:00",
            question_id=other,
        )
        self.assertEqual(self._counts()["samples"], 2)
        self.assertEqual(self._counts(question_id=other)["samples"], 1)
        self.assertEqual(self._counts(question_id=fixtures.QUESTION)["samples"], 1)

    def test_citations_follow_the_same_filter(self):
        answer_id = fixtures.add_answer(
            self.conn, "2026-11-10T10:00:00+08:00", model_key="doubao"
        )
        fixtures.add_answer(
            self.conn, "2026-11-10T11:00:00+08:00", model_key="kimi"
        )
        for i in range(2):
            self.conn.execute(
                """INSERT INTO citation(id, answer_id, url, url_hash, domain, media_name,
                                        media_type, owner_type, position, cite_type)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'reference')""",
                (
                    f"c{i}", answer_id, f"https://example.com/{i}", f"h{i}",
                    "example.com", "示例", "video", "media", i,
                ),
            )
        self.assertEqual(self._counts()["citations"], 2)
        self.assertEqual(self._counts(model="kimi")["citations"], 0)


if __name__ == "__main__":
    unittest.main()
