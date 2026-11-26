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


class PublishTableContractTest(unittest.TestCase):
    """发布页渠道表：空值不给破折号，失败原因不挤在行内。

    此前 7 个渠道 × 5 列里有约 21 个「—」——真实数据就是没连过，
    但一屏破折号既没信息又难扫；失败原因塞在行尾胶囊里读不下去。
    """

    @classmethod
    def setUpClass(cls):
        cls.html = (PROJECT_ROOT / "frontend" / "static" / "publish.html").read_text(encoding="utf-8")

    def test_empty_values_have_status_hints(self):
        self.assertIn("var CH_HINT = {", self.html)
        for state in ("disconnected", "needs_config", "connected", "expired"):
            self.assertIn(f"{state}:", self.html)

    def test_no_dash_placeholder_in_channel_cells(self):
        # 渲染 channels 的三列不许再输出裸「—」；login 兜底保留（无 support 时确实未知）
        self.assertNotIn('c.last_check_note || "—"', self.html)
        self.assertNotIn('|| "—") + "</td></tr>"', self.html)

    def test_login_mode_derived_from_support(self):
        self.assertIn('c.support === "browser" ? "扫码登录" : c.support === "api" ? "API 密钥"', self.html)

    def test_failure_reason_own_row(self):
        self.assertIn('class="pub-fail"', self.html)
        self.assertNotIn("'<span class=\"tag bad\">' + esc(p.failure_reason)", self.html)

    def test_channel_table_scrolls_instead_of_crushing_cells(self):
        """窄屏下 5 列挤在半屏面板会把「未连接」拆成两行。

        给表格最小宽度触发 .table-wrap 的横向滚动，状态标签本身 nowrap。
        """
        self.assertIn("#channels table { min-width: 560px; }", self.html)
        self.assertIn("#channels td .tag { white-space: nowrap; }", self.html)


class CollectionRunsContractTest(unittest.TestCase):
    """采集任务列表：真 run 记录，无记录说人话，不编进度。"""

    @classmethod
    def setUpClass(cls):
        cls.html = MONITOR.read_text(encoding="utf-8")

    def test_runs_endpoint_is_fetched(self):
        self.assertIn('"/trackers/" + state.trackerId + "/runs?" + datesF()', self.html)

    def test_empty_state_says_no_runs(self):
        self.assertIn("本周期尚无采集任务", self.html)
        self.assertIn("采集任务列表暂时读取失败", self.html)

    def test_progress_from_real_counts(self):
        """条目进度必须用 succeeded/failed/total，不能用 run.stats 的空对象。"""
        self.assertIn('r.succeeded + r.failed) + "/" + r.total', self.html)


class BrandFilterContractTest(unittest.TestCase):
    """品牌筛选是真实维度：下拉由 tracker 列表生成，选中即切对象。"""

    @classmethod
    def setUpClass(cls):
        cls.html = MONITOR.read_text(encoding="utf-8")

    def test_brand_options_are_dynamic(self):
        """守渲染语句本身：下拉内容必须由 tracker 列表赋值，不能只查声明。"""
        self.assertNotIn('<option value="own">本品牌</option>', self.html)
        self.assertIn("bsel.innerHTML = (state.trackers || []).map(function (t)", self.html)
        self.assertIn("bsel.value = state.trackerId;", self.html)

    def test_brand_switch_reuses_single_path(self):
        """品牌下拉与左栏列表必须共用 switchTracker，避免两份逻辑漂移。"""
        self.assertIn("function switchTracker(id)", self.html)
        self.assertIn('$("brandFilter").addEventListener("change", function () {\n    switchTracker(this.value);', self.html)
        self.assertIn("if (el) switchTracker(el.dataset.tracker);", self.html)

    def test_table_browse_button_wired(self):
        self.assertIn('id="tableBrowseBtn"', self.html)
        self.assertIn('$("tableBrowseBtn").addEventListener("click"', self.html)
        self.assertIn('state.answerView = "table";', self.html)


class HomeKpiGridContractTest(unittest.TestCase):
    """首页 KPI 卡：8 张要在宽屏排 4+4，情感卡不再独占一行。"""

    @classmethod
    def setUpClass(cls):
        cls.css = APP_CSS.read_text(encoding="utf-8")

    def test_cards_use_wider_min_column(self):
        """260px 阈值让 1440 下 4 列、900 下 2 列；退回 148px 会变 7+1 断行。"""
        self.assertIn(".cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));",
                      self.css)
        self.assertNotIn("minmax(148px, 1fr)", self.css)

    def test_sentiment_no_narrow_hack(self):
        """卡片变宽后不能再把三段强行收紧成竖排兜底。"""
        self.assertNotIn(".card .sentiment-split { gap: 0; line-height: 1.35; }", self.css)


