"""报告快照与可比性（FR-027 ~ FR-031）。

两条核心断言：
1. 快照生成后不随数据变化 —— 再插入一条提及，报告数字与哈希都不变，而实时口径会变；
2. 不可比时不输出涨跌幅 —— 模型集合不一致必须给出原因并把 delta 置空。
"""

import unittest

import hashlib
import json

from geo import db, seed
from geo.repositories import report_repository as reports
from geo.repositories import sqlite_repository as repo
from geo.services import metric_service, report_service

TRACKER = "11111111-1111-1111-1111-111111111111"
TENANT = "00000000-0000-0000-0000-000000000001"
NOW = "2026-11-11T10:00:00+08:00"


class ReportSnapshotTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed.seed()
        cls.conn = db.connect()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_snapshot_is_immutable_after_new_samples(self):
        before = report_service.generate(self.conn, TRACKER, TENANT, "2026-11-01", "2026-11-03", NOW)
        self.assertIsNotNone(before)
        frozen_rate = before["payload"]["cards"]["mention_rate"]["value"]
        frozen_hash = before["payload_hash"]
        self.assertEqual(frozen_rate, 0.2222)

        # 插入一条新的提及样本：实时口径应立即变化
        run_id = "run-snapshot-test"
        self.conn.execute(
            """INSERT INTO run(id, tenant_id, tracker_id, trigger, status, scheduled_for, stats)
               VALUES (?, ?, ?, 'manual', 'completed', '2026-11-02T10:00:00+08:00', '{}')""",
            (run_id, TENANT, TRACKER),
        )
        seed.insert_answer(self.conn, "2026-11-02", "kimi", 1, seed.Q1, True, 1, "pos", True, run_id)
        self.conn.commit()

        live = metric_service.build_overview(self.conn, TRACKER, "2026-11-01", "2026-11-03", None, None)
        self.assertGreater(live["cards"]["mention_rate"]["value"], frozen_rate)

        again = reports.get_report(self.conn, before["id"], TENANT)
        self.assertEqual(again["payload_hash"], frozen_hash)
        self.assertEqual(again["payload"]["cards"]["mention_rate"]["value"], frozen_rate)

    def test_comparable_report_reports_delta(self):
        report = report_service.generate(self.conn, TRACKER, TENANT, "2026-11-04", "2026-11-05", NOW)
        comparison = report["payload"]["comparison"]
        self.assertTrue(comparison["comparable"])
        self.assertEqual(comparison["previous_period"], {"from": "2026-11-02", "to": "2026-11-03"})
        self.assertIsNotNone(comparison["delta_pp"])

    def test_incomparable_when_model_set_differs(self):
        report = report_service.generate(self.conn, TRACKER, TENANT, "2026-11-05", "2026-11-06", NOW)
        comparison = report["payload"]["comparison"]
        self.assertFalse(comparison["comparable"])
        self.assertIsNone(comparison["delta_pp"])
        self.assertTrue(any("模型集合不一致" in r for r in comparison["incomparable_reasons"]))

    def test_snapshot_hash_matches_payload(self):
        report = report_service.generate(self.conn, TRACKER, TENANT, "2026-11-01", "2026-11-03", NOW)
        expected = hashlib.sha256(
            json.dumps(report["payload"], ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        self.assertEqual(report["payload_hash"], expected)

    def test_repeated_generation_reuses_same_snapshot(self):
        """同一周期重复生成（含失败重试）不得产生第二份报告。"""
        first = report_service.generate(self.conn, TRACKER, TENANT, "2026-11-02", "2026-11-04", NOW)
        second = report_service.generate(self.conn, TRACKER, TENANT, "2026-11-02", "2026-11-04", NOW)
        self.assertEqual(first["id"], second["id"])
        self.assertTrue(second.get("reused"))
        self.assertEqual(first["payload_hash"], second["payload_hash"])
        count = self.conn.execute(
            """SELECT COUNT(*) FROM report WHERE tracker_id = ? AND period_start = '2026-11-02'
               AND period_end = '2026-11-04' AND metric_version = ?""",
            (TRACKER, first["metric_version"]),
        ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_url_detail_is_deduped_but_counts_accumulate(self):
        """变异检查：插入同 URL 的引用后，明细仍只一行，但次数累计 +1。"""
        summary = repo.citation_summary(self.conn, TRACKER, "2026-11-01", "2026-11-03", None, None)
        before = repo.url_dedup_citations(self.conn, TRACKER, "2026-11-01", "2026-11-03")["items"]
        self.assertEqual(len({r["url"] for r in before}), len(before))

        row = self.conn.execute("SELECT * FROM citation LIMIT 1").fetchone()
        cols = list(row.keys())
        values = [row[k] for k in cols]
        values[cols.index("id")] = "duplicate-citation-test"
        self.conn.execute(
            f"INSERT INTO citation({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})", values
        )
        self.conn.commit()

        after = repo.url_dedup_citations(self.conn, TRACKER, "2026-11-01", "2026-11-03")["items"]
        same = [r for r in after if r["url"] == row["url"]]
        self.assertEqual(len(after), len(before), "重复 URL 不应新增明细行")
        self.assertEqual(len(same), 1)
        self.assertEqual(same[0]["times"], 2, "次数应累计")
        summary_after = repo.citation_summary(self.conn, TRACKER, "2026-11-01", "2026-11-03", None, None)
        self.assertEqual(summary_after["total_citations"], summary["total_citations"] + 1)

    def test_url_detail_totals_cover_all_rows_not_just_page(self):
        """totals 必须是全量口径：items 被 LIMIT 截断时，total_urls/total_occurrences 不能跟着变小。"""
        detail = repo.url_dedup_citations(self.conn, TRACKER, "2026-11-07", "2026-11-30")
        self.assertEqual(len(detail["items"]), 100)
        self.assertGreater(detail["total_urls"], 100)
        self.assertGreater(detail["total_occurrences"], detail["total_urls"], "热点 URL 应让次数累计 > URL 数")
        row = self.conn.execute(
            """SELECT COUNT(DISTINCT c.url) AS u, COUNT(*) AS n
               FROM citation c JOIN answer a ON a.id = c.answer_id
               WHERE a.tracker_id = ? AND date(a.collected_at) BETWEEN ? AND ?""",
            (TRACKER, "2026-11-07", "2026-11-30"),
        ).fetchone()
        self.assertEqual(detail["total_urls"], row["u"])
        self.assertEqual(detail["total_occurrences"], row["n"])


if __name__ == "__main__":
    unittest.main()
