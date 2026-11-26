// P7 监控对象目录的 CDP 真点检查：19 对象→搜索可筛→切到空对象显示诚实空态。
// browsercheck 只验静态 DOM，这里真点搜索与对象切换，守住「左栏充实且可筛」。
// 用法：先 python backend/run.py，再 node backend/scripts/p7check.mjs
// 换端口：GEO_BASE=http://127.0.0.1:8128 node backend/scripts/p7check.mjs
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
const BASE = process.env.GEO_BASE || "http://127.0.0.1:8123";
const PORT = Number(process.env.GEO_CDP_PORT || 9393);
const CHROME = ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"].filter(existsSync)[0];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--no-sandbox",
  `--remote-debugging-port=${PORT}`, `--user-data-dir=${process.env.TEMP}/geo-p7-cdp`, "about:blank"], { stdio: "ignore" });
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
  await send("Page.navigate", { url: `${BASE}/monitor` }); await sleep(3500);

  const opts = await evaluate(`document.querySelectorAll('#tracker option').length`);
  check(opts === 19, "左栏下拉 19 个对象", String(opts));

  // 搜索：输入「启明」，列表应只剩含启明的项
  await evaluate(`(function(){ var s=document.getElementById('listSearch'); s.value='启明';
    s.dispatchEvent(new Event('input',{bubbles:true})); })()`);
  await sleep(500);
  const listText = await evaluate(`document.getElementById('trackerList').innerText`);
  check(listText.includes("启明") && !listText.includes("华辰"), "搜索只留匹配对象",
    listText.replace(/\n/g, " ").slice(0, 60));

  // 清空搜索，切到新对象，验空态
  await evaluate(`(function(){ var s=document.getElementById('listSearch'); s.value='';
    s.dispatchEvent(new Event('input',{bubbles:true})); })()`);
  await sleep(400);
  const switched = await evaluate(`(function(){
    var el=Array.from(document.querySelectorAll('#trackerList [data-tracker]')).find(function(x){
      return x.textContent.indexOf('启明')>=0; });
    if(!el) return 'no-item';
    el.click(); return el.dataset.tracker; })()`);
  await sleep(2000);
  const objText = await evaluate(`document.getElementById('objName').textContent`);
  check(objText.includes("启明"), "切到新对象", objText);
  const metaText = await evaluate(`(document.getElementById('objMeta')||{}).textContent || ''`);
  check(!/NaN|undefined/.test(metaText), "新对象无 NaN/undefined", metaText.replace(/\n/g, " ").slice(0, 60));

  check(errors.length === 0, "全程无 JS 异常", errors.join("; "));
  const failed = results.filter((x) => !x).length;
  console.log(failed === 0 ? "\nALL P7 E2E CHECKS PASSED" : `\n${failed} FAILED`);
  process.exitCode = failed === 0 ? 0 : 1;
} catch (e) { console.error("ERROR", e.message); process.exitCode = 1; }
finally { try { ws?.close(); } catch {} chrome.kill(); }
