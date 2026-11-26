// 模型下拉「·网页」标注与筛选未破坏的 CDP 验收。
// 守两件事：① UI 诚实标注只有网页渠道；② 加标注没把 value/筛选搞坏。
// 用法：先起 GEO_PORT=8138 的新实例，再 GEO_BASE=http://127.0.0.1:8138 node modelhintcheck.mjs

import { spawn } from "node:child_process";
import { existsSync } from "node:fs";

const BASE = process.env.GEO_BASE || "http://127.0.0.1:8138";
const PORT = Number(process.env.GEO_CDP_PORT || 9362);
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
  `--user-data-dir=${process.env.TEMP || "/tmp"}/geo-modelhintcheck`,
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

  await send("Page.navigate", {
    url: `${BASE}/monitor?tab=answers&tracker=11111111-1111-1111-1111-111111111111&from=2026-11-01&to=2026-11-03`,
  });
  await sleep(3500);

  const ui = await evaluate(`({
    options: Array.from(document.querySelectorAll('#modelFilter option')).map(function (o) {
      return { value: o.value, text: o.textContent };
    }),
    hint: document.getElementById('modelChannelHint')?.textContent || '',
  })`);

  const real = ui.options.filter((o) => o.value);
  // 注意中间有点号+空格："doubao · 网页"。第一版写成 "·网页" 误报红——
  // 断言里的一个空格让功能明明对了却显示失败，比断言宽松更危险。
  check(real.length > 0 && real.every((o) => o.text.includes("· 网页")),
    "模型选项都标注了「·网页」", real.map((o) => o.text).join(" / "));
  check(real.every((o) => !/手机|mobile/.test(o.text)),
    "没有假装支持手机端");
  check(ui.hint.includes("未接入"), "标注了手机端未接入", ui.hint);

  // 筛选没被标注破坏：value 仍是纯 model_key，选中后 URL 带 model、列表刷新
  const before = await evaluate(`document.querySelectorAll('[data-answer]').length`);
  await evaluate(`(function () {
    var s = document.getElementById('modelFilter');
    s.value = 'deepseek';
    s.dispatchEvent(new Event('change', { bubbles: true }));
  })()`);
  await sleep(1800);
  const after = await evaluate(`({
    url: new URLSearchParams(location.search).get('model'),
    rows: document.querySelectorAll('[data-answer]').length,
    count: document.getElementById('ansMeta')?.textContent || '',
  })`);
  check(after.url === "deepseek", "筛选值仍是纯 model_key，写进 URL", `model=${after.url}`);
  check(after.rows > 0 && after.rows <= before, "筛选后列表仍正常", `${before}→${after.rows} ${after.count}`);
} catch (e) {
  console.error("[x] 验收异常：", e.message);
  results.push(false);
} finally {
  chrome.kill("SIGKILL");
}

const pass = results.filter(Boolean).length;
console.log(`\n模型标注: ${pass}/${results.length} 通过`);
process.exit(pass === results.length ? 0 : 1);
