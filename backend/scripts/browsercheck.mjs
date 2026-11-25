// 真实浏览器 DOM 冒烟：不引入 Playwright / 不装依赖，直接调系统 Chrome|Edge 的 --dump-dom。
//
// 为什么需要它：HTTP 200 只证明路由存在，node --check 只证明语法没错，
// 两者都拦不住「页面读了接口里不存在的字段 → 整页报错」。这一层验证的是
// JS 真的在浏览器里跑完、异步请求返回后 DOM 里真的有数据。
//
// 用法：先 python backend/run.py，再 node backend/scripts/browsercheck.mjs
// 换端口：GEO_BASE=http://127.0.0.1:8124 node backend/scripts/browsercheck.mjs
// 指定浏览器：GEO_CHROME="C:\path\to\chrome.exe"
import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";

const BASE = process.env.GEO_BASE || "http://127.0.0.1:8123";
const CANDIDATES = [
  process.env.GEO_CHROME,
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
].filter(Boolean);
const CHROME = CANDIDATES.find(existsSync);

if (!CHROME) {
  console.log("SKIP  未找到 Chrome / Edge，跳过浏览器层检查（接口层与页面层检查仍有效）");
  process.exit(0);
}

// 详情页必须用真实 ID：拿假 ID 去请求只会得到 404，页面显示「读取失败」是正确行为，
// 不能拿来当成页面坏了的证据。
const TRACKER = "11111111-1111-1111-1111-111111111111";

// 鉴权开着时，浏览器里打开的每个页面都会跳到登录页，DOM 断言没有意义。
// 早点说清楚，别让人对着一屏「渲染失败」猜。
const probe = await fetch(`${BASE}/v1/trackers/${TRACKER}/reports`);
if (probe.status === 401) {
  console.log("FAIL  服务开启了鉴权，浏览器里只会看到登录页。");
  console.log("      用 GEO_AUTH_REQUIRED=0 python backend/run.py 启动后再跑本脚本。");
  process.exit(1);
}

let reportId = "", answerId = "";
try {
  const reps = await (await fetch(`${BASE}/v1/trackers/${TRACKER}/reports`)).json();
  reportId = (reps.items || [])[0]?.id || "";
  const ans = await (await fetch(`${BASE}/v1/trackers/${TRACKER}/answers?from=2026-11-01&to=2026-11-05&limit=1`)).json();
  answerId = (ans.items || [])[0]?.id || "";
} catch (e) { /* 下面统一报失败 */ }

const PAGES = [
  "/", "/settings", "/reports", "/report/" + reportId, "/answer/" + answerId, "/studio",
  "/monitor", "/monitor?tab=answers", "/monitor?tab=metrics",
  "/monitor?tab=trend", "/monitor?tab=trend&g=week", "/monitor?tab=sources",
  "/monitor?tab=competitors", "/monitor?tab=competitorStats", "/monitor?tab=media",
  "/monitor?tab=sentiment", "/monitor?tab=health",
  "/insight", "/opportunities", "/tasks", "/publish", "/assets", "/sources", "/pilot"
];

