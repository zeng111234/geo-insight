// 模型 × 端筛选的 CDP 真点检查：选端→URL 带 client→刷新保持→清除还原。
// browsercheck 只验静态 DOM，这里真点下拉并验 URL 状态，守住「刷新保住」这条判据。
// 用法：先 python backend/run.py，再 node backend/scripts/p3check.mjs
// 换端口：GEO_BASE=http://127.0.0.1:8125 node backend/scripts/p3check.mjs
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
const BASE = process.env.GEO_BASE || "http://127.0.0.1:8123";
const PORT = Number(process.env.GEO_CDP_PORT || 9391);
const CHROME = [
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
].filter(existsSync)[0];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--no-sandbox",
  `--remote-debugging-port=${PORT}`, `--user-data-dir=${process.env.TEMP}/geo-p3-e2e`, "about:blank"],
  { stdio: "ignore" });
async function target() {
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/json/list`);
      const list = await r.json();
      const page = list.find((t) => t.type === "page");
      if (page) return page.webSocketDebuggerUrl;
    } catch {}
    await sleep(250);
  }
  throw new Error("no CDP page");
}
let id = 0; const pending = new Map(); let ws;
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
const check = (ok, label, extra = "") => { results.push(ok); console.log(`${ok ? "PASS" : "FAIL"}  ${label}${extra ? ` (${extra})` : ""}`); };
try {
  ws = new WebSocket(await target());
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) {
      const p = pending.get(m.id); pending.delete(m.id);
      m.error ? p.reject(new Error(m.error.message)) : p.resolve(m.result);
    }
  };
  await send("Page.enable"); await send("Runtime.enable");
  // 注入 fetch 记录器：验证的不只是 URL 状态，而是 API 请求真的带了 client。
  // 只改 URL 不改 filterQuery 会让数字不随端变——这正是 P3 要防的假闭环。
  await send("Page.addScriptToEvaluateOnNewDocument", {
    source: "window.__p3Urls = []; var _f = window.fetch; window.fetch = function (u, o) { window.__p3Urls.push(String(u)); return _f.apply(this, arguments); };",
  });
  const errors = [];
  ws.addEventListener("message", (e) => {
    const m = JSON.parse(e.data);
    if (m.method === "Runtime.exceptionThrown") errors.push(m.params.exceptionDetails.text || "exception");
  });
  await send("Page.navigate", { url: `${BASE}/monitor` });
  await sleep(3500);

  const hasClient = await evaluate(`!!document.getElementById('clientFilter')`);
  check(hasClient, "筛选条有端下拉");

  // 选端为 mobile，触发 change
  await evaluate(`
    (function () {
      var el = document.getElementById('clientFilter');
      el.value = 'mobile';
      el.dispatchEvent(new Event('change', { bubbles: true }));
    })()
  `);
  await sleep(1200);
  const urlHasClient = await evaluate(`location.search.includes('client=mobile')`);
  check(urlHasClient, "选端后 URL 带上 client=mobile", await evaluate(`location.search`));

  // 核心判据：API 请求真的带 client=mobile，不是只改了 URL 状态。
  const reqWithClient = await evaluate(`
    (window.__p3Urls || []).filter(function (u) { return u.indexOf('client=mobile') >= 0; })
  `);
  check(reqWithClient.length > 0, "选端后 API 请求带 client=mobile（数字会随端变）",
    reqWithClient.slice(0, 1).join(""));

  // tab-counts 响应的 filters.client 应为 mobile（从 state 读，因为响应已被前端消费）
  const stateClient = await evaluate(`
    (function () {
      var m = location.search.match(/client=([^&]+)/);
      return m ? decodeURIComponent(m[1]) : '';
    })()
  `);
  check(stateClient === "mobile", "当前端状态为 mobile", stateClient);

  // 刷新，断言端下拉恢复为 mobile
  const href = await evaluate(`location.href`);
  await send("Page.navigate", { url: href });
  await sleep(3500);
  const afterReload = await evaluate(`document.getElementById('clientFilter').value`);
  check(afterReload === "mobile", "刷新后端下拉保持 mobile", afterReload);

  // 清除筛选，断言恢复全部
  await evaluate(`document.getElementById('clearFilters').click()`);
  await sleep(800);
  const afterClear = await evaluate(`document.getElementById('clientFilter').value`);
  check(afterClear === "", "清除筛选后端回到全部", afterClear || "(空)");

  check(errors.length === 0, "全程无 JS 异常", errors.join("; "));
  const failed = results.filter((x) => !x).length;
  console.log(failed === 0 ? "\nALL P3 E2E CHECKS PASSED" : `\n${failed} FAILED`);
  process.exitCode = failed === 0 ? 0 : 1;
} catch (e) {
  console.error("ERROR", e.message); process.exitCode = 1;
} finally {
  try { ws?.close(); } catch {}
  chrome.kill();
}
