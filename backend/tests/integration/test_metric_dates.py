"""日期口径：本地自然日，不是 UTC 日。

这条不是学术问题：SQLite 的 `date()` 会把 `2026-11-20T00:30:00+08:00` 转成
`2026-11-19T16:30Z` 再取日期，结果是 11-19。于是一早采的样本从看板上"消失"，
和第二天的数据混在一起。缺口清单也会因此和看板打架。
"""

import tempfile
import unittest
from pathlib import Path

from geo.repositories import import_repository as repo
from geo.services import metric_service
from tests.fixtures import NOW, QUESTION, TENANT, TRACKER, build_minimal_db


class LocalDateTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.conn = build_minimal_db(Path(self._tmp.name) / "geo.db")
        self.addCleanup(self.conn.close)

    def add_answer(self, collected_at: str, text: str = "答案正文"):
        from geo.services.import_service import sha

        return repo.insert_answer(
            self.conn,
            {
                "tenant_id": TENANT,
                "tracker_id": TRACKER,
                "question_id": QUESTION,
                "model_key": "doubao",
                "model_version": None,
                "channel": "api",
                "raw_uri": "test://x",
                "text": text,
                "text_hash": sha(text),
                "collected_at": collected_at,
                "batch_id": None,
                "row_hash": sha(collected_at + text),
            },
        )

    def overview(self, day: str) -> dict:
        return metric_service.build_overview(self.conn, TRACKER, day, day, None, None)

    def test_early_morning_sample_counts_on_local_day(self):
        self.add_answer("2026-11-20T00:30:00+08:00")

        self.assertEqual(self.overview("2026-11-20")["valid_total"], 1)
        self.assertEqual(self.overview("2026-11-19")["valid_total"], 0)

    def test_date_range_uses_local_day(self):
        self.add_answer("2026-11-20T00:30:00+08:00")
        conn_row = self.conn.execute(
            "SELECT MIN(substr(collected_at, 1, 10)) AS d FROM answer"
        ).fetchone()
        self.assertEqual(conn_row["d"], "2026-11-20")

        from geo.repositories import sqlite_repository as sqlrepo

        first, last = sqlrepo.data_date_range(self.conn, TRACKER)
        self.assertEqual((first, last), ("2026-11-20", "2026-11-20"))


if __name__ == "__main__":
    unittest.main()
