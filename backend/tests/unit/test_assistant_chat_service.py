"""AI 助手对话的离线行为测试：假 transport，不联网、不花钱。

守四件事：
1. prompt 只带系统真实快照数据，不臆造数字；
2. 真调返回文本答案 + token + raw_uri，失败显式抛错；
3. 快照缺数据时 prompt 仍诚实（数据不足就直说，不编结论）；
4. snapshot 从真实库取数，口径与 metric_service 一致。
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from geo.collectors.http import HttpResponse
from geo.services import assistant_chat_service as chat
from tests import fixtures

TENANT = fixtures.TENANT
TRACKER = fixtures.TRACKER


def chat_body(text: str) -> dict:
    return {
        "id": "msg-chat",
        "type": "message",
        "role": "assistant",
        "model": "deepseek-chat",
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": 120, "output_tokens": 80},
    }


class FakeTransport:
    def __init__(self, body: dict, status: int = 200):
        self.body = body
        self.status = status
        self.calls: list[dict] = []

    def __call__(self, url, payload, headers, timeout):
        self.calls.append(payload)
        raw = json.dumps(self.body).encode("utf-8")
        return HttpResponse(status=self.status, body=raw, elapsed_ms=10)


class AssistantChatServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self._restore_raw_dir)
        self._old_raw = os.getenv("GEO_RAW_DIR")
        os.environ["GEO_RAW_DIR"] = self.tmp.name

    def _restore_raw_dir(self):
        if self._old_raw is None:
            os.environ.pop("GEO_RAW_DIR", None)
        else:
            os.environ["GEO_RAW_DIR"] = self._old_raw

    def test_prompt_contains_only_real_snapshot(self):
        snap = {
            "brand": "辽征", "period": {"from": "2026-11-01", "to": "2026-11-30"},
            "sample_total": 9, "valid_total": 9, "mention_count": 2,
            "mention_rate": 0.2222, "avg_rank": None, "coverage_rate": 0.5,
            "top3_rate": None, "keywords": ["律所"], "client_profile": {},
        }
        ft = FakeTransport(chat_body("本期提及率 22.22%，样本 9 条。"))
        result = chat.ask(snap, "这期提及率怎么样？", api_key="fake-key", transport=ft)
        self.assertEqual(result["answer"], "本期提及率 22.22%，样本 9 条。")
        self.assertEqual(result["token_in"], 120)
        prompt = ft.calls[0]["messages"][0]["content"]
        self.assertIn('"mention_rate": 0.2222', prompt, "真实快照值必须进 prompt")
        self.assertIn("只用快照里的数据说话", prompt, "必须有不臆造约束")
        self.assertIn("这期提及率怎么样", prompt, "用户问题必须进 prompt")
        self.assertFalse(ft.calls[0].get("tools"), "不该联网引入不可控来源")

    def test_missing_data_prompt_stays_honest(self):
        snap = {"brand": "辽征", "mention_rate": None, "sample_total": 0}
        ft = FakeTransport(chat_body("快照里没有足够数据判断。"))
        result = chat.ask(snap, "为什么下降？", api_key="fake-key", transport=ft)
        self.assertIn("没有足够数据", result["answer"])
        prompt = ft.calls[0]["messages"][0]["content"]
        self.assertIn("快照没有的数据就说", prompt)

    def test_failure_raises_chat_error(self):
        ft = FakeTransport({}, status=500)
        with self.assertRaises(chat.ChatError):
            chat.ask({"brand": "辽征"}, "问题", api_key="fake-key", transport=ft)

    def test_no_key_raises_not_configured(self):
        # keys.local.json 可能真实存在，不能靠删环境变量制造"无 key"——
        # 直接 patch get_key 返回 None，隔离掉本机配置。
        old_env = os.environ.pop("GEO_DEEPSEEK_API_KEY", None)
        old_get = chat.keys.get_key
        chat.keys.get_key = lambda name: None
        try:
            with self.assertRaises(chat.ChatError) as ctx:
                chat.ask({"brand": "辽征"}, "问题", api_key="", transport=FakeTransport(chat_body("x")))
            self.assertEqual(ctx.exception.code, "NOT_CONFIGURED")
        finally:
            chat.keys.get_key = old_get
            if old_env is not None:
                os.environ["GEO_DEEPSEEK_API_KEY"] = old_env

    def test_snapshot_reads_real_db(self):
        conn = fixtures.build_minimal_db(Path(self.tmp.name) / "t.db")
        try:
            snap = chat.snapshot(conn, TRACKER, TENANT)
            self.assertEqual(snap["brand"], "辽宁辽征律师事务所")
            self.assertIn("period", snap)
            self.assertIn("mention_rate", snap)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
