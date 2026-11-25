"""D4 归因验收：按问题/模型切窗口 + 发布 URL 与 AI 引用的匹配。

这些测试守的是「一篇针对 Q1 的文章，不能拿 Q2 的变化给自己贴金」，
以及「同一条链接的 http/https、www、尾斜杠、utm 变体要能匹配上」。
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
from geo.collectors.citations import domain_of
from geo.services import content_service
from geo.services.import_service import sha
from geo.settings import settings
from tests import fixtures

ARTICLE = "a1000000-0000-0000-0000-000000000001"
CHANNEL_ZHIHU = "ch000000-0000-0000-0000-000000000001"
CHANNEL_WECHAT = "ch000000-0000-0000-0000-000000000002"
PUB_ZHIHU = "p0000000-0000-0000-0000-000000000001"
PUB_WECHAT = "p0000000-0000-0000-0000-000000000002"
Q2 = "q2000000-0000-0000-0000-000000000002"
Q2_TEXT = "沈阳刑事辩护律师哪个好"
Q_NO_ARTICLE = "q3000000-0000-0000-0000-000000000003"

BEFORE_DAYS = ("2026-11-01", "2026-11-02", "2026-11-03")
AFTER_DAYS = ("2026-11-04", "2026-11-05", "2026-11-06")
PUBLISHED_AT = "2026-11-04T10:00:00+08:00"
ZHIHU_URL = "http://www.zhihu.com/p/123/?utm_source=feed"
WECHAT_URL = "https://mp.weixin.qq.com/s/abc?from=timeline"


def _answer(conn, day: str, hour: int, model: str, question: str) -> str:
    return fixtures.add_answer(
        conn, f"{day}T{hour:02d}:00:00+08:00", model_key=model, question_id=question
    )


def _citation(conn, answer_id: str, url: str, position: int = 1) -> None:
    conn.execute(
        """INSERT INTO citation(id, answer_id, url, url_hash, domain, media_name,
                                media_type, owner_type, position, cite_type,
                                published_at, published_at_unknown)
           VALUES (?, ?, ?, ?, ?, ?, 'news', 'media', ?, 'reference', NULL, 0)""",
        (
            f"cit-{answer_id}-{position}",
            answer_id,
            url,
            sha(url),
            domain_of(url),
            f"来源 {position}",
            position,
        ),
    )


class EffectImpactTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = fixtures.build_minimal_db(Path(self.tmp.name) / "effect.db")
        fixtures.add_question(self.conn, Q2_TEXT, question_id=Q2)
        self._seed_article()
        self._seed_answers()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _seed_article(self):
        self.conn.execute(
            """INSERT INTO channel_account(id, tenant_id, platform_key, platform_name,
                                           status, login_mode)
               VALUES (?, ?, 'zhihu', '知乎', 'connected', 'scan_qr')""",
            (CHANNEL_ZHIHU, fixtures.TENANT),
        )
        self.conn.execute(
            """INSERT INTO channel_account(id, tenant_id, platform_key, platform_name,
                                           status, login_mode)
               VALUES (?, ?, 'wechat', '微信公众号', 'connected', 'scan_qr')""",
            (CHANNEL_WECHAT, fixtures.TENANT),
        )
        self.conn.execute(
            """INSERT INTO article(id, tenant_id, tracker_id, question_id, title, body,
                                   status, version, created_at, updated_at)
               VALUES (?, ?, ?, ?, '沈阳律所怎么选', '正文', 'published', 1, ?, ?)""",
            (ARTICLE, fixtures.TENANT, fixtures.TRACKER, fixtures.QUESTION, fixtures.NOW, fixtures.NOW),
        )
        for pub_id, channel, url in (
            (PUB_ZHIHU, CHANNEL_ZHIHU, ZHIHU_URL),
            (PUB_WECHAT, CHANNEL_WECHAT, WECHAT_URL),
        ):
            self.conn.execute(
                """INSERT INTO publication(id, article_id, channel_id, status,
                                           published_url, published_at, created_at)
                   VALUES (?, ?, ?, 'published', ?, ?, ?)""",
                (pub_id, ARTICLE, channel, url, PUBLISHED_AT, fixtures.NOW),
            )
        self.conn.commit()

    def _seed_answers(self):
        # 目标问题 Q1：发布前 6 条（doubao 3 / deepseek 3），1 次提及；
        # 发布后 6 条（doubao 3 / deepseek 3），3 次提及。
        q1_before_doubao = []
        q1_before_deepseek = []
        for day in BEFORE_DAYS:
            q1_before_doubao.append(_answer(self.conn, day, 9, "doubao", fixtures.QUESTION))
            q1_before_deepseek.append(_answer(self.conn, day, 15, "deepseek", fixtures.QUESTION))
        fixtures.add_mention(self.conn, q1_before_doubao[0])

        q1_after_doubao = []
        q1_after_deepseek = []
        for day in AFTER_DAYS:
            q1_after_doubao.append(_answer(self.conn, day, 9, "doubao", fixtures.QUESTION))
            q1_after_deepseek.append(_answer(self.conn, day, 15, "deepseek", fixtures.QUESTION))
        fixtures.add_mention(self.conn, q1_after_doubao[0])
        fixtures.add_mention(self.conn, q1_after_doubao[1])
        fixtures.add_mention(self.conn, q1_after_deepseek[0])

        # 对照问题 Q2：发布前 6 条 3 次提及；发布后 6 条 0 次提及。
        control_before = []
        for day in BEFORE_DAYS:
            for hour in (9, 15):
                control_before.append(_answer(self.conn, day, hour, "doubao", Q2))
        for answer in control_before[:3]:
            fixtures.add_mention(self.conn, answer)
        for day in AFTER_DAYS:
            for hour in (9, 15):
                _answer(self.conn, day, hour, "doubao", Q2)

        # 发布后 Q1 的引用：两条指向知乎（http/www/utm 变体）、一条指向公众号、一条无关。
        _citation(self.conn, q1_after_doubao[0], "https://zhihu.com/p/123", position=1)
        _citation(self.conn, q1_after_doubao[0], "http://www.zhihu.com/p/123/?utm_source=feed", position=2)
        _citation(self.conn, q1_after_doubao[1], "https://zhihu.com/p/999", position=1)
        _citation(self.conn, q1_after_deepseek[0], WECHAT_URL, position=1)
        # 发布前也放一条同样的链接，用来验证 before/after 分开计数。
        _citation(self.conn, q1_before_doubao[0], "https://zhihu.com/p/123", position=1)
        self.conn.commit()

    def test_question_scope_does_not_take_control_question_credit(self):
        payload = content_service.publish_impact(self.conn, ARTICLE, 3, scope="question")

        self.assertEqual(payload["scope"], "question")
        self.assertEqual(payload["question"]["id"], fixtures.QUESTION)
        # Q1 发布前 6 条 1 次提及；发布后 6 条 3 次提及。
        self.assertEqual(payload["before"]["valid_total"], 6)
        self.assertEqual(payload["before"]["mention_total"], 1)
        self.assertEqual(payload["after"]["valid_total"], 6)
        self.assertEqual(payload["after"]["mention_total"], 3)
        self.assertEqual(payload["delta_pp"], 33.3)

        tracker = content_service.publish_impact(self.conn, ARTICLE, 3, scope="tracker")
        # 全对象把 Q2 的 6 条 3 次提及也算进来，数字被稀释，不能用它评价这篇文章。
        self.assertEqual(tracker["before"]["valid_total"], 12)
        self.assertEqual(tracker["before"]["mention_total"], 4)
        self.assertEqual(tracker["after"]["valid_total"], 12)
        self.assertEqual(tracker["after"]["mention_total"], 3)
        self.assertNotEqual(payload["delta_pp"], tracker["delta_pp"])

    def test_citation_match_normalizes_url_variants(self):
        payload = content_service.publish_impact(self.conn, ARTICLE, 3, scope="question")
        match = payload["citation_match"]

        # 发布 URL 是 http+www+尾斜杠+utm；引用是 https 裸域，必须算同一条。
        self.assertEqual(match["before"]["matched_count"], 1)
        self.assertEqual(match["after"]["matched_count"], 3)
        self.assertEqual(match["after"]["matched_answers"], 2)
        self.assertEqual(match["after"]["by_model"], {"doubao": 2, "deepseek": 1})
        self.assertTrue(any("zhihu.com/p/123" in s["url"] for s in match["after"]["samples"]))
        self.assertFalse(any("p/999" in s["url"] for s in match["after"]["samples"]))

    def test_by_model_uses_each_models_own_denominator(self):
        payload = content_service.publish_impact(self.conn, ARTICLE, 3, scope="question")
        by_model = {row["model_key"]: row for row in payload["by_model"]}

        self.assertEqual(set(by_model), {"doubao", "deepseek"})
        self.assertEqual(by_model["doubao"]["before"]["valid_total"], 3)
        self.assertEqual(by_model["doubao"]["before"]["mention_total"], 1)
        self.assertEqual(by_model["doubao"]["after"]["valid_total"], 3)
        self.assertEqual(by_model["doubao"]["after"]["mention_total"], 2)
        self.assertEqual(by_model["deepseek"]["before"]["valid_total"], 3)
        self.assertEqual(by_model["deepseek"]["before"]["mention_total"], 0)
        self.assertEqual(by_model["deepseek"]["after"]["valid_total"], 3)
        self.assertEqual(by_model["deepseek"]["after"]["mention_total"], 1)

    def test_control_and_difference_in_differences(self):
        payload = content_service.publish_impact(
            self.conn, ARTICLE, 3, scope="question", control_question_id=Q2
        )

        self.assertEqual(payload["control"]["question_id"], Q2)
        self.assertEqual(payload["control"]["before"]["mention_total"], 3)
        self.assertEqual(payload["control"]["after"]["mention_total"], 0)
        self.assertEqual(payload["control"]["delta_pp"], -50.0)
        # 目标 +33.3，对照 -50.0 -> 净变化 +83.3pp
        self.assertEqual(payload["did_pp"], 83.3)

    def test_article_without_question_falls_back_to_tracker(self):
        self.conn.execute("UPDATE article SET question_id = NULL WHERE id = ?", (ARTICLE,))
        self.conn.commit()

        payload = content_service.publish_impact(self.conn, ARTICLE, 3, scope="question")

        self.assertEqual(payload["scope"], "tracker")
        self.assertIsNone(payload["question"])
        self.assertTrue(any("未绑定问题" in note for note in payload["notes"]))


@unittest.skipUnless(TestClient is not None and settings.auth_required, "需要 starlette 与开启鉴权")
class EffectImpactHttpTest(unittest.TestCase):
    """路由层只做参数解析，但参数拼错会让页面静默退回全对象口径，值得一条 HTTP 断言。"""

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

    def test_question_scope_over_http(self):
        response = self.client.get(
            f"/v1/articles/{seed.ART_PUBLISHED}/impact?scope=question&windowDays=14"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["scope"], "question")
        self.assertEqual(payload["window_days"], 14)
        self.assertTrue(payload["question"]["text"])
        self.assertTrue(payload["by_model"])
        self.assertIn("citation_match", payload)

    def test_bad_scope_and_too_wide_window_are_rejected(self):
        bad_scope = self.client.get(f"/v1/articles/{seed.ART_PUBLISHED}/impact?scope=bogus")
        self.assertEqual(bad_scope.status_code, 400)

        too_wide = self.client.get(f"/v1/articles/{seed.ART_PUBLISHED}/impact?windowDays=31")
        self.assertEqual(too_wide.status_code, 400)


if __name__ == "__main__":
    unittest.main()
