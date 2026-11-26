"""真实采集与主口径隔离的行为测试（2026-11-26 接入真实数据时确立）。

核心承诺：真采集进独立试点对象，绝不混进演示主对象——
黄金窗口 9/22.22%、报告快照、富窗口样本数全部按 tracker_id 隔离，
加多少条真实数据都不该让它们动一分。

这里不调真实 API（那要联网与 key），只验证「来源标记 + 按对象隔离」这条
不变量：import 通道写入一条 collect://raw/ 来源的答案，确认它只影响自己
那个对象。
"""

import tempfile
import unittest
from pathlib import Path

from tests import fixtures
from geo import data_provenance as dp
from geo.repositories import import_repository as import_repo
from geo.services import metric_service

REAL_TRACKER = "r0000000-0000-0000-0000-000000000001"
RAW_URI = "collect://raw/2026-11-26/deepseek-test.json"
DAY = "2026-11-26T09:00:00+08:00"


class RealCollectionIsolationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = fixtures.build_minimal_db(Path(self.tmp.name) / "iso.db")
        # 独立试点对象：复用同一品牌与问题集结构，只换 id。
        self.conn.execute(
            """INSERT INTO tracker(id, tenant_id, brand_id, name, created_at, updated_at)
               VALUES (?, ?, ?, '真实采集试点', ?, ?)""",
            (REAL_TRACKER, fixtures.TENANT, fixtures.BRAND, fixtures.NOW, fixtures.NOW),
        )
        qs = "q0000000-0000-0000-0000-000000000099"
        self.conn.execute(
            "INSERT INTO question_set(id, tenant_id, tracker_id, name, created_at) VALUES (?, ?, ?, '试点问题集', ?)",
            (qs, fixtures.TENANT, REAL_TRACKER, fixtures.NOW),
        )
        self.conn.execute("UPDATE tracker SET active_question_set_id = ? WHERE id = ?", (qs, REAL_TRACKER))
        self.conn.execute(
            """INSERT INTO question(id, tenant_id, question_set_id, text, normalized_text, tier, created_at)
               VALUES (?, ?, ?, ?, ?, 'core', ?)""",
            (fixtures.QUESTION + "-r", fixtures.TENANT, qs,
             fixtures.QUESTION_TEXT, fixtures.QUESTION_TEXT, fixtures.NOW),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def add_real_answer(self, tracker_id, question_id):
        body = "真实平台返回的回答内容"
        return import_repo.insert_answer(self.conn, {
            "tenant_id": fixtures.TENANT,
            "tracker_id": tracker_id,
            "question_id": question_id,
            "model_key": "deepseek",
            "model_version": None,
            "channel": "api",
            "raw_uri": RAW_URI,
            "text": body,
            "text_hash": fixtures.sha(body),
            "collected_at": DAY,
            "batch_id": None,
            "row_hash": fixtures.sha("|".join([tracker_id, question_id, "deepseek", DAY, body])),
            "client": "web",
        })

    def test_real_answer_marked_real_and_stays_in_own_tracker(self):
        self.add_real_answer(REAL_TRACKER, fixtures.QUESTION + "-r")
        rows = self.conn.execute(
            "SELECT raw_uri, COUNT(*) AS n FROM answer WHERE tracker_id = ? GROUP BY raw_uri",
            (REAL_TRACKER,),
        ).fetchall()
        by_kind = {}
        for row in rows:
            kind = dp.source_of(dict(row))
            by_kind[kind] = by_kind.get(kind, 0) + row["n"]
        self.assertEqual(by_kind.get(dp.REAL), 1, "真实采集必须标记为 real")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM answer WHERE tracker_id = ?", (fixtures.TRACKER,)).fetchone()[0],
            0, "真采集不得混进演示主对象",
        )

    def test_golden_metrics_unchanged_by_real_collection(self):
        before = metric_service.build_overview(
            self.conn, fixtures.TRACKER, "2026-11-01", "2026-11-30", None, None
        )["valid_total"]
        self.add_real_answer(REAL_TRACKER, fixtures.QUESTION + "-r")
        after = metric_service.build_overview(
            self.conn, fixtures.TRACKER, "2026-11-01", "2026-11-30", None, None
        )["valid_total"]
        self.assertEqual(before, after, "真采集不得改变演示主对象的指标")
        real_total = metric_service.build_overview(
            self.conn, REAL_TRACKER, "2026-11-26", "2026-11-26", None, None
        )["valid_total"]
        self.assertEqual(real_total, 1, "试点对象自己应能看到那条真采集")


if __name__ == "__main__":
    unittest.main()
