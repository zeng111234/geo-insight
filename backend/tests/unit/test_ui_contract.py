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


class OpportunityToStudioLoopContractTest(unittest.TestCase):
    """「发现机会 → 写这篇」闭环：真实试点对象必须能落成草稿。

    之前 studio.html 把 TRACKER 写死成演示对象，机会页跳过来时问题归属
    被后端拒（QuestionNotFoundError），真实数据所在对象 0 篇文章。
    """

    def test_studio_tracker_comes_from_url(self):
        html = STUDIO.read_text(encoding="utf-8")
        self.assertIn('new URLSearchParams(location.search).get("tracker")', html)
        self.assertNotIn(
            'const TRACKER = "11111111-1111-1111-1111-111111111111";',
            html,
            "TRACKER 又写死成演示对象了",
        )

    def test_opportunity_link_carries_tracker(self):
        html = (PROJECT_ROOT / "frontend" / "static" / "opportunities.html").read_text(encoding="utf-8")
        self.assertIn("/studio?tracker=", html)
        self.assertIn("encodeURIComponent(G.tracker())", html)

    def test_fill_trackers_honors_url_tracker(self):
        js = (PROJECT_ROOT / "frontend" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('new URLSearchParams(location.search).get("tracker")', js)
        self.assertIn("sel.value = wanted", js)


class PublishLinkHonestyContractTest(unittest.TestCase):
    """发布页回链：种子 demo 假链不能渲染成可点真实回链。

    隔壁在 tasks.html 做了 isValidUrl 排除 demo-article，publish.html 曾漏掉——
    4 条 published 全是假 URL 却显示可点「回链 →」，像是真发布成功过。
    """

    def test_publish_page_excludes_demo_fake_links(self):
        html = (PROJECT_ROOT / "frontend" / "static" / "publish.html").read_text(encoding="utf-8")
        self.assertIn("function isValidUrl", html)
        self.assertIn("/demo-article/i", html)
        self.assertIn("演示回链", html)
        self.assertIn("isValidUrl(p.published_url)", html)


class AnswerChatViewContractTest(unittest.TestCase):
    """回答详情呈现为对话气泡（头像+名字+左右气泡），不是纯文字墙。

    老板反馈：回答页纯文字不合格，要像参考系统那样带模型头像和名字。
    守模型显示名口径与后端 domain/channels.py 一致。
    """

    def test_answer_page_renders_chat_bubbles(self):
        html = (PROJECT_ROOT / "frontend" / "static" / "answer.html").read_text(encoding="utf-8")
        self.assertIn("chat-head", html)
        self.assertIn("bubble-row mine", html, "用户提问气泡要在右侧")
        self.assertIn("bubble ai", html)
        self.assertIn("MODEL_META", html)
        for label in ("DeepSeek", "Kimi", "豆包", "文心一言"):
            self.assertIn(label, html, f"模型显示名缺失: {label}")

    def test_model_labels_match_backend_channels(self):
        from geo.domain.channels import CHANNELS
        labels = {c["label"] for c in CHANNELS}
        html = (PROJECT_ROOT / "frontend" / "static" / "answer.html").read_text(encoding="utf-8")
        # 前端至少要有后端 8 模型对应的显示名
        for label in ("豆包", "DeepSeek", "Kimi", "千问", "元宝", "文心一言", "夸克 AI", "Bing AI"):
            self.assertIn(label, html, f"模型 {label} 前端缺失")


class ModelBadgeContractTest(unittest.TestCase):
    """全局模型名徽章：所有表格/列表用「头像+名字」，不再纯文字 model_key。

    参考系统 AI平台列带 Kimi 头像+名字，我们此前全是纯文字。抽到 app.js
    公共函数 + app.css 样式，各页面统一调用，避免重复代码与口径漂移。
    """

    def test_app_exports_model_badge(self):
        js = (PROJECT_ROOT / "frontend" / "static" / "app.js").read_text(encoding="utf-8")
        css = (PROJECT_ROOT / "frontend" / "static" / "app.css").read_text(encoding="utf-8")
        self.assertIn("modelBadge: modelBadge", js, "app.js 未导出 modelBadge")
        self.assertIn(".model-badge-av", css, "app.css 缺头像样式")
        for label in ("豆包", "DeepSeek", "Kimi", "千问", "元宝", "文心一言", "夸克 AI", "Bing AI"):
            self.assertIn(label, js, f"模型显示名缺失: {label}")

    def test_pages_use_model_badge(self):
        static = PROJECT_ROOT / "frontend" / "static"
        # 这些页面有模型名表格/列表，必须走公共徽章函数
        for name in ("index.html", "monitor.html", "insight.html",
                     "opportunities.html", "pilot.html", "publish.html"):
            html = (static / name).read_text(encoding="utf-8")
            self.assertIn("modelBadge(", html, f"{name} 未使用模型徽章")


class ApiPathPrefixContractTest(unittest.TestCase):
    """G.get/post 内部 API 已含 /v1，页面调用不得再传 /v1 前缀。

    update.html 曾 5 处写成 G.get("/v1/system/update/...")，拼成 /v1/v1/...
    全 404——系统更新页整个坏掉。这里守住全站不再出现重复前缀。
    """

    def test_no_double_v1_prefix(self):
        import re
        static_dir = PROJECT_ROOT / "frontend" / "static"
        violations = []
        for path in static_dir.glob("*.html"):
            text = path.read_text(encoding="utf-8")
            for m in re.finditer(r"G\.(get|post|patch|del)\(\s*[\"'`]/v1/", text):
                line = text[: m.start()].count("\n") + 1
                violations.append(f"{path.name}:{line}")
        self.assertEqual(violations, [], f"这些页面把 /v1 传给了已含 /v1 的 G 方法：{violations}")

    def test_update_page_calls_correct_paths(self):
        html = (PROJECT_ROOT / "frontend" / "static" / "update.html").read_text(encoding="utf-8")
        self.assertIn('G.get("/system/update/status")', html)
        self.assertNotIn('"/v1/system/update/', html)
        self.assertEqual(html.count("/system/update/"), 5, "更新页 5 个接口路径应都不带 /v1")


class AiGenerateButtonContractTest(unittest.TestCase):
    """工作室 AI 生成草稿按钮：接后端 generate 接口，失败显式报错不假造。

    后端 ffedaa5 落地了 POST /trackers/{id}/articles/generate，前端按钮曾没接。
    """

    def test_ai_generate_button_wired(self):
        html = (PROJECT_ROOT / "frontend" / "static" / "studio.html").read_text(encoding="utf-8")
        self.assertIn('id="aiGenerate"', html, "AI 生成按钮缺失")
        self.assertIn('id="aiHint"', html, "生成状态提示缺失")
        self.assertIn('"/articles/generate"', html, "未接后端 generate 接口")
        self.assertIn("生成中", html, "缺 loading 提示")

    def test_failure_is_honest(self):
        html = (PROJECT_ROOT / "frontend" / "static" / "studio.html").read_text(encoding="utf-8")
        self.assertIn("AI 生成失败：", html, "失败没有显式报错")
        self.assertIn("e.status === 503", html, "无 key 503 没有专门提示")
        # 不允许静默吞掉错误假装成功
        self.assertNotIn('catch (e) {}', html)


class E2EScriptAuthContractTest(unittest.TestCase):
    """AI 生成按钮 E2E 必须能在默认鉴权环境跑，不能只在免鉴权环境绿。

    默认服务开鉴权，直接 navigate /studio 会被 302 到登录页；脚本若无登录检测，
    报晦涩 CDP 异常（隔壁踩过）。Node fetch 不带 cookie 也会 401。
    """

    def test_aigen_script_handles_auth(self):
        script = (PROJECT_ROOT / "backend" / "scripts" / "aigenbuttoncheck.mjs").read_text(encoding="utf-8")
        self.assertIn('location.pathname', script, "缺当前页检测")
        self.assertIn('"/login"', script, "缺登录页检测")
        self.assertIn("geo-demo-2026", script, "缺自动登录凭据")
        # count 必须在浏览器里 fetch（带 cookie），不能用 Node fetch
        self.assertIn("await ev(`(async function(){", script, "文章数读取未走浏览器 cookie")


class AssistantDrawerHonestyContractTest(unittest.TestCase):
    """助手抽屉文案：助手对话未接是真的，但不能笼统说「AI 能力未接入」。

    2026-11-27：DeepSeek key 已配、工作室 AI 写稿已真接，抽屉还说
    「内容成稿由人工完成」「AI 能力尚未接入」是反向说假话。
    """

    def test_drawer_distinguishes_chat_from_writing(self):
        js = (PROJECT_ROOT / "frontend" / "static" / "global-bar.js").read_text(encoding="utf-8")
        # 助手对话未接入：守住诚实底线（旧断言不能破）
        self.assertIn("需 key", js)
        self.assertIn("未接入", js)
        self.assertIn("助手对话", js, "没说明未接入的是对话本身")
        # AI 写稿已真接：必须指路，不能误导用户以为整个系统没 AI
        self.assertIn("AI 写稿已经能用", js, "没指出 AI 写稿可用")
        self.assertIn("内容工作室", js, "没指路到真正可用的入口")
        # 不能再笼统说 AI 能力整体未接入
        self.assertNotIn("AI 能力尚未接入", js, "过时表述还在")


if __name__ == "__main__":
    unittest.main()
