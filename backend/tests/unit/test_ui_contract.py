"""界面契约：修复过的响应式缺陷不能再悄悄退回去。

CSS 断点不会被 python 单元测试执行，但「窄屏阶段条退化成 6 行纵列」
这种回退在截图里要等到下一轮体检才发现。这里把已经确定的布局决策
写成源码契约：断点在、网格列数对、纵列规则不在。
"""

import unittest

from geo.settings import PROJECT_ROOT

STUDIO = PROJECT_ROOT / "frontend" / "static" / "studio.html"


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


if __name__ == "__main__":
    unittest.main()
