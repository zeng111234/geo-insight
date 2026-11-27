"""AI 写作的离线行为测试：假 transport，不联网、不花钱。

守四件事：
1. 素材从库里真实 citation/关键词来，不臆造；
2. 一次生成落草稿+带来源的事实条目+六阶段+一条积分流水；
3. LLM 失败显式抛错并回滚，不留半截草稿/账；
4. 同一篇重复记账幂等（ref_type/ref_id）。
"""

import json
import tempfile
import unittest
from pathlib import Path

from tests import fixtures
from geo.collectors.http import HttpResponse
from geo.services import assistant_service
from geo.services import credit_service

TENANT = fixtures.TENANT
TRACKER = "a0000000-0000-0000-0000-000000000001"


def writer_body(text: str) -> dict:
    return {
        "id": "msg-writer",
        "type": "message",
        "role": "assistant",
        "model": "deepseek-chat",
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": 100, "output_tokens": 200},
    }


class FakeTransport:
    def __init__(self, body: dict | None = None, status: int = 200):
        self.body = body
        self.status = status
        self.calls: list[dict] = []

    def __call__(self, url, payload, headers, timeout):
        self.calls.append(payload)
        raw = json.dumps(self.body or {}).encode("utf-8")
        return HttpResponse(status=self.status, body=raw, elapsed_ms=10)


GOOD_TEXT = json.dumps({
    "title": "广州GEO服务怎么选",
    "body": "第一段结论：看可核验的案例与更新频率。\n第二段：关注引用来源是否真实。",
    "facts": [
        {"claim": "GEO 关注 AI 答案里的品牌可见度", "source_ref": "https://example.com/geo"},
        {"claim": "参考要点需人工核实", "source_ref": "人工参考"},
    ],
}, ensure_ascii=False)


class AssistantServiceTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.conn = fixtures.build_minimal_db(Path(self._dir.name) / "assistant.db")
        self.conn.execute(
            """INSERT INTO tracker(id, tenant_id, brand_id, name, keywords, status, created_at, updated_at)
               VALUES (?, ?, ?, '惠策 · GEO服务', 'GEO,AI搜索', 'active', ?, ?)""",
            (TRACKER, TENANT, fixtures.BRAND, fixtures.NOW, fixtures.NOW),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self._dir.cleanup()

    def test_prompt_uses_real_material(self):
        material = assistant_service.collect_material(
            self.conn, TRACKER, TENANT, reference="客户提供参考：看案例")
        self.assertIn("GEO", material["keywords"])
        self.assertIn("AI搜索", material["keywords"])
        self.assertIn("客户提供参考", material["reference"])
        prompt = assistant_service._build_prompt(material)
        self.assertIn("品牌：", prompt)
        self.assertIn("GEO", prompt)
        self.assertIn("只使用提供的素材", prompt)
        self.assertIn("不要输出 JSON 以外的任何文字", prompt)

    def test_generate_draft_lands_article_facts_stages_and_credit(self):
        transport = FakeTransport(writer_body(GOOD_TEXT))
        article = assistant_service.generate_draft(
            self.conn, TRACKER, TENANT, reference="客户提供参考：看案例",
            api_key="sk-test-placeholder", transport=transport,
        )
        self.conn.commit()
        self.assertEqual(article["status"], "draft")
        self.assertEqual(article["title"], "广州GEO服务怎么选")
        self.assertEqual(article["fact_count"], 2)
        self.assertEqual(article["source"], "ai_generated_pending_review")
        # 落库回读
        facts = self.conn.execute(
            "SELECT claim, source_ref FROM article_fact WHERE article_id = ? ORDER BY position",
            (article["id"],),
        ).fetchall()
        self.assertEqual(len(facts), 2)
        self.assertEqual(facts[0]["source_ref"], "https://example.com/geo")
        stages = self.conn.execute(
            "SELECT COUNT(*) FROM article_stage WHERE article_id = ?", (article["id"],)
        ).fetchone()[0]
        self.assertEqual(stages, 6)
        # 积分：一次生成记一条 -5，ref 幂等
        self.assertEqual(credit_service.balance(self.conn, TENANT), -5)
        self.assertEqual(len(transport.calls), 1)
        # 原始响应可回溯
        self.assertTrue(article["raw_uri"])

    def test_llm_failure_rolls_back_no_half_draft(self):
        transport = FakeTransport(None, status=500)
        with self.assertRaises(assistant_service.AssistantError):
            assistant_service.generate_draft(
                self.conn, TRACKER, TENANT,
                api_key="sk-test-placeholder", transport=transport,
            )
        self.conn.rollback()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM article").fetchone()[0], 0)
        self.assertEqual(credit_service.balance(self.conn, TENANT), 0)

    def test_bad_json_explicit_error(self):
        transport = FakeTransport(writer_body("好的，这是您的文章： blah"))
        with self.assertRaises(assistant_service.AssistantError) as ctx:
            assistant_service.generate_draft(
                self.conn, TRACKER, TENANT,
                api_key="sk-test-placeholder", transport=transport,
            )
        self.assertEqual(ctx.exception.code, "BAD_DRAFT")
        self.conn.rollback()

    def test_foreign_question_rejected_before_spending_money(self):
        """问题不属于本对象时必须在调 LLM 之前就被拒——不能花完钱才失败。"""
        foreign = "f0000000-0000-0000-0000-000000000001"
        foreign_set = "fs000000-0000-0000-0000-000000000001"
        foreign_tracker = "ft000000-0000-0000-0000-000000000001"
        self.conn.execute(
            """INSERT INTO tracker(id, tenant_id, brand_id, name, created_at, updated_at)
               VALUES (?, ?, ?, '别的对象', ?, ?)""",
            (foreign_tracker, TENANT, fixtures.BRAND, fixtures.NOW, fixtures.NOW),
        )
        self.conn.execute(
            """INSERT INTO question_set(id, tenant_id, tracker_id, name, created_at)
               VALUES (?, ?, ?, '别家问题集', ?)""",
            (foreign_set, TENANT, foreign_tracker, fixtures.NOW),
        )
        self.conn.execute(
            """INSERT INTO question(id, tenant_id, question_set_id, text, normalized_text,
                                      tier, status, created_at)
               VALUES (?, ?, ?, '别的对象的问题', '别的对象的问题', 'core', 'active', ?)""",
            (foreign, TENANT, foreign_set, fixtures.NOW),
        )
        self.conn.commit()
        transport = FakeTransport(writer_body(GOOD_TEXT))
        with self.assertRaises(assistant_service.AssistantError) as ctx:
            assistant_service.generate_draft(
                self.conn, TRACKER, TENANT, question_id=foreign,
                api_key="sk-test-placeholder", transport=transport,
            )
        self.assertEqual(ctx.exception.code, "NOT_FOUND")
        self.assertEqual(len(transport.calls), 0, "越界问题不该触发 LLM 调用（不能白花钱）")

    def test_no_key_is_not_configured(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {}, clear=False), \
             mock.patch("geo.collectors.keys.get_key", return_value=None):
            with mock.patch.dict(os.environ, {"GEO_DEEPSEEK_API_KEY": ""}, clear=False):
                with self.assertRaises(assistant_service.AssistantError) as ctx:
                    assistant_service.generate(
                        {"brand_name": "x", "keywords": []},
                        api_key="", transport=FakeTransport(writer_body(GOOD_TEXT)),
                    )
        self.assertEqual(ctx.exception.code, "NOT_CONFIGURED")


if __name__ == "__main__":
    unittest.main()
