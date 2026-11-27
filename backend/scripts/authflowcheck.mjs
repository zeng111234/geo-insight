// 真实用户路径 E2E：登录 → 工作室 → 点 AI 生成草稿 → 草稿落库载入编辑器。
// 此前的 aigenbuttoncheck 跑在免鉴权服务（GEO_AUTH_REQUIRED=0），真实用户要登录；
// 这里补上登录态这一环，验证 CSRF/session 链路下的 AI 按钮真能用。
// 用法：先 python backend/run.py（默认开鉴权），再 node backend/scripts/authflowcheck.mjs
// 换端口：GEO_BASE=http://127.0.0.1:8142 node backend/scripts/authflowcheck.mjs
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
const BASE = process.env.GEO_BASE || "http://127.0.0.1:8123";
const PORT = Number(process.env.GEO_CDP_PORT || 9437);
const CHROME = ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"].filter(existsSync)[0];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--no-sandbox", `--remote-debugging-port=${PORT}`, `--user-data-dir=${process.env.TEMP}/geo-authflow-cdp-${Date.now()}`, "about:blank"], { stdio: "ignore" });
async function target() { for (let i = 0; i < 60; i++) { try { const r = await fetch(`http://127.0.0.1:${PORT}/json/list`); const l = await r.json(); const p = l.find((t) => t.type === "page"); if (p) return p.webSocketDebuggerUrl; } catch {} await sleep(250); } throw Error("no cdp"); }
let id = 0; const pending = new Map(); let ws;
function send(m, p = {}) { const mid = ++id; ws.send(JSON.stringify({ id: mid, method: m, params: p })); return new Promise((res, rej) => pending.set(mid, { resolve: res, reject: rej })); }
async function ev(e) { const r = await send("Runtime.evaluate", { expression: e, returnByValue: true, awaitPromise: true }); if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 200)); return r.result.value; }
const results = [];
const check = (ok, label, extra = "") => { results.push(ok); console.log(`${ok ? "PASS" : "FAIL"}  ${label}${extra ? ` (${extra})` : ""}`); };
try {
  ws = new WebSocket(await target());
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { const p = pending.get(m.id); pending.delete(m.id); p.resolve(m.result); } };
  await send("Page.enable"); await send("Runtime.enable");

  // 未登录访问 studio 应被踢到登录页
  await send("Page.navigate", { url: BASE + "/studio" });
  await sleep(1800);
  const redirected = await ev(`location.pathname`);
  check(redirected === "/login", "未登录跳登录页", redirected);

  // 填表登录
  await ev(`(function(){
    document.querySelector('input[name=email]').value='demo@example.com';
    document.querySelector('input[name=password]').value='geo-demo-2026';
    document.querySelector('form').submit(); return true; })()`);
  await sleep(2500);
  const afterLogin = await ev(`location.pathname`);
  check(afterLogin !== "/login", "登录成功进系统", afterLogin);

  // 登录后进工作室，确认 AI 按钮在
  await send("Page.navigate", { url: BASE + "/studio" });
  await sleep(2500);
  const hasBtn = await ev(`!!document.getElementById('aiGenerate')`);
  check(hasBtn, "登录态能看到 AI 生成按钮", String(hasBtn));

  // 统计文章数（需带 session cookie，用浏览器 fetch 同源）
  const before = await ev(`(async function(){var r=await fetch('/v1/trackers/11111111-1111-1111-1111-111111111111/articles');var d=await r.json();return (d.items||[]).length})()`);

  await ev(`document.getElementById('newArticle').click()`);
  await sleep(1000);
  await ev(`(function(){var s=document.getElementById('newQuestion');if(s.options.length>1){s.selectedIndex=1}return true})()`);
  await ev(`document.getElementById('aiGenerate').click()`);

  let loaded = null;
  for (let i = 0; i < 24; i++) {
    await sleep(1500);
    const after = await ev(`(async function(){var r=await fetch('/v1/trackers/11111111-1111-1111-1111-111111111111/articles');var d=await r.json();return (d.items||[]).length})()`);
    loaded = await ev(`(function(){var p=document.getElementById('newPanel');return {hidden:p.style.display==='none',meta:(document.getElementById('draftMeta')||{}).textContent||''}})()`);
    if (after === before + 1 && loaded.hidden) break;
  }
  const after = await ev(`(async function(){var r=await fetch('/v1/trackers/11111111-1111-1111-1111-111111111111/articles');var d=await r.json();return (d.items||[]).length})()`);
  check(after === before + 1, "登录态 AI 生成后文章数+1", `${before}→${after}`);
  check(loaded && loaded.hidden, "草稿载入编辑器面板收起", JSON.stringify(loaded).slice(0, 80));

  const failed = results.filter((x) => !x).length;
  console.log(failed === 0 ? "\nALL AUTH FLOW CHECKS PASSED" : `\n${failed} FAILED`);
  process.exitCode = failed === 0 ? 0 : 1;
} catch (e) { console.error("ERROR", e.message); process.exitCode = 1; }
finally { try { ws?.close(); } catch {} chrome.kill(); }
