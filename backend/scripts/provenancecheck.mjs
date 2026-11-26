// 数据来源标识的真浏览器验收：横幅 + 每条回答的来源标签。
//
// 用户反馈"网站真真假假分不清"——1510 条演示数据和 6 条真实采集混在看板里。
// 本脚本用 CDP 真打开页面，验证：
// 1. 对象头横幅正确显示该对象的数据性质（演示对象=演示数据、试点对象=真实采集）；
// 2. 问答明细每条回答带来源标签，且演示数据显示"演示数据"；
// 3. 真实试点对象的明细显示"真实采集"。
//
// 用 8123（当前服务），只读页面不改数据。

import { spawn } from "node:child_process";
import { existsSync } from "node:fs";

const BASE = process.env.GEO_BASE || "http://127.0.0.1:8123";
const PORT = Number(process.env.GEO_CDP_PORT || 9358);
const CANDIDATES = [
  process.env.GEO_CHROME,
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "/usr/bin/google-chrome", "/usr/bin/chromium",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
].filter(Boolean);
const CHROME = CANDIDATES.find(existsSync);
if (!CHROME) { console.error("没有找到 Chrome/Edge，用 GEO_CHROME 指定"); process.exit(1); }

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const chrome = spawn(CHROME, [
  "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
  `--remote-debugging-port=${PORT}`, "--window-size=1680,1000",
  `--user-data-dir=${process.env.TEMP || "/tmp"}/geo-provenancecheck`, "about:blank",
], { stdio: "ignore" });

let ws, id = 0;
const pending = new Map();
function send(method, params = {}) {
  const mid = ++id;
  ws.send(JSON.stringify({ id: mid, method, params }));
  return new Promise((resolve, reject) => pending.set(mid, { resolve, reject }));
}
async function evaluate(expr) {
  const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
  return r.result.value;
}
const results = [];
const check = (ok, label, extra = "") => {
  results.push(ok);
  console.log(`${ok ? "PASS" : "FAIL"}  ${label}${extra ? ` (${extra})` : ""}`);
};

try {
  let page;
  for (let i = 0; i < 60; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
      page = list.find((t) => t.type === "page");
      if (page) break;
    } catch { /* 浏览器还在起 */ }
    await sleep(250);
  }
  if (!page) throw new Error("拿不到 CDP 页面");
  ws = new WebSocket(page.webSocketDebuggerUrl);
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

  // 1. 演示对象：横幅应显示"演示数据"性质
  await send("Page.navigate", { url: `${BASE}/monitor?tracker=11111111-1111-1111-1111-111111111111&tab=answers` });
  await sleep(4000);
  const banner1 = await evaluate(`(function () {
    var el = document.getElementById('sourceBanner');
    return el && el.offsetParent !== null ? el.textContent : '';
  })()`);
  check(banner1.includes("演示数据") && banner1.includes("不是真实采集"),
    "演示对象横幅明确显示'演示数据/不是真实采集'", banner1.slice(0, 70));

  // 2. 演示对象明细：每条带来源标签
  const tags1 = await evaluate(`(function () {
    return Array.from(document.querySelectorAll('.tag.source-demo')).map(function (e) { return e.textContent.trim(); });
  })()`);
  check(tags1.length > 0 && tags1.every((t) => t === "演示数据"),
    "演示明细每条带'演示数据'来源标签", `${tags1.length} 条标签`);

  // 3. 真实试点对象：横幅显示真实采集、明细带真实标签
  await send("Page.navigate", { url: `${BASE}/monitor?tracker=78212285-75c2-4066-b4fc-f2d77288a7dd&tab=answers` });
  await sleep(4000);
  const banner2 = await evaluate(`(function () {
    var el = document.getElementById('sourceBanner');
    return el && el.offsetParent !== null ? el.textContent : '';
  })()`);
  check(banner2.includes("真实采集") && banner2.includes("均为真实平台采集"),
    "真实对象横幅明确显示'真实采集'", banner2.slice(0, 70));

  const tags2 = await evaluate(`(function () {
    return Array.from(document.querySelectorAll('.tag.source-real')).map(function (e) { return e.textContent.trim(); });
  })()`);
  check(tags2.length > 0 && tags2.every((t) => t === "真实采集"),
    "真实明细每条带'真实采集'来源标签", `${tags2.length} 条标签`);

  // 4. 反向守门：真实对象页面不该出现"演示数据"标签（不能混淆）
  const demoOnReal = await evaluate(`document.querySelectorAll('.tag.source-demo').length`);
  check(demoOnReal === 0, "真实对象明细没有混入演示数据标签", String(demoOnReal));

} catch (e) {
  console.error("FAIL  脚本异常：", e.message);
  results.push(false);
} finally {
  if (ws) try { ws.close(); } catch { /* 已断 */ }
  try { chrome.kill("SIGKILL"); } catch { /* 已退 */ }
}

const pass = results.filter(Boolean).length;
console.log(`\nprovenancecheck: ${pass}/${results.length} 通过`);
process.exitCode = pass === results.length ? 0 : 1;
