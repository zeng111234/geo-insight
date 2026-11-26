"""侧栏菜单 = 产品契约（决策 009 逐字对齐参考系统）。

这条测试存在的理由：菜单是唯一一处「改了没人会发现」的地方。
把 5 个分组 12 个入口写死成期望值，谁动了菜单或改了名，测试立刻红，
而且 diff 里能直接看出少了哪一项——比对着截图比对可靠。

不要为了让它变绿去改 EXPECTED，先确认参考系统里是不是真有这个菜单项。
"""

import unittest

from geo.api import nav


# (分组, [(菜单文案, 路径)])，顺序即渲染顺序
EXPECTED = [
    ("工作台", [("首页", "/"), ("问题监控", "/monitor"), ("AI 舆情", "/insight")]),
    (
        "优化执行",
        [
            ("优化机会", "/opportunities"),
            ("优化任务", "/tasks"),
            ("内容工作室", "/studio"),
            ("任务发布", "/publish"),
        ],
    ),
    ("分析报告", [("报告中心", "/reports")]),
    ("品牌资产", [("品牌资产", "/assets")]),
    ("探索工具", [("行业信源分布", "/sources")]),
]


class NavContractTest(unittest.TestCase):
    def test_groups_and_labels_match_reference(self):
        actual = [
            (group, [(label, href) for _key, label, href, _icon in items])
            for group, items in nav.NAV_GROUPS
        ]
        self.assertEqual(actual, EXPECTED)

    def test_settings_is_not_a_menu_item(self):
        """配置页不在侧栏（参考系统也没有），只能从对象头或左栏「新建」进。"""
        html = nav.render_sidebar("monitor")
        self.assertNotIn('href="/settings"', html)

    def test_settings_page_still_highlights_monitor(self):
        """进 /settings 时侧栏高亮必须落在「问题监控」，否则用户看不出自己在哪。

        路由里 active 传的是 monitor（app.py 的 _page 调用），这里守的是这个约定。
        """
        html = nav.render_sidebar("monitor")
        self.assertIn('aria-current="page"', html)
        # 高亮的那一项必须是 /monitor，不能是别的
        idx = html.index('aria-current="page"')
        self.assertIn('href="/monitor"', html[max(0, idx - 120):idx])

    def test_every_menu_href_is_unique(self):
        hrefs = [href for _g, items in nav.NAV_GROUPS for _k, _l, href, _i in items]
        self.assertEqual(len(hrefs), len(set(hrefs)), f"菜单路径重复: {hrefs}")

    def test_a3_global_bar_injected_once(self):
        """A3 壳：公共顶栏脚本插到 </body> 前，且重复注入不会产生两份。"""
        html = "<html><body><main>x</main></body></html>"
        once = nav.inject_global_bar(html)
        self.assertIn(nav.GLOBAL_BAR_SCRIPT, once)
        self.assertLess(once.index(nav.GLOBAL_BAR_SCRIPT), once.index("</body>"))
        twice = nav.inject_global_bar(once)
        self.assertEqual(twice.count(nav.GLOBAL_BAR_SCRIPT), 1)

    def test_a3_excludes_print_and_detail_pages(self):
        """打印/详情页不注入交互按钮——那些页要的是干净的可打印视图。"""
        self.assertEqual(nav.NO_GLOBAL_BAR, {"report.html", "pilot.html", "answer.html"})
        self.assertNotIn("index.html", nav.NO_GLOBAL_BAR)  # 首页要有徽章
        self.assertNotIn("monitor.html", nav.NO_GLOBAL_BAR)


if __name__ == "__main__":
    unittest.main()
