// 真实浏览器交互冒烟：用 CDP 点按钮，而不是只看 DOM 有没有渲染。
//
// 为什么单独要这一层：browsercheck.mjs 只证明"JS 跑完、数据渲染出来了"，
// 但删除确认这种交互——弹框是否弹出、名称不对时按钮是否禁用、取消是否真的不删——
// 只有真点一遍才知道。它守的是不可逆操作，值得每次改动都回归。
//
// 用 Node 22 自带的全局 WebSocket，不装 puppeteer/playwright。
// 用法：先 python backend/run.py，再 node backend/scripts/interactioncheck.mjs
// 换端口：GEO_BASE=http://127.0.0.1:8124 node backend/scripts/interactioncheck.mjs
// 指定浏览器：GEO_CHROME="C:\path\to\chrome.exe"
//
// 注意：这一轮会点「暂停」再「恢复」，不留状态；删除弹框点的是「取消」，
// 不会真的删数据。真删的测试在 tests/integration/test_tracker_delete.py 里用事务回滚做。
// 首页下钻抽屉是只读交互（点卡片看样本、看口径、关闭），不改任何数据。

import { spawn } from "node:child_process";
import { existsSync } from "node:fs";

const BASE = process.env.GEO_BASE || "http://127.0.0.1:8123";
const PORT = Number(process.env.GEO_CDP_PORT || 9355);
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
  console.error("没有找到 Chrome/Edge。用 GEO_CHROME 指定浏览器路径。");
  process.exit(1);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const chrome = spawn(CHROME, [
  "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
  `--remote-debugging-port=${PORT}`,
  "--window-size=1680,1000",
  `--user-data-dir=${process.env.TEMP || "/tmp"}/geo-interactioncheck`,
  "about:blank",
], { stdio: "ignore" });

