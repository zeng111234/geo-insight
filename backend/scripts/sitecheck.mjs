// 信源落地页 E2E：无登录公开打开 → 校验真实登记的品牌/电话/官网渲染出来，
// 缺的字段显示「待补充」，不写死假数据。跑在隔离实例（临时库带真实 settings）。
// 用法：GEO_BASE=http://127.0.0.1:8148 GEO_TRACKER=<id> node sitecheck.mjs
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
const BASE = process.env.GEO_BASE || "http://127.0.0.1:8148";
const TRACKER = process.env.GEO_TRACKER || "d3aae9ce-cb61-468a-9ab2-51d2f4d28bf1";
const CHROME = ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"].filter(existsSync)[0];
const PORT = Number(process.env.GEO_CDP_PORT || 9457);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--no-sandbox", `--remote-debugging-port=${PORT}`, `--user-data-dir=${process.env.TEMP}/geo-site-cdp-${Date.now()}`, "about:blank"], { stdio: "ignore" });
let id = 0; const pending = new Map(); let ws;
function send(m, p = {}) { const mid = ++id; ws.send(JSON.stringify({ id: mid, method: m, params: p })); return new Promise((res, rej) => pending.set(mid, { resolve: res, reject: rej })); }
async function ev(e) { const r = await send("Runtime.evaluate", { expression: e, returnByValue: true, awaitPromise: true }); if (r.exceptionDetails) throw new Error((r.exceptionDetails.exception?.description || r.exceptionDetails.text || "?").split("\n")[0]); return r.result.value; }
const results = [];
const check = (ok, label, extra = "") => { results.push(ok); console.log(`${ok ? "PASS" : "FAIL"}  ${label}${extra ? ` (${extra})` : ""}`); };
try {
  let target;
  for (let i = 0; i < 60; i++) { try { const l = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json(); target = l.find((t) => t.type === "page"); if (target) break; } catch {} await sleep(250); }
  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { const p = pending.get(m.id); pending.delete(m.id); p.resolve(m.result); } };
  await send("Page.enable"); await send("Runtime.enable");
  await send("Page.navigate", { url: `${BASE}/site/${TRACKER}` });
  await sleep(2500);
  const path = await ev("location.pathname");
  check(path.startsWith("/site/"), "落地页公开打开（未跳登录）", path);
  const body = await ev("document.getElementById('card').innerText");
  check(!/加载中/.test(body), "数据已渲染", body.slice(0, 40));
  check(/020-88886666/.test(body), "真实登记的电话已展示", "020-88886666");
  check(/huice\.example\.com/.test(body), "真实登记的官网已展示", "huice.example.com");
  check(/惠策/.test(body), "真实品牌名已展示");
  check(/待补充/.test(body), "未登记字段标「待补充」而非空白");
  const passed = results.filter(Boolean).length;
  console.log(`\n${passed}/${results.length} checks passed`);
  if (passed !== results.length) process.exitCode = 1;
} catch (e) {
  console.error("E2E 异常:", e.message);
  process.exitCode = 1;
} finally {
  chrome.kill("SIGKILL");
}
