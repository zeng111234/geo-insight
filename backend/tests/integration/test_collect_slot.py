"""D3 的验收测试：槽位幂等、超额拒绝、缺口归零、采集后自动识别提及。

全部用离线答案库（``csv_bank``）跑，不发一个网络请求、不花一分钱。
这不是"退而求其次"——D3 的三条判据里，「同日重跑不产生重复样本」和
「缺口清单归零」本身就是纯账务问题，跟平台返回什么无关。

**为什么要有 test_incremental_matches_full_recompute**：
采集后的识别走 ``detect_for_answer``（只算一条），人工触发走 ``recompute``（全量重扫）。
两条路径读同一个 parser，但一条是"新增的这条"，另一条是"所有答案"。
分叉过一次的话，看板上的提及率会随采集量与重算时机跳动，而且极难发现。
所以这里直接对拍：采集完的提及数，必须等于全量重算后的提及数。
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from geo.collectors.base import CollectorError
from geo.repositories import import_repository as import_repo
from geo.services import collect_service, mention_service
from geo.services.import_service import sha
from tests import fixtures

CSV_HEADER = "question,model,answer\n"
BRAND_ANSWER = "综合来看，辽宁辽征律师事务所是沈阳口碑较好的律所之一。"
PLAIN_ANSWER = "沈阳的律所很多，建议按案件类型选择。"


class CollectSlotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.conn = fixtures.build_minimal_db(self.root / "t.db")
        # 计划：core 问题 3 次/天，模型 doubao
        fixtures.add_plan(self.conn)

        self.csv = self.root / "bank.csv"
        self.write_csv(BRAND_ANSWER)

        self.env = mock.patch.dict(os.environ, {"GEO_COLLECTOR_CSV": str(self.csv)})
        self.env.start()

        # LIFO：最后加的先跑，所以要 conn.close 先于 tmp.cleanup
        self.addCleanup(self.env.stop)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.conn.close)

    def write_csv(self, answer: str) -> None:
        self.csv.write_text(
            CSV_HEADER + f"{fixtures.QUESTION_TEXT},doubao,{answer}\n", encoding="utf-8"
        )

    def collect_one(self, **kwargs):
        return collect_service.run_once(
            self.conn,
            tracker_id=fixtures.TRACKER,
            question_text=fixtures.QUESTION_TEXT,
            model_key="doubao",
            tenant_id=fixtures.TENANT,
            **kwargs,
        )

    def count(self, sql: str) -> int:
        return self.conn.execute(sql).fetchone()[0]

    # -- 槽位 ---------------------------------------------------------------

    def test_slots_increment_within_the_same_day(self):
        """同一天连采两次是两条独立样本，序号 1 和 2，不是重复。"""
        first = self.collect_one()
        second = self.collect_one()

        self.assertTrue(first["inserted"])
        self.assertTrue(second["inserted"])
        self.assertEqual(first["sample_idx"], 1)
        self.assertEqual(second["sample_idx"], 2)
        self.assertEqual(first["sample_day"], second["sample_day"])
        self.assertEqual(self.count("SELECT COUNT(*) FROM answer"), 2)

    def test_over_plan_is_rejected(self):
        """计划 3 次，第 4 次必须拒绝——而且要在调用平台之前拒绝（不花钱）。"""
        for _ in range(3):
            self.collect_one()

        with self.assertRaises(CollectorError) as caught:
            self.collect_one()
        self.assertEqual(caught.exception.code, "NO_SLOT")
        self.assertEqual(self.count("SELECT COUNT(*) FROM answer"), 3)

    def test_force_allows_over_plan_but_still_uses_new_slot(self):
        """补采是"再加一条"，不是覆盖已有的那一条。"""
        for _ in range(3):
            self.collect_one()

        extra = self.collect_one(allow_over=True)
        self.assertTrue(extra["inserted"])
        self.assertEqual(extra["sample_idx"], 4)
        self.assertEqual(self.count("SELECT COUNT(*) FROM answer"), 4)

    def test_unique_index_blocks_duplicate_slot(self):
        """唯一索引是最后一道闸：绕过应用层直接写同一槽位也写不进去。

        这条测的不是"代码有没有判重"，而是"数据库会不会兜住"——
        并发下两个循环都算出同一个序号是可能的，代码判重来不及。
        """
        day = "2026-11-25"
        payload = {
            "tenant_id": fixtures.TENANT,
            "tracker_id": fixtures.TRACKER,
            "question_id": fixtures.QUESTION,
            "model_key": "doubao",
            "channel": "api",
            "raw_uri": "test://1",
            "text": "第一条",
            "text_hash": sha("第一条"),
            "collected_at": f"{day}T09:00:00+08:00",
            "batch_id": None,
            "row_hash": sha("row-1"),
            "sample_day": day,
            "sample_idx": 1,
        }
        import_repo.insert_answer(self.conn, payload)

        with self.assertRaises(sqlite3.IntegrityError):
            import_repo.insert_answer(
                self.conn, {**payload, "text": "第二条", "row_hash": sha("row-2")}
            )

    def test_imported_answer_has_no_slot(self):
        """导入的答案不知道是当天第几次，两列必须是 NULL，不能填 0。"""
        answer_id = fixtures.add_answer(self.conn, fixtures.NOW, model_key="doubao")
        row = self.conn.execute(
            "SELECT sample_day, sample_idx FROM answer WHERE id = ?", (answer_id,)
        ).fetchone()
        self.assertIsNone(row["sample_day"])
        self.assertIsNone(row["sample_idx"])

    # -- 缺口驱动 ------------------------------------------------------------

    def test_fill_gaps_reaches_zero(self):
        stats = collect_service.fill_gaps(
            self.conn, tracker_id=fixtures.TRACKER, model_key="doubao", tenant_id=fixtures.TENANT
        )
        self.assertEqual(stats["planned_missing"], 3)
        self.assertEqual(stats["inserted"], 3)
        self.assertEqual(stats["failed"], 0)
        self.assertEqual(stats["remaining_missing"], 0)

    def test_second_round_collects_nothing(self):
        """采满之后再跑一轮：一条都不采。这就是「同日重跑不产生重复样本」。"""
        collect_service.fill_gaps(
            self.conn, tracker_id=fixtures.TRACKER, model_key="doubao", tenant_id=fixtures.TENANT
        )
        again = collect_service.fill_gaps(
            self.conn, tracker_id=fixtures.TRACKER, model_key="doubao", tenant_id=fixtures.TENANT
        )

        self.assertEqual(again["planned_missing"], 0)
        self.assertEqual(again["inserted"], 0)
        self.assertEqual(again["remaining_missing"], 0)
        self.assertEqual(self.count("SELECT COUNT(*) FROM answer"), 3)

    def test_identical_answers_are_still_separate_samples(self):
        """平台常常逐字返回同样的答案。3 次采样就是 3 条，不能因为文本相同就合并。

        曾经用 row_hash 判重会把第 2、3 次判成重复，样本数永远够不到计划量。
        """
        stats = collect_service.fill_gaps(
            self.conn, tracker_id=fixtures.TRACKER, model_key="doubao", tenant_id=fixtures.TENANT
        )
        self.assertEqual(stats["inserted"], 3)
        texts = [r[0] for r in self.conn.execute("SELECT text FROM answer")]
        self.assertEqual(len(set(texts)), 1, "三条文本应当完全相同")
        self.assertEqual(len(texts), 3)

    # -- 采集后识别提及 -------------------------------------------------------

    def test_mention_detected_right_after_collect(self):
        result = self.collect_one()
        self.assertTrue(result["mention_matched"])
        self.assertIn("提及品牌", result["message"])
        self.assertEqual(self.count("SELECT COUNT(*) FROM mention"), 1)

        row = self.conn.execute("SELECT subject_type, sentiment FROM mention").fetchone()
        self.assertEqual(row["subject_type"], "own")
        self.assertEqual(row["sentiment"], "neu")

    def test_no_mention_when_brand_absent(self):
        self.write_csv(PLAIN_ANSWER)
        result = self.collect_one()
        self.assertFalse(result["mention_matched"])
        self.assertIn("未提及品牌", result["message"])
        self.assertEqual(self.count("SELECT COUNT(*) FROM mention"), 0)

    def test_incremental_matches_full_recompute(self):
        """采集时的增量识别，必须与全量重算得到同一个提及数。

        两条路径读同一个 parser，但一条只看新采的这条、另一条扫全部。
        分叉了的话，看板上的提及率会随"什么时候点了重算"而跳。
        """
        for _ in range(3):
            self.collect_one()
        incremental = self.count("SELECT COUNT(*) FROM mention")

        summary = mention_service.recompute(
            self.conn, tenant_id=fixtures.TENANT, tracker_id=fixtures.TRACKER
        )
        full = self.count("SELECT COUNT(*) FROM mention")

        self.assertEqual(incremental, 3)
        self.assertEqual(full, incremental)
        self.assertEqual(summary["mentions_matched"], incremental)

    def test_detect_is_idempotent_for_same_answer(self):
        """同一条答案重复识别不该长出第二条提及（mention 有 UNIQUE(answer_id, brand_id)）。"""
        result = self.collect_one()
        answer_id = result["answer_id"]

        first = mention_service.detect_for_answer(
            self.conn, tenant_id=fixtures.TENANT, tracker_id=fixtures.TRACKER, answer_id=answer_id
        )
        second = mention_service.detect_for_answer(
            self.conn, tenant_id=fixtures.TENANT, tracker_id=fixtures.TRACKER, answer_id=answer_id
        )

        self.assertTrue(first["matched"])
        self.assertTrue(second["matched"])
        self.assertEqual(self.count("SELECT COUNT(*) FROM mention"), 1)


class FillGapsAbortTests(unittest.TestCase):
    """一轮采不完是常态，但"该不该继续打"要分得清。

    限流/超时/5xx：平台整体不舒服，继续打只会更糟，连续几次就收手。
    配置类错误（问题不在离线库里）：只影响那一条，其余问题照常采。
    两者混为一谈的结果是：要么被一次网络抖动打断整轮，要么在限流里空转几十次。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = fixtures.build_minimal_db(Path(self.tmp.name) / "t.db")
        fixtures.add_plan(self.conn, core_times=3)
        fixtures.add_question(self.conn, "沈阳合同纠纷律师哪家好", tier="long_tail")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.conn.close)

    def fill(self):
        return collect_service.fill_gaps(
            self.conn, tracker_id=fixtures.TRACKER, model_key="doubao", tenant_id=fixtures.TENANT
        )

    def test_stops_after_repeated_rate_limit(self):
        calls: list[str] = []

        def boom(*_args, **kwargs):
            calls.append(kwargs.get("question_text", ""))
            raise CollectorError("RATE_LIMITED", "限流")

        with mock.patch.object(collect_service, "run_once", side_effect=boom):
            stats = self.fill()

        self.assertTrue(stats["aborted"])
        self.assertEqual(len(calls), collect_service.MAX_RETRYABLE_FAILURES)
        self.assertEqual(stats["errors"][0]["code"], "RATE_LIMITED")
        # 一条都没采到，缺口原样
        self.assertEqual(stats["remaining_missing"], stats["planned_missing"])

    def test_config_errors_do_not_abort(self):
        """离线库里没有的问题会一直 NOT_FOUND，但不能因此停下——

        否则一个写错的问题文本会让整轮采集饿死，而真正能采的问题排在它后面。
        """
        calls: list[str] = []

        def not_found(*_args, **kwargs):
            calls.append(kwargs.get("question_text", ""))
            raise CollectorError("NOT_FOUND", "离线库里没有")

        with mock.patch.object(collect_service, "run_once", side_effect=not_found):
            stats = self.fill()

        self.assertFalse(stats["aborted"])
        # 4 个槽位（core 3 + 长尾 1）全部试过
        self.assertEqual(len(calls), 4)


class FillGapsWithoutPlanTests(unittest.TestCase):
    def test_missing_plan_is_a_config_error(self):
        """没配采样计划时，缺口算不出来——要报配置错误，不是静默采一堆。"""
        with tempfile.TemporaryDirectory() as tmp:
            conn = fixtures.build_minimal_db(Path(tmp) / "t.db")
            try:
                with self.assertRaises(ValueError) as caught:
                    collect_service.fill_gaps(conn, tracker_id=fixtures.TRACKER, model_key="doubao")
            finally:
                conn.close()
        self.assertIn("采样计划", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
