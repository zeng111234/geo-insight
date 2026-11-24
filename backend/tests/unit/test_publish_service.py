"""发布通道真实化的核心单元(决策 011)。

为什么不测"能跑"而测这些:发布链路里只有三个纯逻辑点是决定成败的——

- markdown_to_html:全部渠道共用的正文转换,转错=发出去的内容坏;
- _extract_result_json / _map_result:CLI 结果信封 → publication 状态,
  映射错=把失败记成成功(比不发布更糟);
- title_error:派发前的防呆,开浏览器之前就该拒绝。

子进程/浏览器部分不在单测范围(需要真实登录态),由 HTTP 集成测试用
假 runner 覆盖派发状态机。
"""

import unittest

from geo.services import publish_service


class MarkdownToHtmlTest(unittest.TestCase):
    def test_paragraphs_headings_and_ordered_list(self):
        body = "开头一段。\n\n## 三个判断标准\n\n1. 是否专做刑事。\n2. 是否说清思路。\n\n结尾段。"
        html = publish_service.markdown_to_html(body)

        self.assertIn("<p>开头一段。</p>", html)
        self.assertIn("<h2>三个判断标准</h2>", html)
        self.assertIn("<ol><li>是否专做刑事。</li><li>是否说清思路。</li></ol>", html)
        self.assertIn("<p>结尾段。</p>", html)

    def test_html_is_escaped(self):
        html = publish_service.markdown_to_html("说 <b>加粗</b> & 引号「」")
        self.assertIn("&lt;b&gt;加粗&lt;/b&gt;", html)
        self.assertIn("&amp;", html)
        self.assertNotIn("<b>", html)

    def test_soft_line_break_becomes_br(self):
        html = publish_service.markdown_to_html("第一行\n第二行")
        self.assertEqual(html, "<p>第一行<br>第二行</p>")

    def test_empty_body_yields_empty_html(self):
        self.assertEqual(publish_service.markdown_to_html(""), "")
        self.assertEqual(publish_service.markdown_to_html("\n\n"), "")


class TitleErrorTest(unittest.TestCase):
    def test_sohu_title_range_enforced(self):
        self.assertIsNone(publish_service.title_error("sohu", "五个字的标题"))
        self.assertIn("5-72", publish_service.title_error("sohu", "短"))

    def test_unsupported_channel_reports_no_publisher(self):
        # 七个已注册渠道之外(如 B 站)没有发布器,如实拒绝
        err = publish_service.title_error("bilibili", "任意标题")
        self.assertIn("没有发布器", err)

    def test_title_ranges_per_channel(self):
        # 第二批三渠道的字数区间(抖音 1-55 / 头条 5-30 / 小红书 1-20)
        self.assertIn("1-55", publish_service.title_error("douyin", "标" * 60))
        self.assertIn("5-30", publish_service.title_error("toutiao", "短"))
        self.assertIn("1-20", publish_service.title_error("xiaohongshu", "标" * 25))
        self.assertIsNone(publish_service.title_error("xiaohongshu", "二十字以内的标题正好"))


class ExtractResultJsonTest(unittest.TestCase):
    CLI_STDOUT = """请在打开的浏览器窗口完成登录。
已尝试自动登录(表单结果: submitted);弹验证码时请在窗口里人工完成。
{
  "status": "published",
  "final_url": "https://www.zhihu.com/p/123456",
  "note": "unknown 时请人工确认"
}
"""

    def test_extracts_last_envelope_with_status(self):
        result = publish_service._extract_result_json(self.CLI_STDOUT)
        self.assertEqual(result["status"], "published")
        self.assertEqual(result["final_url"], "https://www.zhihu.com/p/123456")

    def test_ignores_json_without_status(self):
        # 百家号的脱敏 payload 预览也是 JSON,但没有 status 键,不能当成结果
        stdout = '{"title": "x", "app_token": "ab...cd"}\n无结果'
        self.assertIsNone(publish_service._extract_result_json(stdout))

    def test_empty_or_garbage_returns_none(self):
        for text in ("", None, "纯文本输出\n没有 JSON"):
            self.assertIsNone(publish_service._extract_result_json(text))


class MapResultTest(unittest.TestCase):
    def test_published_maps_with_url_and_time(self):
        status, extra = publish_service._map_result(
            {"status": "published", "final_url": "http://x"}, ""
        )
        self.assertEqual(status, "published")
        self.assertEqual(extra["published_url"], "http://x")
        self.assertIn("T", extra["published_at"])

    def test_rejected_and_not_logged_in_become_failed(self):
        for cli_status in ("rejected", "not_logged_in"):
            status, extra = publish_service._map_result(
                {"status": cli_status, "reason": "选择器待校准"}, ""
            )
            self.assertEqual(status, "failed")
            self.assertIn("选择器待校准", extra["failure_reason"])

    def test_blocked_and_draft_saved_and_unknown_keep_own_status(self):
        status, _ = publish_service._map_result({"status": "blocked", "reason": "未实名"}, "")
        self.assertEqual(status, "blocked")
        status, extra = publish_service._map_result({"status": "draft_saved", "final_url": "u"}, "")
        self.assertEqual(status, "draft_saved")
        self.assertEqual(extra["published_url"], "u")
        status, extra = publish_service._map_result({"status": "unknown", "note": "人工确认"}, "")
        self.assertEqual(status, "unknown")
        self.assertIn("人工确认", extra["failure_reason"])

    def test_unknown_cli_status_is_failed_not_silently_ok(self):
        status, extra = publish_service._map_result({"status": "weird"}, "fallback note")
        self.assertEqual(status, "failed")
        self.assertIn("fallback", extra["failure_reason"])


if __name__ == "__main__":
    unittest.main()
