"""界面契约：修复过的响应式缺陷不能再悄悄退回去。

CSS 断点不会被 python 单元测试执行，但「窄屏阶段条退化成 6 行纵列」
这种回退在截图里要等到下一轮体检才发现。这里把已经确定的布局决策
写成源码契约：断点在、网格列数对、纵列规则不在。
"""

import unittest

from geo.settings import PROJECT_ROOT

STUDIO = PROJECT_ROOT / "frontend" / "static" / "studio.html"
MONITOR = PROJECT_ROOT / "frontend" / "static" / "monitor.html"
INDEX = PROJECT_ROOT / "frontend" / "static" / "index.html"
APP_CSS = PROJECT_ROOT / "frontend" / "static" / "app.css"


class StudioStageBarContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = STUDIO.read_text(encoding="utf-8")

    def test_stage_bar_grid_at_tablet_breakpoint(self):
        """≤1180px 必须是 3 列网格，不能退回纵列。"""
        self.assertIn(
            "@media (max-width: 1180px) {\n"
            "    .stage-bar { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); }",
            self.html,
        )

    def test_stage_bar_grid_at_narrow_breakpoint(self):
        """≤760px 降为 2 列，仍不是纵列。"""
        self.assertIn(
            "@media (max-width: 760px) {\n"
            "    .stage-bar { grid-template-columns: repeat(2, minmax(0, 1fr)); }",
            self.html,
        )

    def test_no_column_fallback(self):
        """旧规则 flex-direction: column 一旦回来就会把正文挤出首屏。"""
        self.assertNotIn(".stage-bar { flex-direction: column; }", self.html)

    def test_operation_bar_spans_grid(self):
        """操作条必须横跨整行，否则窄屏下会被挤到某列里。"""
        self.assertIn(".stage-act { grid-column: 1 / -1; border-left: none; }", self.html)


class MonitorHeadContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = MONITOR.read_text(encoding="utf-8")

    def test_actions_own_row_on_narrow_screen(self):
        """窄屏下按钮组必须独占一行，不能跟标题/状态挤在一起换行。"""
        self.assertIn(
            "    .mon-head .acts { flex-basis: 100%; margin-left: 0; justify-content: flex-end; }",
            self.html,
        )

    def test_danger_button_visual_weight(self):
        """删除是危险操作，不能和刷新同视觉权重（实心底 + 深边框）。"""
        self.assertIn(
            ".mon-head .acts .danger { background: var(--red-tint); border-color: var(--red-ink); color: var(--red-ink); }",
            self.html,
        )

    def test_tab_number_is_visually_separate(self):
        """tab 数字必须有独立浅底和边框，否则「指标看板1062提及」糊成一串。"""
        self.assertIn("gap: 8px;", self.html)
        self.assertIn("border: 1px solid var(--border);", self.html)


class SentimentSplitContractTest(unittest.TestCase):
    """情感三段值：每段自带标签+数字并 nowrap，折行只断在段之间。

    原来的 `正 X% · 中 Y% · 负 Z%` 是纯文本，窄卡片里折行会把圆点甩到行首。
    """

    def test_shared_component_defined(self):
        css = APP_CSS.read_text(encoding="utf-8")
        self.assertIn(".sentiment-split { display: flex; flex-wrap: wrap;", css)
        self.assertIn(".sentiment-split > span { white-space: nowrap;", css)

    def test_three_colored_segments_in_index(self):
        html = INDEX.read_text(encoding="utf-8")
        for cls in ("pos", "neu", "neg"):
            self.assertIn(f'<span class="{cls}">', html)
        self.assertNotIn("`正 ${fmtPct(s.pos)} · 中 ${fmtPct(s.neu)} · 负 ${fmtPct(s.neg)}`", html)

    def test_three_colored_segments_in_monitor(self):
        html = MONITOR.read_text(encoding="utf-8")
        self.assertIn("'<div class=\"sentiment-split\">' +", html)
        self.assertNotIn("' · 中 ' + fmtPct(senti.neu)", html)


class FontScaleContractTest(unittest.TestCase):
    """字号阶梯：theme.css 是唯一来源，页面不许各自写死小字号。

    此前 15 个文件散落 10/10.5/11/11.5/12px，中文在 1440 屏上低于 11px 难读。
    本次把所有 ≤10.5px 提到 caption(11px)，其余数值不变仅 token 化。
    """

    @classmethod
    def setUpClass(cls):
        cls.static_dir = PROJECT_ROOT / "frontend" / "static"
        cls.theme = (cls.static_dir / "theme.css").read_text(encoding="utf-8")

    def test_token_ladder_defined(self):
        for name, value in (
            ("--fs-caption", "11px"),
            ("--fs-label", "11.5px"),
            ("--fs-meta", "12px"),
            ("--fs-body", "13px"),
            ("--fs-title", "15px"),
            ("--fs-h1", "17px"),
        ):
            self.assertIn(f"{name}: {value}", self.theme)

    def test_no_hardcoded_tiny_font(self):
        """任何文件里都不该再有 ≤10.5px 的裸声明。"""
        violations = []
        for path in list(self.static_dir.glob("*.css")) + list(self.static_dir.glob("*.html")):
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for needle in ("font-size: 10.5px", "font-size:10.5px",
                               "font-size: 10px", "font-size:10px"):
                    if needle in line:
                        violations.append(f"{path.name}:{lineno}")
        self.assertEqual(violations, [], f"发现裸小字号：{violations}")

    def test_pages_reference_tokens(self):
        """主要页面至少引用一次字号 token，防止整页退回裸声明。"""
        for name in ("monitor.html", "studio.html", "settings.html", "app.css"):
            text = (self.static_dir / name).read_text(encoding="utf-8")
            self.assertIn("var(--fs-", text, f"{name} 没有引用字号 token")


if __name__ == "__main__":
    unittest.main()
