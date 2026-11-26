// 发布页回链诚实性 E2E：真实回链可点，种子 demo 假链标「演示回链（非真实发布）」。
// 隔壁在 tasks.html 做了 isValidUrl 排除 demo 假链，publish.html 曾漏掉同样处理——
// 4 条 published 全是 demo-article 假 URL，却渲染成可点「回链 →」，像是真发布过。
// 用法：先 python backend/run.py，再 node backend/scripts/publinkcheck.mjs
// 换端口：GEO_BASE=http://127.0.0.1:8134 node backend/scripts/publinkcheck.mjs
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
const BASE = process.env.GEO_BASE || "http://127.0.0.1:8123";
const PORT = Number(process.env.GEO_CDP_PORT || 9415);
const CHROME = ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"].filter(existsSync)[0];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--no-sandbox",
  `--remote-debugging-port=${PORT}`, `--user-data-dir=${process.env.TEMP}/geo-publink-cdp-${Date.now()}`, "about:blank"], { stdio: "ignore" });
async function target() {
  for (let i = 0; i < 60; i++) {
    try { const r = await fetch(`http://127.0.0.1:${PORT}/json/list`); const l = await r.json();
      const p = l.find((t) => t.type === "page"); if (p) return p.webSocketDebuggerUrl; } catch {}
    await sleep(250);
  }
  throw new Error("no CDP");
}
let id = 0; const pending = new Map(); let ws;
function send(method, params = {}) { const mid = ++id; ws.send(JSON.stringify({ id: mid, method, params }));
  return new Promise((res, rej) => pending.set(mid, { resolve: res, reject: rej })); }
async function evaluate(expr) { const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails)); return r.result.value; }
const results = [];
const check = (ok, label, extra = "") => { results.push(ok); console.log(`${ok ? "PASS" : "FAIL"}  ${label}${extra ? ` (${extra})` : ""}`); };
try {
  ws = new WebSocket(await target());
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) {
    const p = pending.get(m.id); pending.delete(m.id); m.error ? p.reject(new Error(m.error.message)) : p.resolve(m.result); } };
  await send("Page.enable"); await send("Runtime.enable");
  const errors = [];
  ws.addEventListener("message", (e) => { const m = JSON.parse(e.data);
    if (m.method === "Runtime.exceptionThrown") errors.push(m.params.exceptionDetails.text || "exception"); });

  await send("Page.navigate", { url: BASE + "/publish" });
  await sleep(3000);

  const rows = await evaluate(`Array.from(document.querySelectorAll('.pub-row')).map(function(r){
    var a = r.querySelector('a.plain[href]');
    return { text: r.innerText.replace(/\\n/g,' '), href: a ? a.getAttribute('href') : null };
  })`);
  check(rows.length > 0, "发布记录渲染出来", String(rows.length));

  // 假链绝不能渲染成可点链接
  const fakeClickable = rows.filter((r) => r.href && /demo-article/i.test(r.href));
  check(fakeClickable.length === 0, "demo 假链不可点", String(fakeClickable.length));
  const honestMarks = rows.filter((r) => r.text.includes("演示回链"));
  check(honestMarks.length >= 3, "假链标为演示回链（非真实发布）", String(honestMarks.length));

  // 真实回链仍然可点
  const realLink = rows.find((r) => r.href && /real-article/.test(r.href));
  check(!!realLink, "真实回链可点", realLink ? realLink.href : "none");

  check(errors.length === 0, "全程无 JS 异常", errors.join("; ").slice(0, 120));
  const failed = results.filter((x) => !x).length;
  console.log(failed === 0 ? "\nALL PUB LINK CHECKS PASSED" : `\n${failed} FAILED`);
  process.exitCode = failed === 0 ? 0 : 1;
} catch (e) { console.error("ERROR", e.message); process.exitCode = 1; }
finally { try { ws?.close(); } catch {} chrome.kill(); }