class TrendLineContractTest(unittest.TestCase):
    """折线：x 轴按真实日期、缺口用虚线，不能再退回点序号+整段消失。"""

    @classmethod
    def setUpClass(cls):
        cls.html = MONITOR.read_text(encoding="utf-8")

    def test_x_axis_uses_real_dates(self):
        self.assertIn("function dayNum(s)", self.html)
        self.assertIn("(dayNum(p.date) - t0) / span", self.html)
        self.assertNotIn("pad + i * step", self.html)

    def test_gap_uses_dashed_segment(self):
        self.assertIn('stroke-dasharray="4 4"', self.html)
        self.assertIn("gapDays > 1.5", self.html)

    def test_legend_matches_behavior(self):
        self.assertIn("虚线＝无样本区间", self.html)


class ArticleBarContractTest(unittest.TestCase):
    """文章栏：几百篇时搜索+分页，当前文章钉住，不能退回全量平铺。"""

    @classmethod
    def setUpClass(cls):
        cls.html = STUDIO.read_text(encoding="utf-8")

    def test_search_box_present(self):
        self.assertIn('id="articleSearch"', self.html)
        self.assertIn("state.articleQuery", self.html)

    def test_pagination_bounded(self):
        self.assertIn("ARTICLE_PAGE_SIZE = 12", self.html)
        self.assertIn("Math.ceil(matched.length / ARTICLE_PAGE_SIZE)", self.html)
        self.assertIn('id="articleNext"', self.html)

    def test_render_uses_paged_slice(self):
        """真正守行为：渲染的必须是分页切片，不能退回全量 matched。"""
        self.assertIn(
            "const slice = matched.slice((state.articlePage - 1) * ARTICLE_PAGE_SIZE, state.articlePage * ARTICLE_PAGE_SIZE);",
            self.html,
        )
        self.assertIn("slice.map((a) => articleChip(a, false))", self.html)

    def test_current_article_pinned(self):
        self.assertIn("pinned", self.html)
        self.assertIn("chip-current", self.html)

    def test_no_full_flatten(self):
        # 旧实现把全部文章 map 成 chip，没有分页；确认那段已被替换
        self.assertNotIn("const chips = state.articles.map((a)", self.html)


class CsrfInjectionContractTest(unittest.TestCase):
    """不加载 app.js 的页面若发写请求，必须自己注入 CSRF 头。

    真 bug：settings.html / reports.html 自写 api() 只发 Content-Type，
    鉴权开启时所有写操作 403。HTTP 层测试（TestClient 手动带头）全绿，
    只有真浏览器点一遍才发现。这里把"哪些页面必须自带 CSRF 注入"写成静态契约，
    以后新增页面漏了能在单测阶段抓到，不必等浏览器 E2E。
    """

    # 有写操作（POST/PATCH/DELETE/PUT）但不加载 app.js 的页面。
    # studio.html 已有注入；login.html 的登录 POST 走服务端豁免，不算。
    WRITE_PAGES_WITHOUT_APP_JS = ("settings.html", "reports.html", "studio.html")

    def test_every_write_page_injects_csrf_header(self):
        static = PROJECT_ROOT / "frontend" / "static"
        for name in self.WRITE_PAGES_WITHOUT_APP_JS:
            html = (static / name).read_text(encoding="utf-8")
            self.assertIn("csrfToken()", html, f"{name} 缺 csrfToken 读取")
            self.assertIn('"X-CSRF-Token"', html, f"{name} 的 api() 没注入 CSRF 头")

    def test_csrf_header_merged_not_replacing_content_type(self):
        """注入头时不能让 options.headers 覆盖掉 Content-Type（否则 JSON body 解析失败）。"""
        static = PROJECT_ROOT / "frontend" / "static"
        for name in ("settings.html", "reports.html"):
            html = (static / name).read_text(encoding="utf-8")
            self.assertIn(
                'Object.assign({ "Content-Type": "application/json" }, options.headers || {})',
                html,
                f"{name} 的 headers 合并写错了，会丢 Content-Type",
            )


if __name__ == "__main__":
    unittest.main()
