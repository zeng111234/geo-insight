// 页面字段契约 + 统一侧栏结构检查：按各页面的真实请求顺序，逐字段校验接口响应。
// 用法：先 python backend/run.py，再 node backend/scripts/pagecheck.mjs
// 换端口：GEO_BASE=http://127.0.0.1:8124 node backend/scripts/pagecheck.mjs
const BASE = process.env.GEO_BASE || "http://127.0.0.1:8123";
const API = BASE + "/v1";
const T = "11111111-1111-1111-1111-111111111111";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
const STATIC = fileURLToPath(new URL("../../frontend/static/", import.meta.url));
const fails = [];
const need = (c, name) => { if (!c) fails.push(name); console.log((c ? "PASS  " : "FAIL  ") + name); };
// 项目默认开鉴权：脚本必须在鉴权/免鉴权两种环境都能跑（见 MEMORY.md 规则）。
// 这是纯 Node fetch 脚本（非浏览器），登录方式是 POST /login 拿 Set-Cookie 后带上。
let sessionCookie = "";
const authFetch = (p, init = {}) => fetch(p, {
  ...init,
  headers: { ...(init.headers || {}), ...(sessionCookie ? { Cookie: sessionCookie } : {}) },
  redirect: init.redirect || "follow",
});

// 首次请求若 401，就自动登录一次；免鉴权环境这里直接返回。
async function ensureLogin() {
  const probe = await authFetch(`${API}/meta/methodology`);
  if (probe.status !== 401) return;
  const res = await authFetch(`${BASE}/login`, {
    method: "POST",
    redirect: "manual",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ email: "demo@example.com", password: "geo-demo-2026", next: "/" }).toString(),
  });
  const raw = res.headers.getSetCookie ? res.headers.getSetCookie() : [res.headers.get("set-cookie") || ""];
  const hit = raw.find((c) => c.startsWith("geo_session="));
  if (!hit) {
    console.log("FAIL  鉴权服务登录失败，拿不到 session cookie（换密码？服务是否可达？）");
    process.exit(1);
  }
  sessionCookie = hit.split(";")[0];
  const check = await authFetch(`${API}/meta/methodology`);
  if (check.status !== 200) {
    console.log("FAIL  登录后接口仍不可用：" + check.status);
    process.exit(1);
  }
  console.log("PASS  自动登录成功（鉴权环境）");
}
await ensureLogin();

const get = async (p) => {
  const r = await authFetch(p);
  if (r.status === 401) {
    console.log("FAIL  接口仍返回 401：登录态没带上（cookie 是否被拒？）");
    process.exit(1);
  }
  if (!r.ok) throw new Error(p + " -> " + r.status);
  return r.json();
};

// ---- 工作台 ----
const ov = await get(`${API}/trackers/${T}/metrics/overview?from=2026-11-01&to=2026-11-03`);
need(ov.cards.mention_rate.value === 0.2222, "工作台: 提及率 22.22%");
need(ov.cards.avg_rank.status === "insufficient_samples", "工作台: 平均排名样本不足");
need(ov.cards.sentiment.base === 2, "工作台: 情感分母=提及数");
const samples = await get(`${API}/trackers/${T}/metrics/mention_rate/samples?from=2026-11-01&to=2026-11-03`);
need(samples.sample_base === 9 && samples.items.length === 9, "工作台下钻: 提及率 9 条样本");
need(samples.items.filter((i) => i.mentioned).length === 2, "工作台下钻: 2 条计入提及");
need(samples.items.every((i) => "answer_id" in i && "in_denominator" in i), "工作台下钻: 字段齐全");
const rankSamples = await get(`${API}/trackers/${T}/metrics/avg_rank/samples?from=2026-11-01&to=2026-11-04`);
need(rankSamples.sample_base === 2 && rankSamples.items.filter((i) => i.in_denominator).length === 2, "工作台下钻: 平均排名分母只有 2 条位次样本");
need(rankSamples.items.some((i) => i.mentioned && !i.in_denominator), "工作台下钻: 无列表提及被排除在分母外");
const method = await get(`${API}/meta/methodology`);
need(method.definitions.length === 6 && method.rules.length >= 5, "口径说明: 6 条定义 + 规则");

// ---- 监控配置 ----
const detail = await get(`${API}/trackers/${T}`);
need(["name","brand","competitors","sampling","question_summary"].every((k) => k in detail), "配置页: 详情字段齐全");
// 仓储层返回 tuple，直接塞进响应会变成数组；页面按对象读 first_day 会静默显示「无」。
need(detail.data_range && !Array.isArray(detail.data_range) &&
     detail.data_range.first_day === "2026-11-01" && detail.data_range.last_day === "2026-11-30",
     "配置页: data_range 是对象且给出真实首末日");
need(detail.brand.aliases.length === 3, "配置页: 3 个别名");
need(detail.sampling.daily_planned === 60, "配置页: 每日计划 60 条");
need(detail.sampling.failure.status === "over_threshold" && detail.sampling.failure.failed === 214,
     "配置页: 失败率 214 次超阈值");
need(detail.competitors.version === 1 && detail.competitors.items.length === 5, "配置页: 竞品 v1 五个");
const qs = await get(`${API}/trackers/${T}/questions`);
need(qs.summary.core === 4 && qs.summary.long_tail === 8, "配置页: 4 核心 8 长尾");
need(qs.items.every((q) => "sample_total" in q && "tier" in q), "配置页: 问题字段齐全");

