"""P2 人工纠正的验收测试。

核心主张：纠正记录在**独立表**，不写回 mention——黄金窗口的 19 项手算值
基于自动识别，任何"点了纠正数字就变了"的行为都是口径事故。
所以断言里既要有"纠正真落库可回显"，也要有"mention 表一行没动"。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from geo import db, migrate
from geo.services import audit_service, correction_service
from tests import fixtures
from tests.fixtures import TENANT, build_minimal_db

EMPTY_PAYLOAD = {"note": "人工确认"}


class CorrectionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "t.db"
        self.conn = build_minimal_db(self.db_path)
        self.addCleanup(self.conn.close)
        # fixture 只建到 question 层；纠正挂在 answer 上，自己造一条真 answer + mention。
        self.answer_id = "correction-answer-1"
        self.conn.execute(
            """INSERT INTO answer(id, tenant_id, tracker_id, question_id, model_key, channel,
                                  raw_uri, text, text_hash, collected_at)
               VALUES (?, ?, ?, ?, 'deepseek', 'web', 'collect://test', '测试回答', 'h1', ?)""",
            (
                self.answer_id, TENANT, fixtures.TRACKER, fixtures.QUESTION, fixtures.NOW,
            ),
        )
        self.conn.execute(
            """INSERT INTO mention(id, answer_id, brand_id, subject_type, matched_alias,
                                   match_type, sentiment, snippet)
               VALUES ('m1', ?, ?, 'own', '辽征律所', 'exact', 'neu', '测试回答')""",
            (self.answer_id, fixtures.BRAND),
        )
        self.conn.commit()

    def _answer_id(self) -> str:
        return self.answer_id

    def _mention_snapshot(self):
        return self.conn.execute(
            "SELECT id, matched_alias, sentiment, position FROM mention ORDER BY id"
        ).fetchall()

    def test_write_then_read_echoes_all_fields(self):
        aid = self._answer_id()
        before = self._mention_snapshot()
        result = correction_service.upsert_correction(
            self.conn,
            answer_id=aid,
            tenant_id=TENANT,
            payload={"own_mentioned": 1, "sentiment": "pos", "position": 2, "note": "人工确认提及"},
            corrected_by="tester",
        )
        self.conn.commit()
        self.assertEqual(result["own_mentioned"], 1)
        self.assertEqual(result["sentiment"], "pos")
        self.assertEqual(result["position"], 2)
        self.assertEqual(result["note"], "人工确认提及")
        self.assertEqual(result["corrected_by"], "tester")

        read = correction_service.get_correction(self.conn, aid, TENANT)
        self.assertEqual(read["note"], "人工确认提及")
        # 关键：mention 一行没动，指标基准不受影响
        self.assertEqual(self._mention_snapshot(), before)

    def test_upsert_overwrites_and_keeps_untouched_fields(self):
        aid = self._answer_id()
        correction_service.upsert_correction(
            self.conn, answer_id=aid, tenant_id=TENANT,
            payload={"own_mentioned": 1, "sentiment": "pos", "position": 3, "note": "第一版"},
        )
        self.conn.commit()
        # 第二次只改 note：位次/情感应沿用，不该被清空
        result = correction_service.upsert_correction(
            self.conn, answer_id=aid, tenant_id=TENANT, payload={"note": "第二版"}
        )
        self.conn.commit()
        self.assertEqual(result["note"], "第二版")
        self.assertEqual(result["position"], 3)
        self.assertEqual(result["sentiment"], "pos")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM answer_correction WHERE answer_id=?", (aid,)).fetchone()[0],
            1,
        )

    def test_upsert_writes_audit_with_before_snapshot(self):
        aid = self._answer_id()
        correction_service.upsert_correction(
            self.conn, answer_id=aid, tenant_id=TENANT, payload={"note": "旧"}
        )
        self.conn.commit()
        correction_service.upsert_correction(
            self.conn, answer_id=aid, tenant_id=TENANT, payload={"note": "新"}
        )
        self.conn.commit()
        rows = self.conn.execute(
            "SELECT before_json, after_json FROM audit_log WHERE action='answer.correction' ORDER BY id"
        ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertIn("旧", rows[1]["before_json"])
        self.assertIn("新", rows[1]["after_json"])

    def test_rejects_answer_from_other_tenant(self):
        aid = self._answer_id()
        with self.assertRaises(correction_service.CorrectionError) as ctx:
            correction_service.upsert_correction(
                self.conn, answer_id=aid, tenant_id="00000000-0000-0000-0000-999999999999",
                payload=EMPTY_PAYLOAD,
            )
        self.assertEqual(ctx.exception.code, "NOT_FOUND")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM answer_correction").fetchone()[0], 0)

    def test_rejects_bad_values(self):
        aid = self._answer_id()
        for payload, code in [
            ({"sentiment": "happy"}, "BAD_SENTIMENT"),
            ({"position": 0}, "BAD_POSITION"),
            ({"own_mentioned": 2}, "BAD_OWN_MENTIONED"),
            ({}, "EMPTY_CORRECTION"),
        ]:
            with self.subTest(payload=payload):
                with self.assertRaises(correction_service.CorrectionError) as ctx:
                    correction_service.upsert_correction(
                        self.conn, answer_id=aid, tenant_id=TENANT, payload=payload
                    )
                self.assertEqual(ctx.exception.code, code)

    def test_missing_answer_is_not_found(self):
        with self.assertRaises(correction_service.CorrectionError) as ctx:
            correction_service.upsert_correction(
                self.conn, answer_id="no-such-answer", tenant_id=TENANT, payload=EMPTY_PAYLOAD
            )
        self.assertEqual(ctx.exception.code, "NOT_FOUND")


class CorrectionMigrationTest(unittest.TestCase):
    """迁移本身要能在真实链上建出表，不能只在测试 fixture 上过。"""

    def test_real_chain_creates_correction_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = __import__("sqlite3").connect(Path(tmp) / "fresh.db")
            try:
                migrate.apply(conn)
                cols = {
                    r[1] for r in conn.execute("PRAGMA table_info(answer_correction)").fetchall()
                }
                for expected in ("answer_id", "own_mentioned", "sentiment", "position", "note"):
                    self.assertIn(expected, cols)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
