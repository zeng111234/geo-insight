"""D2 采集 adapter 的测试：全部用假 HTTP，不花真钱、不依赖网络。

覆盖四件必须成立的事：

1. **四类失败能归类**：401 / 429 / 5xx / 超时，加上空回答与非 JSON 响应。
2. **成功调用的字段完整**：正文、引用、token、延迟、搜索次数，一个都不能丢。
3. **「没搜索」与「搜了没引用」能分开**：这是 D1 判定「目标问题至少 2/3 次
   触发搜索」的前提。只有一个布尔值时，两种 0 引用会混成一个数字。
4. **引用真能落库**：answer 的 search_called 与 citation 行数在同一个事务里。

响应样本照官方文档的字段结构手写（DeepSeek Messages 协议 / 豆包 Chat 协议），
不缩小字段名——字段名写错的话，测试和生产会一起错，这种测试没有价值。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from geo.collectors import citations as citation_builder
from geo.collectors import registry
from geo.collectors.base import CollectRequest, CollectorError
from geo.collectors.deepseek import DeepSeekCollector, _parse_message
from geo.collectors.doubao import DoubaoCollector, _parse_chat
from geo.collectors.http import HttpResponse, NetworkError
from geo.services import collect_service
from tests import fixtures

# ---------------------------------------------------------------------------
# 假的 HTTP：可断言"发了什么"，可指定"回什么"
# ---------------------------------------------------------------------------


class FakeTransport:
    def __init__(self, respond):
        self._respond = respond
        self.calls: list[dict] = []

    def __call__(self, url, payload, headers, timeout):
        self.calls.append({"url": url, "payload": payload, "headers": headers, "timeout": timeout})
        result = self._respond(url, payload)
        if isinstance(result, Exception):
            raise result
        return result


def resp(status: int = 200, body: dict | None = None, text: str | None = None, elapsed: int = 1200):
    raw = json.dumps(body).encode("utf-8") if body is not None else (text or "").encode("utf-8")
    return HttpResponse(status=status, body=raw, elapsed_ms=elapsed)


# 照 DeepSeek 官方文档的响应结构：搜索结果在 web_search_tool_result 块，
# 正文引用在 text 块的 citations 里，两个块之间靠 url 关联。
DEEPSEEK_BODY = {
    "id": "18f3b95b",
    "type": "message",
    "role": "assistant",
    "model": "deepseek-flash",
    "content": [
        {
            "type": "server_tool_use",
            "id": "srvtoolu_1",
            "name": "deepseek_web_search",
            "input": {"action": "search", "queries": ["沈阳 律所 排名"]},
            "kind": "search",
        },
        {
            "type": "web_search_tool_result",
            "tool_use_id": "srvtoolu_1",
            "content": [
                {
                    "type": "web_search_result",
                    "title": "沈阳律所排名前十",
                    "url": "https://www.huaon.com/a/1.html",
                    "page_age": "2025-10-15T16:00:00+00:00",
                    "snippet": "华经产业研究院数据...",
                    "site_name": "华经情报网",
                }
            ],
            "kind": "search",
        },
        {
            "type": "text",
            "text": "沈阳较专业的律所包括辽宁辽征律师事务所等。",
            "citations": [
                {
                    "type": "web_search_result_location",
                    "url": "https://www.huaon.com/a/1.html",
                    # 刻意不给 title：验证能从搜索结果块回填
                    "encrypted_index": "",
                    "cited_text": "沈阳较专业的律所包括...",
                }
            ],
        },
        {"type": "text", "text": "以上排名仅供参考。"},
    ],
    "stop_reason": "end_turn",
    "usage": {
        "input_tokens": 1905,
        "output_tokens": 72,
        "server_tool_use": {"web_search_requests": 1, "web_fetch_requests": 0},
    },
}

DOUBAO_BODY = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "model": "doubao-seed-2-1-pro-260915",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "沈阳的律师事务所中，辽宁辽征律所口碑较好。",
                "annotations": [
                    {
                        "type": "url_citation",
                        "title": "沈阳律所怎么选",
                        "url": "https://zhuanlan.zhihu.com/p/123",
                        "site_name": "知乎",
                        "publish_time": "2026-09-04T10:24:00+08:00",
                        "summary": "选律所看三点...",
                    },
                    {
                        "type": "url_citation",
                        "title": "律师执业规范",
                        "url": "https://wenshu.court.gov.cn/x/1",
                        "site_name": "中国裁判文书网",
                        "publish_time": None,
                    },
                ],
            },
            "finish_reason": "stop",
        }
    ],
    "usage": {
        "prompt_tokens": 3666,
        "completion_tokens": 1610,
        "total_tokens": 5276,
        "tool_usage": {"web_search": 1},
    },
}


class RawDirMixin(unittest.TestCase):
    """把所有测试的原始响应急存目录指到临时目录。

    ``save_raw`` 是真的写文件，不隔离的话每跑一次测试就在 backend/data/raw
    里堆一批 JSON，几次之后没人敢删。合同期结束时临时目录自动清掉。
    """

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.raw_dir = Path(self._tmp.name)
        self._env = mock.patch.dict(os.environ, {"GEO_RAW_DIR": str(self.raw_dir)})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()
        super().tearDown()


# ---------------------------------------------------------------------------
# DeepSeek：解析
# ---------------------------------------------------------------------------


class DeepSeekParseTests(RawDirMixin):
    def collect(self, body: dict | None = None, **kwargs):
        transport = FakeTransport(lambda url, payload: resp(body=body or DEEPSEEK_BODY))
        collector = DeepSeekCollector(api_key="sk-test", transport=transport, **kwargs)
        return collector.collect(CollectRequest(question_text="沈阳哪一家律所最专业", model_key="deepseek"))

    def test_fields_complete(self):
        result = self.collect()
        self.assertIn("辽宁辽征律师事务所", result.text)
        self.assertIn("仅供参考", result.text)
        self.assertEqual(result.model_key, "deepseek")
        self.assertEqual(result.model_version, "deepseek-flash")
        self.assertEqual(result.token_in, 1905)
        self.assertEqual(result.token_out, 72)
        self.assertIsNotNone(result.latency_ms)
        self.assertEqual(len(result.citations), 1)
        self.assertIs(result.search_called, True)
        self.assertEqual(result.search_count, 1)

    def test_citation_backfilled_from_search_result(self):
        """citation 本身没有 site_name / 发布时间，必须从结果块按 url 回填。

        不回填的话，信源分布与发布时间完整度两个 tab 会一片空白——
        不是没数据，是丢了。
        """
        result = self.collect()
        cite = result.citations[0]
        self.assertEqual(cite["url"], "https://www.huaon.com/a/1.html")
        self.assertEqual(cite["site_name"], "华经情报网")
        self.assertEqual(cite["published_at"], "2025-10-15T16:00:00+00:00")
        # title 在 citation 里缺省，从结果块补上
        self.assertEqual(cite["title"], "沈阳律所排名前十")

    def test_raw_saved_to_disk(self):
        result = self.collect()
        self.assertTrue(result.raw_uri.startswith("collect://raw/"), result.raw_uri)
        relative = result.raw_uri.removeprefix("collect://raw/")
        saved = self.raw_dir / relative
        self.assertTrue(saved.is_file(), f"原始响应没落盘：{saved}")
        self.assertIn("web_search_tool_result", saved.read_text(encoding="utf-8"))

    def test_searched_but_not_cited(self):
        """搜到了结果、正文一句都没引 —— 这不算失败，是「没被采纳」。

        必须与「没搜索」区分开：前者要改内容/渠道，后者要改问法。
        """
        body = {
            "model": "deepseek-flash",
            "content": [
                {
                    "type": "server_tool_use",
                    "name": "deepseek_web_search",
                    "input": {"action": "search", "queries": ["x"]},
                },
                {
                    "type": "web_search_tool_result",
                    "content": [
                        {"type": "web_search_result", "url": "https://a.com/1", "site_name": "A 网"}
                    ],
                },
                {"type": "text", "text": "这个问题我无法确定。"},
            ],
            "usage": {"input_tokens": 100, "output_tokens": 10, "server_tool_use": {"web_search_requests": 1}},
        }
        result = self.collect(body=body)
        self.assertIs(result.search_called, True)
        self.assertEqual(result.citations, [])
        # 搜到的结果不能冒充"被引用"
        self.assertEqual(result.search_count, 1)

    def test_no_search(self):
        body = {
            "model": "deepseek-flash",
            "content": [{"type": "text", "text": "沈阳有几家律所。"}],
            "usage": {"input_tokens": 50, "output_tokens": 8},
        }
        result = self.collect(body=body)
        self.assertIs(result.search_called, False)
        self.assertEqual(result.citations, [])

    def test_empty_text_is_error(self):
        body = {"model": "deepseek-flash", "content": [{"type": "text", "text": ""}], "usage": {}}
        with self.assertRaises(CollectorError) as caught:
            self.collect(body=body)
        self.assertEqual(caught.exception.code, "EMPTY_TEXT")

    def test_missing_key_says_where_to_put_it(self):
        collector = DeepSeekCollector(api_key=None, transport=FakeTransport(lambda *a: resp(body={})))
        with mock.patch.dict(os.environ, {"GEO_KEYS_FILE": str(self.raw_dir / "none.json")}, clear=False):
            for name in ("GEO_DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY"):
                os.environ.pop(name, None)
            self.assertFalse(collector.available())
            with self.assertRaises(CollectorError) as caught:
                collector.collect(CollectRequest(question_text="q", model_key="deepseek"))
        self.assertEqual(caught.exception.code, "NOT_CONFIGURED")
        self.assertIn("keys.local.json", str(caught.exception))


# ---------------------------------------------------------------------------
# 失败归类：两个平台共用同一套分类，所以两边都跑一遍
# ---------------------------------------------------------------------------


class FailureClassificationTests(RawDirMixin):
    CASES = [
        (401, "AUTH_FAILED"),
        (403, "AUTH_FAILED"),
        (429, "RATE_LIMITED"),
        (400, "BAD_REQUEST"),
        (422, "BAD_REQUEST"),
        (500, "UPSTREAM_ERROR"),
        (502, "UPSTREAM_ERROR"),
        (503, "UPSTREAM_ERROR"),
    ]

    def _collect_with_status(self, collector_cls, status, model_key):
        transport = FakeTransport(
            lambda url, payload: resp(status=status, body={"error": {"code": "x", "message": "详情"}})
        )
        collector = collector_cls(api_key="k", transport=transport)
        return collector.collect(CollectRequest(question_text="问题", model_key=model_key))

    def test_status_codes(self):
        for status, expected in self.CASES:
            with self.subTest(status=status):
                with self.assertRaises(CollectorError) as caught:
                    self._collect_with_status(DeepSeekCollector, status, "deepseek")
                self.assertEqual(caught.exception.code, expected)
                # 错误正文要带出来，否则排查时还得手敲一次请求
                self.assertIn("详情", str(caught.exception))

    def test_doubao_same_classification(self):
        """同一套分类要覆盖豆包 —— 复制两份 if/elif 就是漏分支的开始。"""
        for status, expected in self.CASES:
            with self.subTest(status=status):
                with self.assertRaises(CollectorError) as caught:
                    self._collect_with_status(DoubaoCollector, status, "doubao")
                self.assertEqual(caught.exception.code, expected)

    def test_timeout(self):
        transport = FakeTransport(lambda url, payload: NetworkError("TIMEOUT", "60 秒内没有响应"))
        collector = DeepSeekCollector(api_key="k", transport=transport, timeout=60)
        with self.assertRaises(CollectorError) as caught:
            collector.collect(CollectRequest(question_text="q", model_key="deepseek"))
        self.assertEqual(caught.exception.code, "TIMEOUT")

    def test_connection(self):
        transport = FakeTransport(lambda url, payload: NetworkError("CONNECTION", "DNS 失败"))
        collector = DoubaoCollector(api_key="k", transport=transport)
        with self.assertRaises(CollectorError) as caught:
            collector.collect(CollectRequest(question_text="q", model_key="doubao"))
        self.assertEqual(caught.exception.code, "CONNECTION")

    def test_not_json(self):
        transport = FakeTransport(lambda url, payload: resp(status=200, text="<html>网关错误</html>"))
        collector = DeepSeekCollector(api_key="k", transport=transport)
        with self.assertRaises(CollectorError) as caught:
            collector.collect(CollectRequest(question_text="q", model_key="deepseek"))
        self.assertEqual(caught.exception.code, "BAD_RESPONSE")

    def test_missing_content_array(self):
        transport = FakeTransport(lambda url, payload: resp(body={"id": "x"}))
        collector = DeepSeekCollector(api_key="k", transport=transport)
        with self.assertRaises(CollectorError) as caught:
            collector.collect(CollectRequest(question_text="q", model_key="deepseek"))
        self.assertEqual(caught.exception.code, "BAD_RESPONSE")


# ---------------------------------------------------------------------------
# 请求体：发出去的东西要能被真实平台接受
# ---------------------------------------------------------------------------


class RequestShapeTests(RawDirMixin):
    def test_deepseek_request(self):
        transport = FakeTransport(lambda url, payload: resp(body=DEEPSEEK_BODY))
        collector = DeepSeekCollector(api_key="sk-test", transport=transport)
        collector.collect(CollectRequest(question_text="沈阳哪一家律所最专业", model_key="deepseek"))

        call = transport.calls[0]
        self.assertEqual(call["url"], "https://api.deepseek.com/anthropic/v1/messages")
        self.assertEqual(call["headers"]["x-api-key"], "sk-test")
        self.assertIn("anthropic-version", call["headers"])
        self.assertEqual(call["payload"]["model"], "deepseek-flash")
        # 联网搜索靠这个工具声明打开；名字写错就是静默不搜
        self.assertEqual(call["payload"]["tools"], [{"type": "deepseek_web_search", "name": "deepseek_web_search"}])
        self.assertFalse(call["payload"]["stream"])

    def test_doubao_request_has_no_tools(self):
        """豆包：tools 与 web_search_options 同时传时联网不生效（官方文档明确）。

        这条断言守的是"别顺手把 tools 加回来"——加了以后采集照常成功，
        只是永远不会搜索，从结果上完全看不出来。
        """
        transport = FakeTransport(lambda url, payload: resp(body=DOUBAO_BODY))
        collector = DoubaoCollector(api_key="ark-test", transport=transport)
        collector.collect(CollectRequest(question_text="沈阳哪一家律所最专业", model_key="doubao"))

        call = transport.calls[0]
        self.assertEqual(call["url"], "https://ark.cn-beijing.volces.com/api/v3/chat/completions")
        self.assertEqual(call["headers"]["Authorization"], "Bearer ark-test")
        self.assertEqual(call["payload"]["web_search_options"], {})
        self.assertNotIn("tools", call["payload"])
        self.assertFalse(call["payload"]["stream"])


# ---------------------------------------------------------------------------
# 豆包：解析
# ---------------------------------------------------------------------------


class DoubaoParseTests(RawDirMixin):
    def collect(self, body: dict | None = None):
        transport = FakeTransport(lambda url, payload: resp(body=body or DOUBAO_BODY))
        collector = DoubaoCollector(api_key="ark-test", transport=transport)
        return collector.collect(CollectRequest(question_text="沈阳哪一家律所最专业", model_key="doubao"))

    def test_fields_complete(self):
        result = self.collect()
        self.assertIn("辽宁辽征律所", result.text)
        self.assertEqual(result.model_version, "doubao-seed-2-1-pro-260915")
        self.assertEqual(result.token_in, 3666)
        self.assertEqual(result.token_out, 1610)
        self.assertEqual(len(result.citations), 2)
        self.assertIs(result.search_called, True)
        self.assertEqual(result.search_count, 1)

    def test_citation_fields(self):
        result = self.collect()
        first = result.citations[0]
        self.assertEqual(first["url"], "https://zhuanlan.zhihu.com/p/123")
        self.assertEqual(first["site_name"], "知乎")
        self.assertEqual(first["published_at"], "2026-09-04T10:24:00+08:00")
        # 发布时间缺失的引用要能保留，标成 None 由落库时记 published_at_unknown
        self.assertIsNone(result.citations[1]["published_at"])

    def test_search_unknown_when_usage_missing(self):
        """平台没报搜索次数时只能说「不知道」，不能当「没搜」。"""
        body = {
            "model": "m",
            "choices": [{"message": {"role": "assistant", "content": "答案"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
        result = self.collect(body=body)
        self.assertIsNone(result.search_called)
        self.assertIsNone(result.search_count)

    def test_cited_without_usage_counts_as_searched(self):
        body = {
            "model": "m",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "答案",
                        "annotations": [{"type": "url_citation", "url": "https://a.com/1"}],
                    }
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
        result = self.collect(body=body)
        self.assertIs(result.search_called, True)
        self.assertIsNone(result.search_count)

    def test_zero_search_is_explicit_false(self):
        body = {
            "model": "m",
            "choices": [{"message": {"role": "assistant", "content": "答案"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "tool_usage": {"web_search": 0}},
        }
        result = self.collect(body=body)
        self.assertIs(result.search_called, False)


# ---------------------------------------------------------------------------
# 引用归一化与分类
# ---------------------------------------------------------------------------


class CitationHelperTests(unittest.TestCase):
    def test_normalize_url(self):
        cases = [
            ("https://www.Example.com/a/?utm_source=x&spm=abc&id=1", "https://example.com/a?id=1"),
            ("http://example.com/a/", "https://example.com/a"),
            ("https://example.com/", "https://example.com/"),
            ("https://example.com/a#section", "https://example.com/a"),
            ("example.com/a", "https://example.com/a"),
            ("", ""),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(citation_builder.normalize_url(raw), expected)

    def test_classify_media(self):
        cases = [
            ("https://zhuanlan.zhihu.com/p/1", "forum"),
            ("https://mp.weixin.qq.com/s/abc", "wechat"),
            ("https://www.bilibili.com/video/BV1", "video"),
            ("https://baike.baidu.com/item/x", "wiki"),
            ("https://wenshu.court.gov.cn/website/x", "database"),
            ("https://blog.csdn.net/a/article", "blog"),
            ("https://news.sina.com.cn/a", "news"),
            ("", "other"),
        ]
        for url, expected in cases:
            with self.subTest(url=url):
                self.assertEqual(citation_builder.classify_media(url), expected)

    def test_infer_cite_type(self):
        self.assertEqual(citation_builder.infer_cite_type("video", "inline"), "video")
        self.assertEqual(citation_builder.infer_cite_type("wiki", "inline"), "reference")
        self.assertEqual(citation_builder.infer_cite_type("database", "inline"), "reference")
        self.assertEqual(citation_builder.infer_cite_type("news", "inline"), "inline")
        self.assertEqual(citation_builder.infer_cite_type("news", "reference"), "reference")
        # 平台给了没见过的值时不能把它原样写进库
        self.assertEqual(citation_builder.infer_cite_type("news", "weird"), "reference")

    def test_build_drafts_dedupes_and_numbers(self):
        drafts = citation_builder.build_drafts(
            [
                {"url": "https://a.com/1", "site_name": "A"},
                # 同一个页面带追踪参数：归一化后是同一条，不能算两条
                {"url": "https://a.com/1?utm_source=x"},
                {"url": ""},
                {"url": "https://b.com/2", "published_at": "2026-09-04T10:24:00+08:00"},
                {"url": "https://c.com/3", "published_at": "不是时间"},
            ],
            platform_default="inline",
        )
        self.assertEqual([d.url for d in drafts], ["https://a.com/1", "https://b.com/2", "https://c.com/3"])
        self.assertEqual([d.position for d in drafts], [1, 2, 3])
        self.assertIsNone(drafts[2].published_at, "无法解析的时间要当未知，不能猜")

    def test_extra_fields_preferred_from_side_map(self):
        drafts = citation_builder.build_drafts(
            [{"url": "https://a.com/1"}],
            platform_default="inline",
            extra_by_url={"https://a.com/1": {"site_name": "A 网", "page_age": "2025-10-15"}},
        )
        self.assertEqual(drafts[0].site_name, "A 网")
        self.assertEqual(drafts[0].published_at, "2025-10-15")


# ---------------------------------------------------------------------------
# 注册表：挑错人是一个静默故障
# ---------------------------------------------------------------------------


class RegistryTests(unittest.TestCase):
    def test_api_preferred_over_offline(self):
        with mock.patch.dict(os.environ, {"GEO_DEEPSEEK_API_KEY": "k"}):
            found = registry.for_model("deepseek")
        self.assertIsNotNone(found)
        self.assertEqual(found.kind, "api")
        self.assertEqual(found.key, "deepseek")

    def test_returns_none_for_unsupported_model(self):
        with mock.patch.dict(os.environ, {"GEO_DEEPSEEK_API_KEY": "k"}):
            self.assertIsNone(registry.for_model("kimi"))

    def test_supported_models_reflects_keys(self):
        with mock.patch.dict(os.environ, {"GEO_DEEPSEEK_API_KEY": "k", "GEO_ARK_API_KEY": "a"}):
            models = registry.supported_models()
        self.assertIn("deepseek", models)
        self.assertIn("doubao", models)


# ---------------------------------------------------------------------------
# 端到端：采集 → 落库（行为层，最关键的一组）
# ---------------------------------------------------------------------------


class CollectToDatabaseTests(RawDirMixin):
    def setUp(self):
        super().setUp()
        self._dbdir = tempfile.TemporaryDirectory()
        self.conn = fixtures.build_minimal_db(Path(self._dbdir.name) / "t.db")
        # 端到端测的是 collect_service，它从**注册表单例**取采集器，
        # 而不是我们手头这个实例。所以要同时做两件事：
        # ① 让 available() 为真（给注册表实例读得到 key 的环境变量）；
        # ② 把注册表实例的 transport 换成假的，绝不能真发网络请求。
        self._env_patch = mock.patch.dict(os.environ, {"GEO_DEEPSEEK_API_KEY": "sk-test"})
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def use_fake(self, respond, platform: str = "deepseek"):
        collector = registry.get(platform)
        patcher = mock.patch.object(collector, "_transport", FakeTransport(respond))
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.conn.close()
        self._dbdir.cleanup()
        super().tearDown()

    def test_answer_and_citations_written_in_one_transaction(self):
        self.use_fake(lambda url, payload: resp(body=DEEPSEEK_BODY))

        outcome = collect_service.run_once(
            self.conn,
            tracker_id=fixtures.TRACKER,
            question_text=fixtures.QUESTION_TEXT,
            model_key="deepseek",
            tenant_id=fixtures.TENANT,
        )
        self.assertTrue(outcome["inserted"])
        self.assertEqual(outcome["citations_written"], 1)
        self.assertIs(outcome["search_called"], True)

        row = self.conn.execute(
            "SELECT search_called, search_count FROM answer WHERE id = ?", (outcome["answer_id"],)
        ).fetchone()
        self.assertEqual(row["search_called"], 1)
        self.assertEqual(row["search_count"], 1)

        cites = self.conn.execute(
            "SELECT url, domain, media_type, cite_type, published_at, url_hash FROM citation WHERE answer_id = ?",
            (outcome["answer_id"],),
        ).fetchall()
        self.assertEqual(len(cites), 1)
        self.assertEqual(cites[0]["domain"], "huaon.com")
        self.assertEqual(cites[0]["media_type"], "news")
        self.assertEqual(cites[0]["cite_type"], "inline")
        self.assertEqual(cites[0]["published_at"], "2025-10-15T16:00:00+00:00")
        # url_hash 必须与导入通道同算法（原始 URL 的 sha256）
        self.assertEqual(cites[0]["url_hash"], fixtures.sha("https://www.huaon.com/a/1.html"))

    def test_searched_but_not_cited_writes_zero_citations(self):
        """搜了没引用：answer 落库、search_called=1、citation 0 条。

        这三者同时成立才算「搜了没被引用」。少任何一条，
        统计时就会和「没搜索」混在一起。
        """
        body = {
            "model": "deepseek-flash",
            "content": [
                {"type": "server_tool_use", "name": "deepseek_web_search", "input": {"action": "search"}},
                {"type": "web_search_tool_result", "content": [{"url": "https://a.com/1"}]},
                {"type": "text", "text": "没有找到可靠信息。"},
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5, "server_tool_use": {"web_search_requests": 1}},
        }
        self.use_fake(lambda url, payload: resp(body=body))
        outcome = collect_service.run_once(
            self.conn,
            tracker_id=fixtures.TRACKER,
            question_text=fixtures.QUESTION_TEXT,
            model_key="deepseek",
            tenant_id=fixtures.TENANT,
        )
        self.assertEqual(outcome["citations_written"], 0)
        self.assertIs(outcome["search_called"], True)
        self.assertIn("搜到了但没被引用", outcome["message"])

        row = self.conn.execute(
            "SELECT search_called FROM answer WHERE id = ?", (outcome["answer_id"],)
        ).fetchone()
        self.assertEqual(row["search_called"], 1)

    def test_same_day_second_collect_is_a_new_sample(self):
        """同一天再采一次 = 第 2 条样本，不是重复。

        平台对同一个问题常常逐字返回相同的答案。早期用 row_hash（含到达秒）
        判重，同一秒内采两次会被吞掉一条——但那是两次独立观测，
        吞掉之后样本数永远够不到计划量，提及率的分母就是错的。
        幂等改由槽位保证（见 tests/integration/test_collect_slot.py）。
        """
        self.use_fake(lambda url, payload: resp(body=DEEPSEEK_BODY))
        first = collect_service.run_once(
            self.conn,
            tracker_id=fixtures.TRACKER,
            question_text=fixtures.QUESTION_TEXT,
            model_key="deepseek",
            tenant_id=fixtures.TENANT,
        )
        second = collect_service.run_once(
            self.conn,
            tracker_id=fixtures.TRACKER,
            question_text=fixtures.QUESTION_TEXT,
            model_key="deepseek",
            tenant_id=fixtures.TENANT,
        )

        self.assertTrue(first["inserted"])
        self.assertTrue(second["inserted"])
        self.assertEqual(first["sample_idx"], 1)
        self.assertEqual(second["sample_idx"], 2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM answer").fetchone()[0], 2)
        # 每条答案各自带一份引用，不能串到一条上
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM citation").fetchone()[0], 2)

    def test_imported_answer_keeps_search_unknown(self):
        """导入的答案没有搜索信息 → 两列必须是 NULL。

        写成 0 会把「不知道」混进「没搜索」，搜索触发率被算低。
        """
        answer_id = fixtures.add_answer(self.conn, fixtures.NOW, model_key="doubao")
        row = self.conn.execute(
            "SELECT search_called, search_count FROM answer WHERE id = ?", (answer_id,)
        ).fetchone()
        self.assertIsNone(row["search_called"])
        self.assertIsNone(row["search_count"])

    def test_wrong_collector_reports_what_is_available(self):
        """自动挑采集器失败时，报错要说清「有哪些、支持什么」。"""
        with mock.patch.dict(os.environ, {}, clear=False):
            for name in ("GEO_DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY", "GEO_ARK_API_KEY", "GEO_DOUBAO_API_KEY", "ARK_API_KEY"):
                os.environ.pop(name, None)
            os.environ["GEO_KEYS_FILE"] = str(self.raw_dir / "none.json")
            with self.assertRaises(CollectorError) as caught:
                collect_service.run_once(
                    self.conn,
                    tracker_id=fixtures.TRACKER,
                    question_text=fixtures.QUESTION_TEXT,
                    model_key="kimi",
                    tenant_id=fixtures.TENANT,
                )
        self.assertIn(caught.exception.code, {"NO_COLLECTOR", "UNSUPPORTED_MODEL"})
        self.assertIn("kimi", str(caught.exception))


# ---------------------------------------------------------------------------
# 解析函数单独测：异常路径不经过 collect 也得能覆盖
# ---------------------------------------------------------------------------


class ParseGuardTests(unittest.TestCase):
    def test_deepseek_missing_content(self):
        with self.assertRaises(CollectorError) as caught:
            _parse_message({"id": "x"})
        self.assertEqual(caught.exception.code, "BAD_RESPONSE")

    def test_doubao_empty_choices(self):
        with self.assertRaises(CollectorError) as caught:
            _parse_chat({"choices": []})
        self.assertEqual(caught.exception.code, "BAD_RESPONSE")

    def test_doubao_content_as_blocks(self):
        """content 是块数组时也要能取到正文（少数 OpenAI 兼容实现这么返回）。"""
        parsed = _parse_chat(
            {
                "choices": [
                    {
                        "message": {
                            "content": [
                                {"type": "text", "text": "第一段", "annotations": [{"type": "url_citation", "url": "https://a.com/1"}]}
                            ]
                        }
                    }
                ],
                "usage": {"tool_usage": {"web_search": 1}},
            }
        )
        self.assertEqual(parsed["text"], "第一段")
        self.assertEqual(len(parsed["citations"]), 1)


if __name__ == "__main__":
    unittest.main()