// ---- 报告中心 ----
const list = await get(`${API}/trackers/${T}/reports`);
need(list.items.length >= 2, "报告中心: 至少 2 份报告");
need(list.items.every((r) => ["period_start","period_end","days","valid_total","mention_rate","comparable","created_at","metric_version"].every((k) => k in r)), "报告中心: 列表字段齐全");
const okReport = list.items.find((r) => r.period_start === "2026-11-04");
need(okReport.comparable === true && okReport.delta_pp === 23.3, "报告中心: 可比 + +23.3pp");

// ---- 报告详情 ----
const rep = await get(`${API}/reports/${okReport.id}`);
const p = rep.payload;
need(["tracker","period","sample","cards","trend","citations","models","model_set_hash","comparison","conclusions"].every((k) => k in p), "报告详情: payload 字段齐全");
need(rep.payload_hash.length === 64, "报告详情: 快照哈希 64 位");
need(p.conclusions.length >= 3, "报告详情: 结论已生成");
need(p.trend.length === 2, "报告详情: 每日趋势 2 点");
need(p.cards.mention_rate.value === 0.4, "报告详情: 本期提及率 40%");

// ---- 答案详情 ----
const ans = (await get(`${API}/trackers/${T}/answers?from=2026-11-01&to=2026-11-03&limit=50`)).items;
const mentioned = ans.filter((a) => a.mentions.length);
const details = await Promise.all(mentioned.map((a) => get(`${API}/answers/${a.id}`)));
const det = details[0];
need(["question_text","model_key","collected_at","text","mentions","citations","blocks","text_hash"].every((k) => k in det), "答案详情: 字段齐全");
const positions = details.map((d) => d.mentions[0].position).sort();
need(JSON.stringify(positions) === "[2,5]", "答案详情: 两条提及位次为 2 与 5（实际 " + JSON.stringify(positions) + "）");
need(det.citations.length === 2, "答案详情: 2 条引用");
need(det.blocks.length === 5, "答案详情: 5 条推荐清单");

// ---- 统一侧栏：12 页结构一致性 ----
// 旧页面 aside 写法有三种（class="side" / class="side no-print" / 裸 aside），
// 注入正则漏掉任意一种就会留下第二份侧栏与第二个 active 高亮。
const PAGES = [
  "/", "/settings", "/reports", "/report/any", "/answer/any", "/studio",
  "/monitor", "/insight", "/opportunities", "/tasks", "/publish", "/assets", "/sources"
];
for (const path of PAGES) {
  const r = await authFetch(BASE + path);
  const html = await r.text();
  const side = (html.match(/<aside class="side">/g) || []).length;
  const active = (html.match(/class="active"/g) || []).length;
  const dead = (html.match(/href="#"/g) || []).length;
  const groups = (html.match(/class="group"/g) || []).length;
  const foot = (html.match(/class="page-foot"/g) || []).length;
  // 分组数必须与 nav.py 的 NAV_GROUPS 长度一致：决策 009 后是 5 组
  // （工作台 / 优化执行 / 分析报告 / 品牌资产 / 探索工具）。加分组时两边一起改。
  need(r.status === 200 && side === 1 && active === 1 && dead === 0 && groups === 5 && foot === 1,
    `侧栏: ${path} 单侧栏/单高亮/5 分组/单页脚/无死链 (side=${side} active=${active} dead=${dead} group=${groups} foot=${foot})`);
}

// ---- 侧栏样式契约（静态闸门） ----
// 曾踩过两个坑：app.css 在 1180px 直接 display:none 让整块导航消失；
// studio.html 没引 app.css、还自带 aside{display:none}，导致那一页侧栏长得和别的页不一样。
const PAGE_FILES = [
  "index.html", "settings.html", "reports.html", "report.html", "answer.html", "studio.html",
  "monitor.html", "insight.html", "opportunities.html", "tasks.html", "publish.html",
  "assets.html", "sources.html",
];
for (const file of PAGE_FILES) {
  const html = readFileSync(STATIC + file, "utf8");
  need(html.includes('href="/static/app.css"') && html.includes('href="/static/theme.css"'),
    `样式契约: ${file} 引入统一 theme/app 样式表`);
  need(!/aside[^{]*\{[^}]*display:\s*none/.test(html), `样式契约: ${file} 未在页面内联样式里隐藏侧栏`);
}
const css = readFileSync(STATIC + "app.css", "utf8");
need(!/aside\.side\s*\{[^}]*display:\s*none/.test(css), "样式契约: app.css 不隐藏统一侧栏");
need(/\.shell\s*\{\s*grid-template-columns:\s*168px/.test(css), "样式契约: 中等宽度保留侧栏列");
// 面板高度不齐留白：所有栅格必须 align-items:stretch，不允许 start（矮面板下方会露出页面底色）
for (const f of ["monitor.html", "sources.html", "tasks.html", "settings.html"]) {
  const html = readFileSync(STATIC + f, "utf8");
  const grids = (html.match(/\.(?:mon-grid|src-grid|kanban)\s*\{[^}]*\}/g) || []);
  for (const g of grids) {
    need(!/align-items:\s*start/.test(g), `样式契约: ${f} 的自定义栅格不用 align-items:start（${g.slice(0, 40)}…）`);
  }
}
need(/\.grid\.c2, \.grid\.c3, \.main-split, \.grid\.side\s*\{\s*align-items:\s*stretch/.test(css),
  "样式契约: app.css 栅格用 align-items:stretch 保持面板等高");

console.log(fails.length ? `\n${fails.length} FAILED` : "\nALL PAGE CONTRACT CHECKS PASSED");
process.exit(fails.length ? 1 : 0);
