// 人工干预跟进 E2E（SOP 第三步缺口）：采集健康页告警后能记「谁/何时/做了什么」。
// 存 tracker.settings.interventions，复用深合并，不加新表（决策 012）。
// 用法：先 python backend/run.py，再 node backend/scripts/interventioncheck.mjs
// 换端口：GEO_BASE=http://127.0.0.1:8152 node backend/scripts/interventioncheck.mjs
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
const BASE = process.env.GEO_BASE || "http://127.0.0.1:8123";
const PORT = Number(process.env.GEO_CDP_PORT || 9470);
const TID = "11111111-1111-1111-1111-111111111111";
const CHROME = ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"].filter(existsSync)[0];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--no-sandbox", `--remote-debugging-port=${PORT}`, `--user-data-dir=${process.env.TEMP}/geo-intervention-cdp-${Date.now()}`, "about:blank"], { stdio: "ignore" });
async function target() { for (let i = 0; i < 60; i++) { try { const r = await fetch(`http://127.0.0.1:${PORT}/json/list`); const l = await r.json(); const p = l.find((t) => t.type === "page"); if (p) return p.webSocketDebuggerUrl; } catch {} await sleep(250); } throw Error("no cdp"); }
let id = 0; const pending = new Map(); let ws;
function send(m, p = {}) { const mid = ++id; ws.send(JSON.stringify({ id: mid, method: m, params: p })); return new Promise((res, rej) => pending.set(mid, { resolve: res, reject: rej })); }
async function ev(e) { const r = await send("Runtime.evaluate", { expression: e, returnByValue: true, awaitPromise: true }); if (r.exceptionDetails) throw new Error("浏览器执行失败: " + String((r.exceptionDetails.exception?.description) || r.exceptionDetails.text).split("\n")[0].slice(0, 160)); return r.result.value; }
const results = [];
const check = (ok, label, extra = "") => { results.push(ok); console.log(`${ok ? "PASS" : "FAIL"}  ${label}${extra ? ` (${extra})` : ""}`); };
async function openHealth() {
  await send("Page.navigate", { url: BASE + "/monitor?tab=health" });
  await sleep(3000);
  if (await ev(`location.pathname`) === "/login") {
    await ev(`(function(){document.querySelector('input[name=email]').value='demo@example.com';
      document.querySelector('input[name=password]').value='geo-demo-2026';
      document.querySelector('form').submit();return true})()`);
    await sleep(2200);
    await send("Page.navigate", { url: BASE + "/monitor?tab=health" });
    await sleep(3000);
  }
}
try {
  ws = new WebSocket(await target());
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { const p = pending.get(m.id); pending.delete(m.id); p.resolve(m.result); } };
  await send("Page.enable"); await send("Runtime.enable");
  const errors = [];
  ws.addEventListener("message", (e) => { const m = JSON.parse(e.data); if (m.method === "Runtime.exceptionThrown") errors.push(m.params.exceptionDetails.text || "exception"); });

  await openHealth();
  const panel = await ev(`!!document.getElementById('intAdd')`);
  check(panel, "采集健康页有干预记录面板", String(panel));

  // 先清空已有记录（避免残留），用 API 直接写空数组
  await ev(`(async function(){var csrf=(document.cookie.match(/(?:^|;\\s*)geo_csrf=([^;]+)/)||[])[1]||'';
    await fetch('/v1/trackers/${TID}',{method:'PATCH',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},
      body:JSON.stringify({settings:{interventions:[]}})});return true})()`);
  await openHealth();
  const emptyCount = await ev(`document.querySelectorAll('#intList .kv').length`);
  check(emptyCount === 0, "起点已清空", String(emptyCount));

  // 添加一条唯一记录
  const tag = "I" + Date.now().toString().slice(-6);
  await ev(`(function(){document.getElementById('intType').value='官媒投喂';
    document.getElementById('intNote').value='${tag}补投3篇';
    document.getElementById('intActor').value='${tag}运营';
    document.getElementById('intAdd').click();return true})()`);
  await sleep(1800);
  const saved = await ev(`document.getElementById('intState').textContent`);
  check(/已记录/.test(saved), "添加成功提示", saved);
  const rowText = await ev(`document.getElementById('intList').innerText`);
  check(rowText.includes(tag + "补投3篇") && /官媒投喂/.test(rowText), "记录显示类型+说明", rowText.slice(0, 70));

  // 刷新后仍在（真落库）
  await openHealth();
  const afterReload = await ev(`document.getElementById('intList').innerText`);
  check(afterReload.includes(tag + "补投3篇"), "刷新后记录仍在（真落库）", afterReload.slice(0, 70));

  // API 回读结构
  const apiVal = await ev(`(async function(){var d=await(await fetch('/v1/trackers/${TID}')).json();return (d.settings.interventions||[])[0]||{}})()`);
  check(apiVal.type === "官媒投喂" && apiVal.note === tag + "补投3篇" && apiVal.actor === tag + "运营" && apiVal.at,
    "API 回读 interventions 结构", JSON.stringify(apiVal).slice(0, 90));

  // 删除后消失
  await ev(`(function(){var b=document.querySelector('#intList [data-int-del]');if(b){b.click();return true}return false})()`);
  await sleep(1500);
  await openHealth();
  const afterDel = await ev(`document.querySelectorAll('#intList .kv').length`);
  check(afterDel === 0, "删除后记录消失", String(afterDel));
  check(errors.length === 0, "全程无 JS 异常", errors.join("; ").slice(0, 100));

  const failed = results.filter((x) => !x).length;
  console.log(failed === 0 ? "\nALL INTERVENTION CHECKS PASSED" : `\n${failed} FAILED`);
  process.exitCode = failed === 0 ? 0 : 1;
} catch (e) { console.error("ERROR", e.message); process.exitCode = 1; }
finally { try { ws?.close(); } catch {} chrome.kill(); }
