# -*- coding: utf-8 -*-
"""H2 收尾的验收测试(003 US2:T004 问题集绑定 / T005 引用类型筛选)。

H2-6 的关键断言不是「能切换」,而是「切换之后口径整体跟着走且能切回来」:
问题列表、指标数字、核心词(跑题判定的输入)三者同步变化,切回主集后
黄金窗口(9 样本 / 22.22%)分毫不动——这是 spec 的硬验收。

H2-7 的关键断言是「条数与手工 SQL 一致」:接口筛选的 totals 拿
`SELECT COUNT` 对拍,不是拿接口自己的输出自我印证。
"""

import json
import sqlite3
import unittest
from contextlib import closing

try:
    from starlette.testclient import TestClient
except ModuleNotFoundError:
    raise unittest.SkipTest("环境里没有 starlette,跳过 HTTP 层测试(用系统 python 跑全套)")

from geo import db, seed
from geo.api.app import app
from geo.settings import settings

FORM = {"Content-Type": "application/x-www-form-urlencoded"}
TRACKER = "11111111-1111-1111-1111-111111111111"


@unittest.skipUnless(settings.auth_required, "GEO_AUTH_REQUIRED=0,鉴权被显式关闭")
class H2QuestionSetSwitchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed.seed()
        with closing(db.connect()) as conn:
            from geo.services import auth_service

            auth_service.ensure_admin_password(conn)
        cls.client = TestClient(app)
        body = f"email={settings.admin_email}&password={settings.admin_password}"
        cls.client.post("/login", content=body, headers=FORM, follow_redirects=False)
        cls.client.headers.update({"X-CSRF-Token": cls.client.cookies.get("geo_csrf", "")})

    def setUp(self):
        # 每条用例前把生效集切回主集,避免用例间串扰
        self.client.patch(
            f"/v1/trackers/{TRACKER}",
            json={"active_question_set_id": seed.QUESTION_SET},
        )

    # ------------------------------------------------------------------

    def test_question_sets_listed_with_counts(self):
        response = self.client.get(f"/v1/trackers/{TRACKER}/question-sets")

        self.assertEqual(response.status_code, 200)
        items = response.json()["items"]
        # 同库其它用例可能新建过集,只断言种子里的两个(顺序无关)
        by_name = {i["name"]: i for i in items}
        self.assertIn("律所核心问题", by_name)
        self.assertIn("长尾扩展集", by_name)
        main = by_name["律所核心问题"]
        self.assertEqual(main["question_total"], 12)
        self.assertTrue(main["active"])
        extra = by_name["长尾扩展集"]
        self.assertEqual(extra["question_total"], 2)
        # 只有一个集处于 active(切换语义不能出现双生效)
        self.assertEqual(sum(1 for i in items if i["active"]), 1)
        self.assertFalse(extra["active"])
        # 核心词随集展示(spec Edge:不同集核心词可以不同)
        self.assertIn("刑事辩护", main["keywords"])
        self.assertIn("合同纠纷", extra["keywords"])

    def test_switch_syncs_questions_metrics_and_keywords(self):
        sets = self.client.get(f"/v1/trackers/{TRACKER}/question-sets").json()["items"]
        extra_id = next(i["id"] for i in sets if not i["active"])

        # 切换前:主集口径(黄金窗口)
        before_questions = self.client.get(f"/v1/trackers/{TRACKER}/questions").json()
        before_metrics = self.client.get(
            f"/v1/trackers/{TRACKER}/metrics/overview?from=2026-11-01&to=2026-11-03"
        ).json()
        before_detail = self.client.get(f"/v1/trackers/{TRACKER}").json()

        response = self.client.patch(
            f"/v1/trackers/{TRACKER}", json={"active_question_set_id": extra_id}
        )
        self.assertEqual(response.status_code, 200)

        # 1. 问题列表同步:12 → 2,且是新集的问题
        after_questions = self.client.get(f"/v1/trackers/{TRACKER}/questions").json()
        self.assertEqual(len(after_questions["items"]), 2)
        texts = {q["text"] for q in after_questions["items"]}
        self.assertIn("沈阳合同纠纷律师哪家好", texts)

        # 2. 指标口径同步:扩展集没有答案,黄金窗口变 0 样本
        after_metrics = self.client.get(
            f"/v1/trackers/{TRACKER}/metrics/overview?from=2026-11-01&to=2026-11-03"
        ).json()
        self.assertEqual(before_metrics["valid_total"], 9)
        self.assertEqual(after_metrics["valid_total"], 0)

        # 3. 核心词同步:详情读的是生效集
        after_detail = self.client.get(f"/v1/trackers/{TRACKER}").json()
        self.assertIn("合同纠纷", after_detail["keywords"])
        self.assertNotIn("刑事辩护", after_detail["keywords"])

        # 4. 问题的旧计数只是历史,不误导读侧
        self.assertEqual(before_questions["summary"]["total"], 12)

    def test_switch_back_restores_golden_window(self):
        sets = self.client.get(f"/v1/trackers/{TRACKER}/question-sets").json()["items"]
        extra_id = next(i["id"] for i in sets if not i["active"])
        self.client.patch(f"/v1/trackers/{TRACKER}", json={"active_question_set_id": extra_id})

        self.client.patch(f"/v1/trackers/{TRACKER}", json={"active_question_set_id": seed.QUESTION_SET})
        impact = self.client.get(
            f"/v1/articles/{seed.ART_PUBLISHED}/impact"
        ).json()
        # 切走再切回,黄金窗口手算值必须分毫不动(22.22% / 9 样本)
        self.assertEqual(impact["before"]["valid_total"], 9)
        self.assertEqual(impact["before"]["mention_rate"]["value"], 0.2222)

    def test_switch_to_foreign_set_is_rejected(self):
        # 集不属于此 tracker:同租户另一对象的集,必须 400(不是静默生效)
        response = self.client.patch(
            f"/v1/trackers/{TRACKER}", json={"active_question_set_id": seed.QSET2}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("不属于", response.json()["message"])

    def test_keywords_patch_writes_active_set(self):
        # H2-6 后核心词属于问题集:PATCH 走老入口,但落点必须是生效集
        response = self.client.patch(
            f"/v1/trackers/{TRACKER}", json={"keywords": ["律所", "新核心词"]}
        )
        self.assertEqual(response.status_code, 200)
        with closing(db.connect()) as conn:
            kw = conn.execute(
                "SELECT keywords FROM question_set WHERE id = ?", (seed.QUESTION_SET,)
            ).fetchone()[0]
        self.assertIn("新核心词", json.loads(kw))
        detail = self.client.get(f"/v1/trackers/{TRACKER}").json()
        self.assertIn("新核心词", detail["keywords"])
        # 恢复种子核心词,别污染同类的其它用例(共享库只 seed 一次)
        self.client.patch(
            f"/v1/trackers/{TRACKER}",
            json={"keywords": ["律所", "律师", "刑事辩护", "离婚", "劳动仲裁"]},
        )

    def test_create_question_set_then_switch(self):
        response = self.client.post(
            f"/v1/trackers/{TRACKER}/question-sets", json={"name": "测试新集", "keywords": "律所, 测试"}
        )
        self.assertEqual(response.status_code, 201)
        new_id = response.json()["id"]

        switch = self.client.patch(
            f"/v1/trackers/{TRACKER}", json={"active_question_set_id": new_id}
        )
        # 带上响应体：500 时中间件把异常原文放在 message 里，
        # 不打印的话只看到"500 != 200"，等于没有信息。
        self.assertEqual(switch.status_code, 200, switch.text)
        # 新集是空的:问题列表清零,指标 0 样本
        self.assertEqual(len(self.client.get(f"/v1/trackers/{TRACKER}/questions").json()["items"]), 0)
        # 切回主集
        self.client.patch(f"/v1/trackers/{TRACKER}", json={"active_question_set_id": seed.QUESTION_SET})


@unittest.skipUnless(settings.auth_required, "GEO_AUTH_REQUIRED=0,鉴权被显式关闭")
class H2CitationTypeFilterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed.seed()
        with closing(db.connect()) as conn:
            from geo.services import auth_service

            auth_service.ensure_admin_password(conn)
        cls.client = TestClient(app)
        body = f"email={settings.admin_email}&password={settings.admin_password}"
        cls.client.post("/login", content=body, headers=FORM, follow_redirects=False)

    def _sql_count(self, cite_type: str) -> int:
        with closing(db.connect()) as conn:
            return conn.execute(
                """SELECT COUNT(*) FROM citation c
                   JOIN answer a ON a.id = c.answer_id
                   WHERE a.tracker_id = ? AND a.is_valid = 1
                     AND c.cite_type = ?""",
                (TRACKER, cite_type),
            ).fetchone()[0]

    def test_detail_filter_matches_manual_sql(self):
        # 逐个类型:接口的 totals 与手工 SQL 对拍(spec 硬验收)
        for cite_type in ("inline", "reference", "video"):
            response = self.client.get(
                f"/v1/trackers/{TRACKER}/citations?from=2026-11-01&to=2026-11-30"
                f"&citeType={cite_type}&limit=1"
            )
            self.assertEqual(response.status_code, 200, cite_type)
            payload = response.json()
            self.assertEqual(payload["cite_type"], cite_type)
            # seed 的答案都落在 11 月内,SQL 全量 == 接口 occurrences
            self.assertEqual(payload["total_occurrences"], self._sql_count(cite_type), cite_type)

    def test_summary_respects_type_filter(self):
        all_types = self.client.get(
            f"/v1/trackers/{TRACKER}/citations/summary?from=2026-11-01&to=2026-11-30"
        ).json()
        only_video = self.client.get(
            f"/v1/trackers/{TRACKER}/citations/summary?from=2026-11-01&to=2026-11-30&citeType=video"
        ).json()
        # 全量 ≥ 单类型,且视频类型恰好等于 SQL 计数
        self.assertGreaterEqual(all_types["total_citations"], only_video["total_citations"])
        self.assertEqual(only_video["total_citations"], self._sql_count("video"))

    def test_invalid_cite_type_is_400(self):
        response = self.client.get(
            f"/v1/trackers/{TRACKER}/citations?from=2026-11-01&to=2026-11-30&citeType=bogus"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("citeType", response.json()["message"])


if __name__ == "__main__":
    unittest.main()
