"""事实人工审核 UI 契约：后端 PATCH 写回接口必须有前端可点的入口。

背景：后端 review_article_fact（PATCH /articles/{id}/facts/{fact_id}）三态完整，
但 studio.html 事实区此前只有只读徽章，全前端没有 PATCH 调用——审核员点不了，
SOP 第二步"人工审核把关"在 UI 层断裂。这里把接线写成源码契约，防止回退成只读。
CSS 断点类测试不会被 python 执行，故用源码断言守住关键字符串。
"""

import unittest

from geo.settings import PROJECT_ROOT

STUDIO = PROJECT_ROOT / "frontend" / "static" / "studio.html"


class FactReviewUiContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = STUDIO.read_text(encoding="utf-8")

    def test_confirm_and_reject_buttons_present(self):
        """确认/驳回两个写操作按钮必须在页面上，不能只有只读徽章。"""
        self.assertIn('data-review="confirmed"', self.html)
        self.assertIn('data-review="rejected"', self.html)

    def test_undo_to_pending_present(self):
        """三态可逆：已确认/驳回后可撤销回待核实，不是只能前进的假按钮。"""
        self.assertIn('data-review="pending"', self.html)
        self.assertIn("撤销回待核实", self.html)

    def test_patch_endpoint_called(self):
        """审核必须走服务端写回，不能在前端改个 class 假装成功。

        用精确的多行锚点守住审核函数体本身：studio.html 别处（saveDraft）也用 PATCH，
        只断言文件里"有 PATCH"会放跑"审核没接线"的回退——变异会证明这条区分。
        """
        self.assertIn(
            '"/facts/" + encodeURIComponent(factId), {\n'
            '      method: "PATCH", body: JSON.stringify(body),',
            self.html,
        )

    def test_review_reads_back_from_server(self):
        """审核后以服务端回读为准重渲染，不靠前端猜新 verdict。"""
        self.assertIn("renderFacts(article)", self.html)
        self.assertIn("const updated = await api", self.html)

    def test_no_source_does_not_fabricate(self):
        """留空依据时不构造假来源：body 只在有值时才带 source_ref。"""
        self.assertIn("if (sourceRef) body.source_ref = sourceRef;", self.html)
        self.assertIn("人工核实", self.html)  # 审核指引里说明留空的诚实标注

    def test_event_delegation_wired(self):
        """事件委托必须绑定，否则 renderFacts 重渲染后按钮点了没反应。"""
        self.assertIn('$("facts").addEventListener("click"', self.html)
        self.assertIn('e.target.closest("[data-review]")', self.html)


if __name__ == "__main__":
    unittest.main()
