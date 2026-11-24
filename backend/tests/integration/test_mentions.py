"""提及重跑的集成测试（003 FR-007：清空重跑语义 + 审计 + 黄金窗口不变）。

两个口径：

- ``MentionRecomputeTest`` 用最小夹具精确数数——旧的自品牌提及被清掉、
  新识别按当前别名落库、竞品提及与无效答案不进重算、审计同事务落账。
- ``GoldenWindowRecomputeTest`` 在种子数据上整库重跑：种子写提及的前提
  （with_brand）同时也是往正文里写品牌名的前提，所以识别器必须一比一
  复现存量提及，黄金窗口 9 样本 2 提及（22.22%）重跑后纹丝不动——这是
  FR-012「黄金窗口手算值不动」对识别器的直接约束。
"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from geo import db, seed
from geo.repositories import sqlite_repository as repo
from geo.services import mention_service
from tests.fixtures import BRAND, TENANT, TRACKER, add_answer, add_mention, build_minimal_db

FULL = "辽宁辽征律师事务所"
COMPETITOR_BRAND = "bcomp0000-0000-0000-0000-000000000001"


class MentionRecomputeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.conn = build_minimal_db(Path(self.tmp.name) / "mentions.db")
        # 夹具自带「辽征律所」(a1)，补齐 v1 口径要考的别名组合
        for alias_id, alias, kind in [
            ("a2", FULL, "full"),
            ("a3", "辽征", "short"),
            ("a4", "LiaoZheng", "en"),
            ("a5", "辽证律所", "misspell"),
        ]:
            self.conn.execute(
                "INSERT INTO brand_alias(id, brand_id, alias, alias_type) VALUES (?, ?, ?, ?)",
                (alias_id, BRAND, alias, kind),
            )
        # 竞品提及的 brand_id 外键需要品牌行存在
        self.conn.execute(
            "INSERT INTO brand(id, tenant_id, name, created_at) VALUES (?, ?, ?, ?)",
            (COMPETITOR_BRAND, TENANT, "北京大成（沈阳）律师事务所", "2026-11-24T12:00:00+08:00"),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def own_rows(self):
        return self.conn.execute(
            """SELECT a.id, m.matched_alias, m.match_type, m.position, m.sentiment
               FROM mention m JOIN answer a ON a.id = m.answer_id
               WHERE a.tracker_id = ? AND m.subject_type = 'own'
               ORDER BY a.collected_at""",
            (TRACKER,),
        ).fetchall()

    def test_recompute_replaces_own_mentions_only(self):
        listed = add_answer(
            self.conn,
            "2026-11-20T09:00:00+08:00",
            text="沈阳较受关注的律所包括：\n1. 辽宁辽征律师事务所\n2. 某某律师事务所（示例 2）",
        )
        prose = add_answer(
            self.conn, "2026-11-21T09:00:00+08:00", text="本地人遇到刑事案子，常推荐辽征。"
        )
        add_answer(
            self.conn, "2026-11-22T09:00:00+08:00", text="沈阳有多家律所，建议先电话咨询。"
        )
        add_answer(
            self.conn, "2026-11-23T09:00:00+08:00", text="liaozheng has good reviews on local forums."
        )
        add_answer(self.conn, "2026-11-24T09:00:00+08:00", text="不建议选辽征，差评不少。")
        # 存量脏数据：正文里没有品牌却挂着自品牌提及（旧口径误标），以及一条竞品提及
        clean_id = self.conn.execute(
            "SELECT id FROM answer WHERE collected_at LIKE '2026-11-22%'"
        ).fetchone()["id"]
        add_mention(self.conn, clean_id, alias=FULL)
        add_mention(
            self.conn,
            listed,
            subject_type="competitor",
            brand_id=COMPETITOR_BRAND,
            alias="北京大成（沈阳）律师事务所",
        )

        summary = mention_service.recompute(self.conn, tenant_id=TENANT, tracker_id=TRACKER)
        self.conn.commit()

        self.assertEqual(summary["answers_scanned"], 5)
        self.assertEqual(summary["mentions_before"], 1)
        self.assertEqual(summary["mentions_deleted"], 1)
        self.assertEqual(summary["mentions_matched"], 4)
        self.assertEqual(summary["aliases"], 6)  # 品牌名 + 夹具 a1 + 测试补的 4 个

        own = self.own_rows()
        self.assertEqual(len(own), 4)
        self.assertEqual(own[0]["matched_alias"], FULL)
        self.assertEqual(own[0]["match_type"], "exact")
        self.assertEqual(own[0]["position"], 1)
        self.assertEqual(own[0]["sentiment"], "neu")
        self.assertEqual(own[1]["matched_alias"], "辽征")
        self.assertIsNone(own[1]["position"])
        self.assertEqual(own[2]["matched_alias"], "LiaoZheng")
        self.assertEqual(own[2]["match_type"], "normalized")
        self.assertEqual(own[3]["matched_alias"], "辽征")
        self.assertEqual(own[3]["sentiment"], "neg")

        # 竞品提及不归识别器管（v1 只做自品牌），原样保留
        kept = self.conn.execute(
            "SELECT COUNT(*) FROM mention WHERE subject_type = 'competitor'"
        ).fetchone()[0]
        self.assertEqual(kept, 1)

        # 审计与删改同一事务：before 是删前快照，after 是识别后的口径
        audit = self.conn.execute(
            "SELECT action, object_type, object_id, before_json, after_json "
            "FROM audit_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(audit["action"], "mention.recompute")
        self.assertEqual(audit["object_type"], "tracker")
        self.assertEqual(audit["object_id"], TRACKER)
        self.assertEqual(json.loads(audit["before_json"]), {"own_mentions": 1})
        self.assertEqual(json.loads(audit["after_json"])["own_mentions"], 4)

    def test_invalid_answers_are_out_of_scope(self):
        answer = add_answer(self.conn, "2026-11-20T09:00:00+08:00", text="辽征出现在正文里")
        self.conn.execute(
            "UPDATE answer SET is_valid = 0, invalid_reason = 'duplicate' WHERE id = ?",
            (answer,),
        )
        self.conn.commit()

        summary = mention_service.recompute(self.conn, tenant_id=TENANT, tracker_id=TRACKER)
        self.conn.commit()

        # 指标分母不认无效样本，识别也不认
        self.assertEqual(summary["answers_scanned"], 0)
        self.assertEqual(summary["mentions_matched"], 0)
        self.assertEqual(self.own_rows(), [])

    def test_unknown_tracker_returns_none(self):
        self.assertIsNone(
            mention_service.recompute(self.conn, tenant_id=TENANT, tracker_id="nope")
        )

    def test_wrong_tenant_returns_none(self):
        self.assertIsNone(
            mention_service.recompute(self.conn, tenant_id="other", tracker_id=TRACKER)
        )


class GoldenWindowRecomputeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed.seed()
        cls.conn = db.connect()
        cls.summary = mention_service.recompute(
            cls.conn,
            tenant_id=seed.TENANT,
            tracker_id=seed.TRACKER,
            now="2026-11-24T12:00:00+08:00",
        )
        cls.conn.commit()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_recompute_reproduces_all_seeded_own_mentions(self):
        # 种子用 with_brand 同时控制「正文含品牌名」与「写提及行」——
        # 识别器在整库上重跑必须一比一复现，多一条少一条都是口径 bug
        self.assertGreater(self.summary["mentions_before"], 0)
        self.assertEqual(self.summary["mentions_matched"], self.summary["mentions_before"])

    def test_golden_window_is_untouched(self):
        window = repo.window_counts(self.conn, seed.TRACKER, "2026-11-01", "2026-11-03")
        self.assertEqual(window["valid_total"], 9)
        self.assertEqual(window["mention_total"], 2)
