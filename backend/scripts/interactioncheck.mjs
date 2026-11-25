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

  await send("Page.navigate", { url: `${BASE}/monitor` });
  await sleep(3500);

  // 1. 对象头按钮齐全且顺序与参考系统一致
  const labels = await evaluate(`
    Array.from(document.querySelectorAll('.mon-head .acts > *')).map(e => e.textContent.trim())
  `);
  check(
    JSON.stringify(labels) === JSON.stringify(["下载报告", "导出", "刷新", "暂停", "删除", "编辑"]),
    "对象头 6 个按钮，顺序同参考系统", labels.join(" | ")
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
    ['ntName','ntBrand','ntIndustry','ntRegion','ntAliases','ntCompetitors']
      .map(function (i) { return !!document.getElementById(i); })
  `);
  check(fields.every(Boolean), "抽屉字段齐全（名称/品牌/行业/地区/别名/竞品）", fields.join(","));

  // 8. 取消关闭抽屉，不新建对象
  await evaluate(`document.getElementById('ntCancel').click()`);
  await sleep(200);
  check(await evaluate(`!document.getElementById('newDrawer').open`) === true, "取消关闭抽屉");

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