// 页面专属的关键渲染结果：容器后面必须出现真实表格/图表，而不是占位或报错。
// 正则用 id="x"[^>]*> 而不是 id="x"> —— 元素上常带 style 等其它属性。
const EXTRA = {
  "/": [
    ["每日摘要表", /<div class="body" id="trendTable"[^>]*>[\s\S]{0,240}<table/],
    ["明细翻页条", /class="pager"/],
  ],
  // 问题监控是工作台：默认 tab 是指标看板，分页条在问答明细 tab 上，
  // 所以拆成两个 URL 各查各的，而不是笼统要求"这页必须有分页"。
  "/monitor": [
    ["标签页导航", /id="tabs"[^>]*>[\s\S]{0,600}data-tab="dashboard"/],
    ["指标看板卡", /class="kpi[\s\S]{0,300}提问次数/],
    ["监控列表项", /class="titem[\s\S]{0,200}data-tracker=/],
    ["分问题表现表", /分问题表现[\s\S]{0,600}<th>问题<\/th>/],
    ["分问题对比条", /<th>问题<\/th>[\s\S]{0,2400}class="cmp-bar"/],
    ["趋势折线", /提及率趋势[\s\S]{0,300}class="spark"[\s\S]{0,300}<path/],
    ["引用概览", /独立域名<\/i>/],
    ["核心词标签", /class="tag kw"/],
  ],
  "/monitor?tab=answers": [
    ["问题下拉", /id="qSelect"/],
    ["筛选条在 tab 之上", /id="filterBar"[\s\S]{0,2200}id="tabs"/],
    ["10 个 tab 带数字", /role="tab"[^>]*>指标看板<span class="n">/],
    ["新建右侧抽屉", /id="newDrawer"[\s\S]{0,300}id="ntName"/],
    ["引用信源第三栏", /id="citeBody"[\s\S]{0,400}cite-item/],
    ["全部/已监控筛选", /id="citeView"[\s\S]{0,160}已监控/],
    ["明细翻页条", /class="pager"/],
  ],
  "/monitor?tab=metrics": [
    ["指标切换按钮", /class="metric-bar"[\s\S]{0,400}data-metric="avg_rank"/],
    ["样本表", /id="metricTable"[^>]*>[\s\S]{0,300}<table/],
    ["明细翻页条", /class="pager"/],
  ],
  "/monitor?tab=trend": [
    ["粒度切换", /data-granularity="day"[^>]*class="on"/],
    ["趋势折线", /class="spark"[\s\S]{0,200}<path/],
    ["每日摘要表", /id="trendTable"[^>]*>[\s\S]{0,300}<table/],
  ],
  "/monitor?tab=trend&g=week": [
    ["按周高亮", /data-granularity="week"[^>]*class="on"/],
    ["周表头", /<th>周（起）<\/th>/],
    ["天数列", /<th class="num">天数<\/th>/],
    ["按周数据行", /<tbody>[\s\S]{0,200}<td>\d{4}-\d{2}-\d{2}<\/td><td class="num">\d+<\/td>/],
  ],
  "/monitor?tab=sources": [
    ["类型分布条", /class="bar-row"[\s\S]{0,200}class="fill"/],
    ["URL 明细表", /<th>页面<\/th>/],
  ],
  "/monitor?tab=competitors": [
    ["竞品排名表", /<th>竞品<\/th>[\s\S]{0,300}class="cmp-bar"/],
  ],
  "/monitor?tab=competitorStats": [
    ["共现矩阵", /class="matrix"[\s\S]{0,400}class="diag"/],
  ],
  "/monitor?tab=media": [
    ["类型切换", /class="metric-bar"[\s\S]{0,400}data-media="news"/],
    ["引用明细表", /<th>页面<\/th>/],
    ["明细翻页条", /class="pager"/],
  ],
  "/monitor?tab=sentiment": [
    ["按模型情感表", /<th>模型<\/th>[\s\S]{0,300}<th class="num">负面<\/th>/],
    ["情感堆叠条", /class="sentiment"[\s\S]{0,120}class="pos"/],
    ["情感口径", /分母 = 各模型下本方品牌的提及样本/],
  ],
  "/monitor?tab=health": [
    ["失败率告警闭环", /采集失败率[\s\S]{0,500}失败原因：/],
    ["应采/已采指标", /<div class="k">应采<\/div>[\s\S]{0,200}<div class="k">已采<\/div>/],
    ["按模型达成率", /<th>模型<\/th>[\s\S]{0,300}<th class="num">达成率<\/th>/],
    ["按天明细", /<div class="rule"[^>]*>按天<\/div>[\s\S]{0,200}<th>日期<\/th>/],
  ],
  "/insight": [
    ["结论列表", /<ul class="concl">/],
    ["趋势图", /<svg class="spark"/],
    ["分问题表现", /<div class="body" id="byQuestion"[^>]*>[\s\S]{0,240}<table/],
    ["明细翻页条", /class="pager"/],
  ],
  "/studio": [
    ["积分徽章", /id="creditBadge"[\s\S]{0,120}id="creditBalance"/],
    ["六阶段步骤条", /class="stage-step[\s\S]{0,900}data-stage="publish"/],
    ["阶段状态已落库", /stage-step (current|done)/],
    ["新建文章入口", /id="newArticle"/],
    ["正文编辑器", /<textarea id="body"/],
  ],
  "/opportunities": [
    ["机会分排序", /<div id="rank"[^>]*>[\s\S]{0,240}<div class="opp"/],
    ["写这篇入口", /写这篇 →/],
    ["排序理由", /<div class="why">本品牌 \d+\/\d+ 条提及/],
    ["覆盖矩阵", /<div class="body" id="matrix"[^>]*>[\s\S]{0,240}<table/],
    ["样本明细", /<div class="body" id="samples"[^>]*>[\s\S]{0,240}<table/],
    ["明细翻页条", /class="pager"/],
  ],
  "/tasks": [["文章明细", /<div class="body" id="articleTable"[^>]*>[\s\S]{0,240}<table/]],
  "/settings": [
    ["采集渠道矩阵", /<div class="channel-grid"[^>]*>[\s\S]{0,300}channel-cell/],
    ["13 个未接入显式标注", /3 已接入 · 13 未接入[\s\S]{0,1800}未接入<\/div>/],
  ],
  "/pilot": [
    ["结案结论", /<div class="verdict/],
    ["目标问题对比", /目标问题（发布前）/],
    ["按模型拆", /<th>模型<\/th>/],
  ],
  "/publish": [
    ["发布记录", /<div id="pubs"[^>]*>[\s\S]{0,240}<div class="pub-row"/],
    ["渠道状态", /<div class="body" id="channels"[^>]*>[\s\S]{0,240}<table/],
    ["发布效果对比", /<div id="impact"[^>]*>[\s\S]{0,240}<div class="imp"/],
    ["目标问题口径", /<div class="scope">目标问题：/],
    ["按模型拆", /<th>模型<\/th>/],
    ["预注册判定", /<div class="verdict [a-z_]+"><b>/],
  ],
  "/assets": [["问题覆盖", /<div class="body" id="questions"[^>]*>[\s\S]{0,240}<table/]],
  "/sources": [["明细翻页条", /class="pager"/]],
  "/reports": [
    ["概览卡", /<div class="cards" id="summary"[^>]*>[\s\S]{0,240}<div class="card"/],
    ["快照明细", /<tbody id="hashRows"[^>]*>[\s\S]{0,240}<tr/],
  ],
};

const fails = [];
const need = (ok, name) => {
  if (!ok) fails.push(name);
  console.log((ok ? "PASS  " : "FAIL  ") + name);
};

need(Boolean(reportId && answerId), `演示数据可解析出报告 / 答案 ID (${reportId.slice(0, 8)} / ${answerId.slice(0, 8)})`);

for (const path of PAGES) {
  let dom;
  try {
    dom = execFileSync(CHROME, [
      "--headless=new", "--disable-gpu", "--no-sandbox",
      "--disable-dev-shm-usage", "--virtual-time-budget=6000",
      "--dump-dom", BASE + path,
    ], {
      encoding: "utf8", maxBuffer: 32 * 1024 * 1024,
      stdio: ["ignore", "pipe", "ignore"], timeout: 40000,
    });
  } catch (e) {
    need(false, `浏览器: ${path} 渲染失败 (${String(e.message).slice(0, 90)})`);
    continue;
  }
  // --dump-dom 会原样带上 <script> 源码，里面的错误模板字符串（如
  // `<div class="notice error">读取失败…`）会被当成渲染结果误报。只看真实节点。
  const rendered = dom.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, "");
  const errors = (rendered.match(/class="notice error"/g) || []).length;
  const pending = (rendered.match(/<div class="empty">加载中…<\/div>/g) || []).length;
  const side = (rendered.match(/<aside class="side">/g) || []).length;
  const active = (rendered.match(/class="active"/g) || []).length;
  need(errors === 0 && pending === 0 && side === 1 && active === 1,
    `浏览器: ${path} 无报错/无残留占位/单侧栏 (error=${errors} pending=${pending} side=${side} active=${active})`);

  for (const [label, re] of (EXTRA[path] || [])) {
    need(re.test(rendered), `浏览器: ${path} ${label}已渲染`);
  }
}

console.log(fails.length ? `\n${fails.length} FAILED` : "\nALL BROWSER DOM CHECKS PASSED");
process.exit(fails.length ? 1 : 0);
