"""机会分的验收：排序依据必须能算、能解释、能被反驳。

守三件事：
1. 分数是三项加权的结果，手算能对上（前端展示的公式不能和实现对不上）；
2. 「0 提及 + 竞品在场 + 引用池可发」排最前，已被稳定提及的排后面；
3. 样本不足、没有引用数据的题不许混进前排。
"""

from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from pathlib import Path

try:
    from starlette.testclient import TestClient
except ModuleNotFoundError:
    TestClient = None

from geo import db, seed
from geo.services import opportunity_service as svc
from geo.services.import_service import sha
from geo.settings import settings
from tests import fixtures

START = "2026-11-01"
END = "2026-11-30"
Q1 = "q-opp-0001"
Q2 = "q-opp-0002"
Q3 = "q-opp-0003"
Q4 = "q-opp-0004"
Q5 = "q-opp-0005"


class OpportunityScoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = fixtures.build_minimal_db(Path(self.tmp.name) / "opp.db")
        self.conn.execute(
            """INSERT INTO brand(id, tenant_id, name, kind, created_at)
               VALUES ('b-cp1', ?, '北京盈科（沈阳）律师事务所', 'competitor', ?)""",
            (fixtures.TENANT, fixtures.NOW),
        )
        for question_id, text in (
            (Q1, "沈阳离婚律师收费标准"),
            (Q2, "沈阳刑事辩护律师"),
            (Q3, "沈阳房产纠纷律师"),
            (Q4, "沈阳劳动仲裁律师"),
            (Q5, "沈阳交通事故律师"),
        ):
            fixtures.add_question(self.conn, text, question_id=question_id)

        # Q1：10 条样本、0 提及；竞品在 4 条里出现；引用 10 条中 6 条来自知乎
        self._seed(Q1, samples=10, own=0, competitor=4, domains=["zhihu.com"] * 6 + ["court.gov.cn"] * 4)
        # Q2：10 条样本、8 提及；竞品 1 条；引用全在裁判文书
        self._seed(Q2, samples=10, own=8, competitor=1, domains=["wenshu.court.gov.cn"] * 10)
        # Q3：只有 2 条样本，低于 min_samples
        self._seed(Q3, samples=2, own=0, competitor=1, domains=["zhihu.com"] * 2)
        # Q5：样本够，但一条引用都没有
        self._seed(Q5, samples=5, own=0, competitor=1, domains=[])
        # Q4 一条样本都不造：验证 no_samples 排在最后
        self.conn.commit()
        self.payload = svc.build_opportunities(self.conn, fixtures.TRACKER, START, END)
        self.by_id = {item["question_id"]: item for item in self.payload["items"]}

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _seed(
        self,
        question_id: str,
        *,
        samples: int,
        own: int,
        competitor: int,
        domains: list[str],
    ) -> None:
        answers = [
            fixtures.add_answer(
                self.conn, f"2026-11-02T{8 + index:02d}:00:00+08:00", question_id=question_id
            )
            for index in range(samples)
        ]
        for answer_id in answers[:own]:
            fixtures.add_mention(self.conn, answer_id)
        for answer_id in answers[:competitor]:
            fixtures.add_mention(
                self.conn,
                answer_id,
                subject_type="competitor",
                brand_id="b-cp1",
                alias="北京盈科（沈阳）律师事务所",
            )
        for index, domain in enumerate(domains):
            url = f"https://{domain}/p/{question_id}-{index}"
            self.conn.execute(
                """INSERT INTO citation(id, answer_id, url, url_hash, domain, media_name,
                                        media_type, owner_type, position, cite_type,
                                        published_at, published_at_unknown)
                   VALUES (?, ?, ?, ?, ?, '来源', 'news', 'media', ?, 'reference', NULL, 0)""",
                (f"cit-{question_id}-{index}", answers[0], url, sha(url), domain, index + 1),
            )

    def test_score_matches_hand_calculation(self):
        item = self.by_id[Q1]
        # gap 1.0 x 0.4 + 竞品 0.4 x 0.25 + 可发 0.6 x 0.35 = 0.71 → 71.0
        self.assertEqual(item["score"], 71.0)
        self.assertEqual(item["status"], "ok")
        self.assertAlmostEqual(item["gap"], 1.0, places=4)
        self.assertAlmostEqual(item["competitor_rate"], 0.4, places=4)
        self.assertAlmostEqual(item["publishable_share"], 0.6, places=4)

    def test_zero_mention_with_competitor_and_publishable_ranks_first(self):
        self.assertEqual(self.payload["items"][0]["question_id"], Q1)
        self.assertEqual(
            self.by_id[Q1]["next_action"], "AI 已经在给品牌答案但没提我们——最适合先投一篇"
        )

    def test_dominated_question_ranks_below(self):
        # 已 80% 提及 + 引用池不可发：gap 0.2 x 0.4 + 竞品 0.1 x 0.25 = 0.105
        self.assertEqual(self.by_id[Q2]["score"], 10.5)
        self.assertGreater(self.by_id[Q1]["score"], self.by_id[Q2]["score"])
        self.assertIn("可发布平台", self.by_id[Q2]["next_action"])

    def test_reasons_carry_the_numbers(self):
        reasons = "；".join(self.by_id[Q1]["reasons"])
        self.assertIn("本品牌 0/10 条提及", reasons)
        self.assertIn("竞品出现在 4/10 条答案里", reasons)
        self.assertIn("6/10 条来自可发布平台", reasons)
        self.assertIn("知乎 6", reasons)

    def test_insufficient_and_empty_questions_sort_last(self):
        items = self.payload["items"]
        order = [item["question_id"] for item in items]
        last_ok = max(index for index, item in enumerate(items) if item["status"] == "ok")
        # 样本不足排在有样本之后，无样本排在最后
        self.assertGreater(order.index(Q3), last_ok)
        self.assertGreater(order.index(Q4), order.index(Q3))
        self.assertEqual(self.by_id[Q3]["status"], "insufficient_samples")
        self.assertEqual(self.by_id[Q4]["status"], "no_samples")
        self.assertIsNone(self.by_id[Q4]["score"])
        self.assertIn("先补采样", self.by_id[Q4]["next_action"])

    def test_question_without_citations_is_flagged(self):
        self.assertEqual(self.by_id[Q5]["citations"], 0)
        self.assertTrue(any("没有引用数据" in reason for reason in self.by_id[Q5]["reasons"]))
        self.assertIn("换渠道", self.by_id[Q5]["next_action"])

    def test_platform_mapping(self):
        self.assertEqual(svc.platform_of("zhuanlan.zhihu.com"), "zhihu")
        self.assertEqual(svc.platform_of("mp.weixin.qq.com"), "weixin")
        self.assertEqual(svc.platform_of("www.sohu.com"), "sohu")
        self.assertEqual(svc.platform_of("zhihu.example.com"), "zhihu")
        self.assertIsNone(svc.platform_of("baijiahao.baidu.com"))
        self.assertIsNone(svc.platform_of(""))


@unittest.skipIf(TestClient is None, "starlette TestClient 不可用")
class OpportunityHttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed.seed()
        with closing(db.connect()) as conn:
            from geo.services import auth_service

            auth_service.ensure_admin_password(conn)
        from geo.api.app import app

        cls.client = TestClient(app)
        body = f"email={settings.admin_email}&password={settings.admin_password}"
        cls.client.post(
            "/login",
            content=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
        )
        cls.client.headers.update({"X-CSRF-Token": cls.client.cookies.get("geo_csrf", "")})

    def test_opportunities_route(self):
        response = self.client.get(f"/v1/trackers/{seed.TRACKER}/opportunities?from={START}&to={END}")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["items"])
        self.assertIn("weights", payload)
        self.assertEqual(payload["period"], {"from": START, "to": END})

    def test_limit_is_validated(self):
        bad = self.client.get(f"/v1/trackers/{seed.TRACKER}/opportunities?limit=0")
        self.assertEqual(bad.status_code, 400)
        not_int = self.client.get(f"/v1/trackers/{seed.TRACKER}/opportunities?limit=abc")
        self.assertEqual(not_int.status_code, 400)

    def test_unknown_tracker_returns_404(self):
        response = self.client.get("/v1/trackers/nope/opportunities")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