async function target() {
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/json/list`);
      const list = await r.json();
      const page = list.find((t) => t.type === "page");
      if (page) return page.webSocketDebuggerUrl;
    } catch { /* 浏览器还在起 */ }
    await sleep(250);
  }
  throw new Error("拿不到 CDP 页面，浏览器可能没起来");
}

let id = 0;
const pending = new Map();
let ws;
function send(method, params = {}) {
  const mid = ++id;
  ws.send(JSON.stringify({ id: mid, method, params }));
  return new Promise((resolve, reject) => pending.set(mid, { resolve, reject }));
}
async function evaluate(expr) {
  const r = await send("Runtime.evaluate", {
    expression: expr, awaitPromise: true, returnByValue: true,
  });
  if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
  return r.result.value;
}

const results = [];
const check = (ok, label, extra = "") => {
  results.push(ok);
  console.log(`${ok ? "PASS" : "FAIL"}  ${label}${extra ? ` (${extra})` : ""}`);
};

try {
  ws = new WebSocket(await target());
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) {
      const p = pending.get(m.id);
      pending.delete(m.id);
      m.error ? p.reject(new Error(m.error.message)) : p.resolve(m.result);
    }
  };
  await send("Page.enable");
  await send("Runtime.enable");

  const errors = [];
  ws.addEventListener("message", (e) => {
    const m = JSON.parse(e.data);
    if (m.method === "Runtime.exceptionThrown") {
      errors.push(m.params.exceptionDetails.text || "exception");
    }
  });

  // 所有后续导航的页面都注入 print 桩：headless 下真弹打印对话框会卡住，
  // 用可观测的计数桩验证「按钮确实调了 window.print」，而不是只验按钮存在。
  await send("Page.addScriptToEvaluateOnNewDocument", {
    source: "window.__printCount = 0; window.print = function () { window.__printCount++; };",
  });

  await send("Page.navigate", { url: `${BASE}/monitor` });
  await sleep(3500);

  // 1. 对象头按钮齐全且顺序与参考系统一致
  const labels = await evaluate(`
    Array.from(document.querySelectorAll('.mon-head .acts > *')).map(e => e.textContent.trim())
  `);
  check(
    JSON.stringify(labels) === JSON.stringify(["下载报告", "表格浏览", "导出", "刷新", "暂停", "删除", "编辑"]),
    "对象头 7 个按钮，顺序同参考系统", labels.join(" | ")
  );

  // 2. 点删除 → 弹框出现，确认按钮初始禁用
  await evaluate(`document.getElementById('deleteBtn').click()`);
  await sleep(500);
  check(await evaluate(`document.getElementById('delDialog').open`) === true, "点删除弹出确认框");
  check(await evaluate(`document.getElementById('delOk').disabled`) === true, "未输入名称时「永久删除」禁用");

  // 3. 输错名字仍禁用，逐字输对才放行
  await evaluate(`
    (function () {
      var el = document.getElementById('delInput');
      el.value = '随便乱打';
      el.dispatchEvent(new Event('input'));
    })();
  `);
  const afterWrong = await evaluate(`document.getElementById('delOk').disabled`);
  const realName = await evaluate(`document.getElementById('delName').textContent`);
  await evaluate(`
    (function () {
      var el = document.getElementById('delInput');
      el.value = ${JSON.stringify("__NAME__")};
      el.dispatchEvent(new Event('input'));
    })();
  `.replace("__NAME__", realName));
  const afterRight = await evaluate(`document.getElementById('delOk').disabled`);
  check(afterWrong === true, "名称不对时仍然禁用");
  check(afterRight === false, "逐字输入正确名称后放行", realName);

  // 4. 取消，不能真删
  await evaluate(`document.getElementById('delDialog').close('cancel')`);
  await sleep(300);
  const stillThere = await evaluate(`document.getElementById('objName').textContent`);
  check(stillThere.length > 0 && stillThere !== "—", "取消后对象还在", stillThere);

  // 5. 暂停 → 按钮变「恢复」+ 标签「已暂停」
  await evaluate(`document.getElementById('pauseBtn').click()`);
  await sleep(1200);
  const paused = await evaluate(`({
    btn: document.getElementById('pauseBtn').textContent.trim(),
    tag: document.getElementById('objStatus').textContent.trim(),
  })`);
  check(paused.btn === "恢复" && paused.tag === "已暂停", "暂停生效", `${paused.btn}/${paused.tag}`);

  // 6. 恢复回去，别把演示数据留在暂停态
  await evaluate(`document.getElementById('pauseBtn').click()`);
  await sleep(1200);
  const resumed = await evaluate(`({
    btn: document.getElementById('pauseBtn').textContent.trim(),
    tag: document.getElementById('objStatus').textContent.trim(),
  })`);
  check(resumed.btn === "暂停" && resumed.tag === "监控中", "恢复生效", `${resumed.btn}/${resumed.tag}`);

  // 7. 新建抽屉：点「＋ 新建」打开右侧抽屉，字段齐全
  await evaluate(`document.getElementById('newTracker').click()`);
  await sleep(300);
  check(await evaluate(`document.getElementById('newDrawer').open`) === true, "点新建打开右侧抽屉");
  const fields = await evaluate(`
    ['ntWebsite','ntName','ntBrand','ntKeywords','ntIndustry','ntRegion','ntAliases','ntCompetitors','ntQuestions']
      .map(function (i) { return !!document.getElementById(i); })
  `);
  check(fields.every(Boolean), "抽屉字段齐全（官网/名称/品牌/核心词/行业/地区/别名/竞品/问题）", fields.join(","));

  // 8. 取消关闭抽屉，不新建对象
  await evaluate(`document.getElementById('ntCancel').click()`);
  await sleep(200);
  check(await evaluate(`!document.getElementById('newDrawer').open`) === true, "取消关闭抽屉");

  // 8b. 表单提交真建对象 + 下拉同步（E1e 真 bug：建完 select 没重建，选不中新对象）
  // 会写库，用产品自己的 DELETE 接口清理，finally 兜底保证不留脏数据。
  const TEMP_NAME = "CDP交互检查临时对象";
  let createdId = null;
  const optionsBefore = await evaluate(`document.querySelectorAll('#tracker option').length`);
  try {
    await evaluate(`document.getElementById('newTracker').click()`);
    await sleep(600);
    const channels = await evaluate(`({
      total: document.querySelectorAll('#ntChannels label').length,
      enabled: document.querySelectorAll('#ntChannels input:not([disabled])').length,
      offLabel: document.getElementById('ntChannels').textContent.indexOf('未接入') >= 0,
    })`);
    check(channels.total === 16, "渠道清单渲染 16 格", `${channels.total} 格`);
    check(channels.enabled === 3 && channels.offLabel, "已接入 3 个可勾、未接入照实标注",
      `可勾 ${channels.enabled} / 标注未接入 ${channels.offLabel}`);
    await evaluate(`(function () {
      document.getElementById('ntName').value = ${JSON.stringify(TEMP_NAME)};
      document.getElementById('ntBrand').value = '交互检查品牌';
      document.getElementById('ntWebsite').value = 'https://example.com/check';
      document.getElementById('ntKeywords').value = '检查词A, 检查词B';
      document.getElementById('ntQuestions').value = '检查问题一？\\n*核心检查问题？';
    })()`);
    // 真实点击「创建」，不是 dispatchEvent——与用户动作一致
    await evaluate(`document.querySelector('#newTrackerForm button[type=submit]').click()`);
    await sleep(1800);

    const list = await (await fetch(`${BASE}/v1/trackers`)).json();
    const created = (list.items || []).find((t) => t.name === TEMP_NAME);
    createdId = created ? created.id : null;
    check(!!createdId, "表单提交真建出对象（接口可查）", createdId || "未创建");

    // P1：新字段真落库——从接口详情读回，不是只看输入框填过
    if (createdId) {
      const detail = await (await fetch(`${BASE}/v1/trackers/${createdId}`)).json();
      check(detail.website === "https://example.com/check", "官网地址落库并可回显", detail.website || "空");
      const kw = (detail.keywords || []).map((x) => (typeof x === "string" ? x : x.text || "")).join(",");
      check(kw.indexOf("检查词A") >= 0 && kw.indexOf("检查词B") >= 0, "核心词落库", kw || "空");
      const qs = await (await fetch(`${BASE}/v1/trackers/${createdId}/questions`)).json();
      const texts = (qs.items || []).map((q) => q.text).join("|");
      check(texts.indexOf("检查问题一") >= 0 && texts.indexOf("核心检查问题") >= 0,
        "监控问题落库", `${(qs.items || []).length} 条`);
      const core = (qs.items || []).some((q) => q.text.indexOf("核心检查") >= 0 && q.tier === "core");
      check(core, "* 开头的问题设为核心",
        (qs.items || []).map((q) => `${q.text}:${q.tier}`).join(" | "));
    }

    const afterSubmit = await evaluate(`({
      options: document.querySelectorAll('#tracker option').length,
      selected: document.getElementById('tracker').value,
      drawerOpen: document.getElementById('newDrawer').open,
    })`);
    check(afterSubmit.options === optionsBefore + 1, "创建后下拉新增一个选项",
      `${optionsBefore}→${afterSubmit.options}`);
    check(afterSubmit.selected === createdId, "创建后下拉自动选中新对象",
      `${afterSubmit.selected} / ${createdId}`);
    check(afterSubmit.drawerOpen === false, "创建后抽屉关闭");

    // P4：对象头「表格浏览」直达问答明细表格视图
    await evaluate(`document.getElementById('tableBrowseBtn').click()`);
    await sleep(900);
    const browse = await evaluate(`({
      tab: new URLSearchParams(location.search).get('tab'),
      view: new URLSearchParams(location.search).get('view'),
      hasTable: !!document.querySelector('#ansList table'),
      btnOn: (function () { var b = document.querySelector('.ans-view [data-view=table]'); return b && b.classList.contains('on'); })(),
    })`);
    check(browse.tab === "answers" && browse.view === "table", "表格浏览切到问答明细表格视图",
      `tab=${browse.tab} view=${browse.view}`);
    // 新建对象没有答案数据，表格区会渲染空提示而不是 <table>——
    // 这里守的是「视图切换生效」（按钮高亮 + 有内容区），不强制表格行存在。
    check(browse.btnOn, "表格视图按钮高亮（新建对象可无数据）", `on=${browse.btnOn}`);
  } finally {
    if (createdId) {
      const del = await fetch(`${BASE}/v1/trackers/${createdId}`, { method: "DELETE" });
      check(del.status === 204, "清理临时对象（DELETE 204）", `HTTP ${del.status}`);
      const finalList = await (await fetch(`${BASE}/v1/trackers`)).json();
      check((finalList.items || []).length === optionsBefore,
        "清理后 tracker 数回到原值", `${(finalList.items || []).length} 个`);
    }
  }

  // 9. 首页下钻抽屉：点指标卡片 → 抽屉打开 → 真加载出样本表（非零行、带分页）
  await send("Page.navigate", { url: `${BASE}/` });
  await sleep(4000);
  const cards = await evaluate(`document.querySelectorAll('#cards [data-metric]').length`);
  check(cards >= 6, "首页指标卡片可下钻", `${cards} 个`);

  await evaluate(`document.querySelector('#cards [data-metric]').click()`);
  await sleep(1500);
  const drill = await evaluate(`({
    show: document.getElementById('drawer').classList.contains('show'),
    hidden: document.getElementById('drawer').getAttribute('aria-hidden'),
    title: document.getElementById('drawerTitle').textContent.trim(),
    sub: document.getElementById('drawerSub').textContent,
    rows: document.querySelectorAll('#drawerBody tbody tr').length,
    hasError: !!document.querySelector('#drawerBody .notice.error'),
    hasPager: !!document.querySelector('#drawerBody [data-pager], #drawerBody .pager'),
  })`);
  check(drill.show === true && drill.hidden === "false", "点卡片打开下钻抽屉");
  check(drill.title === "提及率", "抽屉标题 = 所点指标", drill.title);
  check(/分母\s+\d+\s+条/.test(drill.sub) && !drill.hasError,
    "抽屉加载真接口口径（分母解析成功、无错误条）", drill.sub.slice(0, 50));
  check(drill.rows > 0, "抽屉渲染出样本行（不是空壳）", `${drill.rows} 行`);
  check(drill.hasPager === true, "抽屉样本带分页条");

  // 10. 关闭抽屉：按钮关闭后 aria-hidden 复位
  await evaluate(`document.getElementById('drawerClose').click()`);
  await sleep(300);
  check(await evaluate(`document.getElementById('drawer').getAttribute('aria-hidden') === 'true'`) === true,
    "关闭按钮关掉下钻抽屉");

  // 11. 口径说明抽屉：另一个入口，真加载出定义表与规则清单
  await evaluate(`document.getElementById('methodBtn').click()`);
  await sleep(1200);
  const method = await evaluate(`({
    show: document.getElementById('drawer').classList.contains('show'),
    title: document.getElementById('drawerTitle').textContent.trim(),
    sub: document.getElementById('drawerSub').textContent,
    rows: document.querySelectorAll('#drawerBody tbody tr').length,
    rules: document.querySelectorAll('#drawerBody li').length,
  })`);
  check(method.show === true && method.title === "口径说明", "口径按钮打开抽屉", method.title);
  check(/口径版本\s+v?\d/.test(method.sub) && method.rows >= 3 && method.rules >= 3,
    "口径抽屉加载定义表与规则（非空壳）", `${method.rows} 行 / ${method.rules} 规则`);

  // 12. 报告详情打印：动态取一份真报告 id（不写死，换库也能跑）
  const reportId = await evaluate(`
    (async function () {
      var ts = await (await fetch('/v1/trackers')).json();
      var tid = (ts.items || [])[0].id;
      var rs = await (await fetch('/v1/trackers/' + tid + '/reports')).json();
      return (rs.items || rs)[0].id;
    })()
  `);
  await send("Page.navigate", { url: `${BASE}/report/${reportId}` });
  await sleep(3500);
  const report = await evaluate(`({
    panels: document.querySelectorAll('#content .panel').length,
    cards: document.querySelectorAll('#content .card').length,
    h2: document.querySelector('#content h2') ? document.querySelector('#content h2').textContent : '',
    hasError: !!document.querySelector('#content .notice.error'),
  })`);
  check(report.panels >= 3 && report.cards >= 3 && !report.hasError,
    "报告详情加载出真快照（正文 panel + 概览卡）", `${report.panels} panel / ${report.cards} card`);
  check(report.h2.length > 0, "报告标题非空", report.h2.slice(0, 30));

  const beforePrint = await evaluate(`window.__printCount`);
  await evaluate(`document.getElementById('printBtn').click()`);
  await sleep(300);
  const afterPrint = await evaluate(`window.__printCount`);
  check(beforePrint === 0 && afterPrint === 1,
    "点「打印 / 导出 PDF」真的调用 window.print", `计数 ${beforePrint}→${afterPrint}`);

  // 13. 设置页删除确认：点别名 × → 弹框说清后果 → 取消不掉数据（只读，不写库）
  await send("Page.navigate", { url: `${BASE}/settings` });
  await sleep(3500);
  const aliasButtons = await evaluate(`document.querySelectorAll('#aliases [data-alias]').length`);
  check(aliasButtons > 0, "设置页加载出别名删除按钮", `${aliasButtons} 个`);
  const aliasBefore = await evaluate(`
    Array.from(document.querySelectorAll('#aliases [data-alias]'))
      .map(function (b) { return b.getAttribute('aria-label'); })
  `);
  await evaluate(`document.querySelector('#aliases [data-alias]').click()`);
  await sleep(400);
  const dialog = await evaluate(`({
    open: document.getElementById('confirmDialog').open,
    title: document.getElementById('confirmTitle').textContent.trim(),
    text: document.getElementById('confirmText').textContent,
  })`);
  check(dialog.open === true && dialog.title === "删除别名", "点别名 × 弹出确认框", dialog.title);
  check(/历史提及不会改变/.test(dialog.text),
    "确认框说清后果（历史提及不变）", dialog.text.slice(0, 40));
  await evaluate(`
    (function () {
      var b = document.querySelector('#confirmDialog button[value=cancel]');
      if (b) b.click(); else document.getElementById('confirmDialog').close('cancel');
    })()
  `);
  await sleep(300);
  const aliasAfter = await evaluate(`
    Array.from(document.querySelectorAll('#aliases [data-alias]'))
      .map(function (b) { return b.getAttribute('aria-label'); })
  `);
  const dialogClosed = await evaluate(`!document.getElementById('confirmDialog').open`);
  check(JSON.stringify(aliasAfter) === JSON.stringify(aliasBefore) && dialogClosed === true,
    "取消后别名不变、弹框关闭（零写库）");

  check(errors.length === 0, "全程无 JS 异常", errors.join("; "));

  const failed = results.filter((x) => !x).length;
  console.log(failed === 0 ? "\nALL INTERACTION CHECKS PASSED" : `\n${failed} FAILED`);
  process.exitCode = failed === 0 ? 0 : 1;
} catch (e) {
  console.error("ERROR", e.message);
  process.exitCode = 1;
} finally {
  try { ws?.close(); } catch {}
  chrome.kill();
}
