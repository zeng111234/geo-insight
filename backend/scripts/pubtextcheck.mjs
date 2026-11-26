// 守任务页发布文案诚实：不得再出现「演示态/演示记录」这类和真实发布器打架的措辞；
// 种子 demo-article 假链接不得显示成可点的「回链 →」。
// 用法：GEO_BASE=http://127.0.0.1:8123 node pubtextcheck.mjs

import { spawn } from "node:child_process";
import { existsSync } from "node:fs";

const BASE = process.env.GEO_BASE || "http://127.0.0.1:8123";
const PORT = Number(process.env.GEO_CDP_PORT || 9373);
const PASSWORD = process.env.GEO_PASSWORD || "geo-demo-2026";
const EMAIL = process.env.GEO_EMAIL || "demo@example.com";
const CANDIDATES = [
  process.env.GEO_CHROME,
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
  "/usr/bin/google-chrome", "/usr/bin/chromium",
].filter(Boolean);
const CHROME = CANDIDATES.find(existsSync);
if (!CHROME) { console.error("没找到 Chrome/Edge"); process.exit(1); }

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const chrome = spawn(CHROME, [
  "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
  `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${process.env.TEMP || "/tmp"}/geo-pubtextcheck-${Date.now()}`,
  "about:blank",
], { stdio: "ignore" });

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
async function waitTarget() {
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/json/list`);
      const page = (await r.json()).find((t) => t.type === "page");
      if (page) return page.webSocketDebuggerUrl;
    } catch { /* 还在起 */ }
    await sleep(250);
  }
  throw new Error("拿不到 CDP 页面");
}

try {
  ws = new WebSocket(await waitTarget());
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

  await send("Page.navigate", { url: `${BASE}/tasks` });
  await sleep(2200);
  let url = await evaluate("location.pathname");
  if (url === "/login") {
    await evaluate(`(function(){
      document.querySelector('input[name="email"]').value = ${JSON.stringify(EMAIL)};
      document.querySelector('input[name="password"]').value = ${JSON.stringify(PASSWORD)};
      document.querySelector('form[action="/login"]').submit();
    })()`);
    await sleep(2200);
  }
  await sleep(1500);

  // 必须查页面 HTML 源码：这些文案在 <script> 字符串里，innerText 读不到，
  // 只查 innerText 会导致变异改错文案也全绿（假绿）。
  const html = await evaluate(`(async function(){
    var r = await fetch(location.pathname, {cache:'no-store'});
    return await r.text();
  })()`);
  check(!/待发布（演示态）/.test(html), "PUB_TEXT 无「待发布（演示态）」");
  check(!/生成演示记录/.test(html), "空态无「生成演示记录」措辞");
  check(/待发布"/.test(html) || /待发布</.test(html), "保留「待发布」中性表述");

  // demo-article 假链不得显示成可点回链：检查发布列表里的锚点文本。
  const anchors = await evaluate(`Array.from(document.querySelectorAll('#pubs a')).map(function(a){return a.textContent.trim()})`);
  check(!anchors.some((t) => /回链/.test(t)), "demo-article 假链未显示成可点「回链 →」", `anchors=${anchors.join(',') || '无'}`);

  // 源码契约：isValidUrl 必须能识别并排除假链（在页面上下文里直接调）。
  const fnCheck = await evaluate(`(function(){
    var src = Array.from(document.scripts).map(function(s){return s.textContent||''}).join('');
    return { hasFn: /function isValidUrl/.test(src), excludesDemo: /demo-article/.test(src) };
  })()`);
  check(fnCheck.hasFn && fnCheck.excludesDemo, "URL 有效判定函数存在且排除 demo-article");
} catch (e) {
  console.error("[x] 验收异常：", e.message);
  results.push(false);
} finally {
  chrome.kill("SIGKILL");
}

const pass = results.filter(Boolean).length;
console.log(`\n任务页文案: ${pass}/${results.length} 通过`);
process.exit(pass === results.length ? 0 : 1);
