// 台账↔资料归档 UX 闭环 E2E：台账面板不再手填「照片说明」，而是显示真实归档进度。
// 修的是 UX 断裂：此前台账 t1Photos 手填框假装登记照片，实际照片传在 /files，
// 两处脱节。现在台账显示 /trackers/{id}/assets 的 checklist 真数据 + 引导链接。
// 用法：先 python backend/run.py（默认鉴权），再 node backend/scripts/ledgerassetcheck.mjs
// 换端口：GEO_BASE=http://127.0.0.1:8151 node backend/scripts/ledgerassetcheck.mjs
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
const BASE = process.env.GEO_BASE || "http://127.0.0.1:8123";
const PORT = Number(process.env.GEO_CDP_PORT || 9463);
const TID = "11111111-1111-1111-1111-111111111111";
const CHROME = ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"].filter(existsSync)[0];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--no-sandbox", `--remote-debugging-port=${PORT}`, `--user-data-dir=${process.env.TEMP}/geo-ledgerasset-cdp-${Date.now()}`, "about:blank"], { stdio: "ignore" });
async function target() { for (let i = 0; i < 60; i++) { try { const r = await fetch(`http://127.0.0.1:${PORT}/json/list`); const l = await r.json(); const p = l.find((t) => t.type === "page"); if (p) return p.webSocketDebuggerUrl; } catch {} await sleep(250); } throw Error("no cdp"); }
let id = 0; const pending = new Map(); let ws;
function send(m, p = {}) { const mid = ++id; ws.send(JSON.stringify({ id: mid, method: m, params: p })); return new Promise((res, rej) => pending.set(mid, { resolve: res, reject: rej })); }
async function ev(e) { const r = await send("Runtime.evaluate", { expression: e, returnByValue: true, awaitPromise: true }); if (r.exceptionDetails) throw new Error("浏览器执行失败: " + String((r.exceptionDetails.exception?.description) || r.exceptionDetails.text).split("\n")[0].slice(0, 160)); return r.result.value; }
const results = [];
const check = (ok, label, extra = "") => { results.push(ok); console.log(`${ok ? "PASS" : "FAIL"}  ${label}${extra ? ` (${extra})` : ""}`); };
try {
  ws = new WebSocket(await target());
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { const p = pending.get(m.id); pending.delete(m.id); p.resolve(m.result); } };
  await send("Page.enable"); await send("Runtime.enable");
  const errors = [];
  ws.addEventListener("message", (e) => { const m = JSON.parse(e.data); if (m.method === "Runtime.exceptionThrown") errors.push(m.params.exceptionDetails.text || "exception"); });

  await send("Page.navigate", { url: BASE + "/settings" });
  await sleep(2500);
  if (await ev(`location.pathname`) === "/login") {
    await ev(`(function(){document.querySelector('input[name=email]').value='demo@example.com';
      document.querySelector('input[name=password]').value='geo-demo-2026';
      document.querySelector('form').submit();return true})()`);
    await sleep(2200);
    await send("Page.navigate", { url: BASE + "/settings" });
    await sleep(2500);
  }
  await ev(`(function(){var s=document.getElementById('tracker');
    var o=Array.from(s.options).find(x=>x.value==='${TID}');
    if(o){s.value='${TID}';s.dispatchEvent(new Event('change'));return true}return false})()`);
  await sleep(2500);

  // 归档状态块：不再显示"加载中"
  const status = await ev(`document.getElementById('t1AssetStatus').textContent`);
  check(!/加载中/.test(status), "归档状态已渲染（非加载中）", status.slice(0, 70));
  check(/归档|上传|形象照/.test(status), "状态块显示真实归档信息", status.slice(0, 70));

  // 引导链接指向 /files
  const link = await ev(`(function(){var a=Array.from(document.querySelectorAll('#ledgerPanel a')).find(x=>x.getAttribute('href')==='/files');return a?a.textContent:''})()`);
  check(/客户资料归档/.test(link), "引导链接指向资料归档页", link);

  // 照片字段不再是"记路径/说明"的误导文案
  const label = await ev(`(function(){var ta=document.getElementById('t1Photos');
    var lab=ta.closest('label');return lab?lab.textContent:''})()`);
  check(!/文件放本地|这里记路径/.test(label), "旧误导文案已去除", label.slice(0, 60));
  check(/可选|不计入张数/.test(label), "新文案标明是补充说明", label.slice(0, 60));

  // 数据打通：上传前读 checklist.total，上传一张真图后再读，应 +1
  const before = await ev(`(async function(){var d=await(await fetch('/v1/trackers/${TID}/assets')).json();return d.checklist.total})()`);
  const tag = "L" + Date.now().toString().slice(-6);
  // 构造最小 PNG（1x1）用浏览器 fetch multipart 上传
  const uploaded = await ev(`(async function(){
    var csrf=(document.cookie.match(/(?:^|;\\s*)geo_csrf=([^;]+)/)||[])[1]||'';
    var png=Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=='),c=>c.charCodeAt(0));
    var fd=new FormData(); fd.append('category','portrait');
    fd.append('file',new Blob([png],{type:'image/png'}),'${tag}.png');
    var r=await fetch('/v1/trackers/${TID}/assets',{method:'POST',headers:{'X-CSRF-Token':csrf},body:fd});
    return r.status; })()`);
  check(uploaded === 201 || uploaded === 200, "上传一张真照片", String(uploaded));
  const after = await ev(`(async function(){var d=await(await fetch('/v1/trackers/${TID}/assets')).json();return d.checklist.total})()`);
  check(after === before + 1, "归档张数+1（真打通）", `${before}→${after}`);

  // 刷新 settings，台账面板状态块应反映新张数
  await send("Page.navigate", { url: BASE + "/settings" });
  await sleep(2500);
  await ev(`(function(){var s=document.getElementById('tracker');
    var o=Array.from(s.options).find(x=>x.value==='${TID}');
    if(o){s.value='${TID}';s.dispatchEvent(new Event('change'))}})()`);
  await sleep(2500);
  const refreshed = await ev(`document.getElementById('t1AssetStatus').textContent`);
  check(new RegExp("已归档.*" + after).test(refreshed) || refreshed.includes(String(after)), "刷新后台账显示新归档数", refreshed.slice(0, 70));
  check(errors.length === 0, "全程无 JS 异常", errors.join("; ").slice(0, 100));

  const failed = results.filter((x) => !x).length;
  console.log(failed === 0 ? "\nALL LEDGER-ASSET CHECKS PASSED" : `\n${failed} FAILED`);
  process.exitCode = failed === 0 ? 0 : 1;
} catch (e) { console.error("ERROR", e.message); process.exitCode = 1; }
finally { try { ws?.close(); } catch {} chrome.kill(); }
