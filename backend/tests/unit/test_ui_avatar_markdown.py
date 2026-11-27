"""UI 修复契约：DeepSeek 鲸鱼头像 + AI 助手 markdown 渲染。

用户反馈两个真实问题：① DeepSeek 头像用了字母 D，但它的标志是鲸鱼；
② 助手回复里 #、**、* 等 markdown 标记没渲染，原样显示。
CSS/markdown 不会被 python 单测执行，这里用源码断言守住关键字符串，
防止回退成字母 D 或把 renderMd 换回裸 esc。行为层由 uifixcheck.mjs 真浏览器覆盖。
"""

import unittest

from geo.settings import PROJECT_ROOT

STATIC = PROJECT_ROOT / "frontend" / "static"
APP_JS = STATIC / "app.js"
INDEX = STATIC / "index.html"
ANSWER = STATIC / "answer.html"


class WhaleAvatarContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app_js = APP_JS.read_text(encoding="utf-8")
        cls.index = INDEX.read_text(encoding="utf-8")
        cls.answer = ANSWER.read_text(encoding="utf-8")

    def test_deepseek_meta_has_whale_svg_in_both_files(self):
        """app.js 与 answer.html 两处 MODEL_META 都要给 DeepSeek 配 svg。"""
        self.assertIn("svg: DEEPSEEK_WHALE", self.app_js)
        self.assertIn("svg: DEEPSEEK_WHALE", self.answer)

    def test_whale_svg_is_inline_not_external_image(self):
        """鲸鱼用内联 SVG（离线可用、无版权图片依赖），不是外链图片。"""
        self.assertIn("<svg viewBox=", self.app_js)
        self.assertIn("<svg viewBox=", self.answer)
        self.assertNotIn("deepseek-logo.png", self.app_js)

    def test_both_renderers_use_avatar_html_helper(self):
        """两个头像渲染点都走 modelAvatarHtml（有 svg 用 svg，否则回退字母）。"""
        self.assertIn("modelAvatarHtml(m)", self.app_js)
        self.assertIn("modelAvatarHtml(m)", self.answer)
        self.assertIn("m.svg ? m.svg : esc(m.abbr)", self.app_js)
        self.assertIn("m.svg ? m.svg : esc(m.abbr)", self.answer)

    def test_other_models_keep_letter_fallback(self):
        """其它模型仍用字母，回退逻辑不能被破坏。"""
        self.assertIn('kimi:     { label: "Kimi",      abbr: "K"', self.app_js)


class AssistantMarkdownContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX.read_text(encoding="utf-8")

    def test_render_md_exists_and_escapes_first(self):
        """renderMd 先 esc 转义再替换——XSS 防线的顺序不能反。"""
        self.assertIn("function renderMd(text)", self.html)
        self.assertIn("var out = esc(text);", self.html)

    def test_assistant_reply_uses_render_md(self):
        """助手正文走 renderMd，不能退回裸 esc(m.text)。"""
        self.assertIn('m.role === "assistant" ? renderMd(m.text) : esc(m.text)', self.html)

    def test_user_input_still_plain_esc(self):
        """用户自己的输入仍按纯文本转义，不当 markdown 解释。"""
        self.assertIn(": esc(m.text);", self.html)

    def test_markdown_subset_present(self):
        """标题/列表/粗体/行内码四类替换规则都在。"""
        self.assertIn('#{1,3}', self.html)          # 标题
        self.assertIn('[-*]', self.html)            # 列表
        self.assertIn('\\*\\*(.+?)\\*\\*', self.html)  # 粗体
        self.assertIn("`([^`]+)`", self.html)       # 行内码


if __name__ == "__main__":
    unittest.main()
