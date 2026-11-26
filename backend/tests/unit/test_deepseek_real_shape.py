"""DeepSeek 解析器对"真实 API 响应形态"的回归测试。

2026-11-26 用真 key 采到第一条真实答案，对照原始响应存档发现一个
**假 fixture 完全没覆盖的形态**：真实响应的正文不是一个 text 块，而是被
拆成十几个连续 text 块，中间夹着 thinking / server_tool_use /
web_fetch_tool_result 等多种块。假 fixture（test_collector_api.py）只有一个
text 块、没有 thinking，所以"多块拼接 + 跳过非文本块"从没被测过。

这里用**脱敏的结构骨架**（URL 换成 example.com、正文换成占位）复刻真实形态：
- thinking 块（带 signature）必须跳过，内容不能混进正文
- 多个 text 块必须全部拼进正文，顺序不能乱
- 多个 text 块里的 citations 必须全部收集
- web_fetch_tool_result 是旁证，不能当答案引用
- usage.server_tool_use.web_search_requests 决定 search_count

不花真钱、不依赖网络：全部走假 transport。字段名和结构照真实响应，
不缩小——字段名写错的话，测试和生产会一起错，这种测试没有价值。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from geo.collectors.base import CollectRequest
from geo.collectors.deepseek import DeepSeekCollector, _parse_message
from geo.collectors.http import HttpResponse


class FakeTransport:
    def __init__(self, body: dict):
        self.body = body

    def __call__(self, url, payload, headers, timeout):
        raw = json.dumps(self.body).encode("utf-8")
        return HttpResponse(status=200, body=raw, elapsed_ms=1234)


def _thinking(text: str) -> dict:
    return {"type": "thinking", "thinking": text, "signature": "sig-placeholder"}


def _text(text: str, citations: list[dict] | None = None) -> dict:
    block = {"type": "text", "text": text}
    if citations is not None:
        block["citations"] = citations
    return block


def _search_result(url: str, site: str, age: str) -> dict:
    return {
        "type": "web_search_result",
        "title": f"title-{site}",
        "url": url,
        "page_age": age,
        "snippet": "snippet",
        "site_name": site,
    }


# 真实响应的脱敏骨架：thinking → 搜索 → 抓取 → 多段 text（带引用）
REAL_SHAPE_BODY = {
    "id": "msg-real-shape",
    "type": "message",
    "role": "assistant",
    "model": "deepseek-chat",
    "content": [
        _thinking("内部思考：用户想知道哪家律所专业——这段话绝不能进正文"),
        {"type": "server_tool_use", "id": "srv1", "name": "deepseek_web_search",
         "input": {"queries": ["律所 排名"]}, "caller": {}, "kind": "search"},
        {"type": "web_search_tool_result", "tool_use_id": "srv1", "kind": "search",
         "content": [
             _search_result("https://law.example.com/a", "法律网", "2026-08-26T16:00:00+00:00"),
             _search_result("https://news.example.com/b", "新闻网", "2026-09-05T16:00:00+00:00"),
         ]},
        _thinking("第二次内部思考：资料够了，开始写——也不能进正文"),
        {"type": "web_fetch_tool_result", "tool_use_id": "fetch1", "kind": "fetch",
         "content": [{"type": "web_fetch_result", "url": "https://law.example.com/a"}]},
        _text("第一段正文。", [{"type": "citation", "url": "https://law.example.com/a",
                              "title": "title-法律网", "cited_text": "..."}]),
        _text("第二段正文。", [{"type": "citation", "url": "https://news.example.com/b",
                              "title": "title-新闻网", "cited_text": "..."}]),
        _text("第三段正文。", []),
        _text("第四段正文，没有 citations 键。"),
    ],
    "stop_reason": "end_turn",
    "usage": {
        "input_tokens": 9552,
        "output_tokens": 1297,
        "server_tool_use": {"web_search_requests": 1, "web_fetch_requests": 1},
    },
}


class DeepSeekRealShapeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = mock.patch.dict(
            "os.environ", {"GEO_RAW_DIR": self._tmp.name}, clear=False
        )
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def test_multiple_text_blocks_are_joined_in_order(self):
        """真实响应正文被拆成多块，必须全部拼进去、顺序不乱。"""
        parsed = _parse_message(REAL_SHAPE_BODY)
        self.assertIn("第一段正文", parsed["text"])
        self.assertIn("第四段正文", parsed["text"])
        self.assertLess(
            parsed["text"].index("第一段正文"),
            parsed["text"].index("第四段正文"),
            "多段正文顺序被颠倒了",
        )

    def test_thinking_never_leaks_into_answer(self):
        """thinking 块是内部推理，混进正文等于把思维链展示给客户。"""
        parsed = _parse_message(REAL_SHAPE_BODY)
        self.assertNotIn("内部思考", parsed["text"])
        self.assertNotIn("第二次内部思考", parsed["text"])
        self.assertNotIn("绝不能进正文", parsed["text"])

    def test_citations_from_every_text_block(self):
        """引用分散在多个 text 块，收集时一个都不能漏。"""
        parsed = _parse_message(REAL_SHAPE_BODY)
        urls = {c["url"] for c in parsed["citations"]}
        self.assertIn("https://law.example.com/a", urls)
        self.assertIn("https://news.example.com/b", urls)
        # 从 web_search 结果块回填 site_name / page_age
        law = next(c for c in parsed["citations"] if c["url"].endswith("/a"))
        self.assertEqual(law["site_name"], "法律网")
        self.assertEqual(law["published_at"], "2026-08-26T16:00:00+00:00")

    def test_web_fetch_is_not_counted_as_answer_citation(self):
        """web_fetch_tool_result 是旁证，不能冒充答案引用。"""
        parsed = _parse_message(REAL_SHAPE_BODY)
        urls = {c["url"] for c in parsed["citations"]}
        self.assertNotIn("https://law.example.com/a?fetched", urls)
        # fetch 块里的 URL 若没出现在 text 引用里，不应单独多出一条
        self.assertEqual(len(parsed["citations"]), 2)

    def test_search_count_from_real_usage_shape(self):
        """真实 usage 是 server_tool_use.web_search_requests，不是顶层字段。"""
        parsed = _parse_message(REAL_SHAPE_BODY)
        self.assertIs(parsed["search_called"], True)
        self.assertEqual(parsed["search_count"], 1)

    def test_full_collect_with_real_shape(self):
        """走完整 collect()（含落盘），确认真实形态端到端能解析。"""
        collector = DeepSeekCollector(
            api_key="sk-test-placeholder", transport=FakeTransport(REAL_SHAPE_BODY)
        )
        result = collector.collect(
            CollectRequest(question_text="沈阳哪一家律所最专业", model_key="deepseek")
        )
        self.assertIn("第一段正文", result.text)
        self.assertNotIn("内部思考", result.text)
        self.assertEqual(len(result.citations), 2)
        self.assertEqual(result.token_in, 9552)
        self.assertEqual(result.token_out, 1297)
        self.assertTrue(result.raw_uri.startswith("collect://raw/"))


if __name__ == "__main__":
    unittest.main()
