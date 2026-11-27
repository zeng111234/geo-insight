// 服务台账与资料归档 E2E：填表一/表二/表三 → 保存 → 刷新后仍在（真落库）。
// 数据存 tracker.settings，深合并不丢 website。轻量内部版（决策 012）。
// 用法：先 python backend/run.py，再 node backend/scripts/ledgercheck.mjs
// 换端口：GEO_BASE=http://127.0.0.1:8146 node backend/scripts/ledgercheck.mjs
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
const BASE = process.env.GEO_BASE || "http://127.0.0.1:8123";
const PORT = Number(process.env.GEO_CDP_PORT || 9450);
const TID = "11111111-1111-1111-1111-111111111111";
const CHROME = ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"].filter(existsSync)[0];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--no-sandbox", `--remote-debugging-port=${PORT}`, `--user-data-dir=${process.env.TEMP}/geo-ledger-cdp-${Date.now()}`, "about:blank"], { stdio: "ignore" });
async function target() { for (let i = 0; i < 60; i++) { try { const r = await fetch(`http://127.0.0.1:${PORT}/json/list`); const l = await r.json(); const p = l.find((t) => t.type === "page"); if (p) return p.webSocketDebuggerUrl; } catch {} await sleep(250); } throw Error("no cdp"); }
let id = 0; const pending = new Map(); let ws;
function send(m, p = {}) { const mid = ++id; ws.send(JSON.stringify({ id: mid, method: m, params: p })); return new Promise((res, rej) => pending.set(mid, { resolve: res, reject: rej })); }
async function ev(e) { const r = await send("Runtime.evaluate", { expression: e, returnByValue: true, awaitPromise: true }); if (r.exceptionDetails) { const d = r.exceptionDetails; throw new Error("浏览器执行失败: " + String((d.exception?.description) || d.text).split("\n")[0].slice(0, 160)); } return r.result.value; }
const results = [];
const check = (ok, label, extra = "") => { results.push(ok); console.log(`${ok ? "PASS" : "FAIL"}  ${label}${extra ? ` (${extra})` : ""}`); };
async function loginAndOpen() {
  await send("Page.navigate", { url: BASE + "/settings" });
  await sleep(2500);
  if (await ev(`location.pathname`) === "/login") {
    await ev(`(function(){document.querySelector('input[name=email]').value='demo@example.com';
      document.querySelector('input[name=password]').value='geo-demo-2026';
      document.querySelector('form').submit(); return true;})()`);
    await sleep(2200);
    await send("Page.navigate", { url: BASE + "/settings" });
    await sleep(2500);
  }
  // 选 demo tracker
  await ev(`(function(){var s=document.getElementById('tracker');
    var o=Array.from(s.options).find(function(x){return x.value==='${TID}'});
    if(o){s.value='${TID}';s.dispatchEvent(new Event('change'));return true}return false})()`);
  await sleep(2200);
}
try {
  ws = new WebSocket(await target());
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { const p = pending.get(m.id); pending.delete(m.id); p.resolve(m.result); } };
  await send("Page.enable"); await send("Runtime.enable");

  await loginAndOpen();
  const panelOk = await ev(`!!document.getElementById('saveLedger')`);
  check(panelOk, "台账面板存在", String(panelOk));

  // 原始 website（确保保存台账不丢）
  const origWebsite = await ev(`document.getElementById('ldgWebsite').value`);

  // 先清空台账再保存：确保起点干净，否则上次残留会让"落库"断言假绿。
  await ev(`(function(){
    ['ldgPackage','ldgPrice','ldgStart','ldgOwner','ldgPhone',
     't1Intro','t1Cases','t1Honors','t1Expertise','t1Photos','t3Words']
      .forEach(function(id){var el=document.getElementById(id);if(el)el.value=''});
    // 表二账号清空：点完所有删除按钮
    var dels=document.querySelectorAll('#t2Accounts [data-t2del]');
    for(var i=0;i<dels.length;i++) dels[i].click();
    return true; })()`);
  await ev(`document.getElementById('saveLedger').click()`);
  await sleep(1200);
  const cleared = await ev(`document.getElementById('ldgPackage').value`);
  check(cleared === "", "台账起点已清空", cleared);

  // 用唯一值填写：避免上次残留恰好等于测试值导致"落库"假绿。
  const tag = "T" + Date.now().toString().slice(-6);
  // 填台账 + 表一 + 表三
  await ev(`(function(){
    document.getElementById('ldgPackage').value='${tag}';
    document.getElementById('ldgPrice').value='9800';
    document.getElementById('ldgStart').value='2026-11-27';
    document.getElementById('ldgOwner').value='运营A';
    document.getElementById('ldgPhone').value='024-12345678';
    document.getElementById('t1Intro').value='${tag}简介';
    document.getElementById('t1Cases').value='${tag}案例';
    document.getElementById('t1Honors').value='${tag}荣誉';
    document.getElementById('t3Words').value='${tag}词';
    return true; })()`);
  // 表二加账号
  await ev(`(function(){document.getElementById('t2Platform').value='公众号';
    document.getElementById('t2Name').value='辽征法律';
    document.getElementById('t2Add').click(); return true})()`);
  const t2Before = await ev(`document.querySelectorAll('#t2Accounts .kv').length`);
  check(t2Before === 1, "表二账号添加", String(t2Before));

  await ev(`document.getElementById('saveLedger').click()`);
  await sleep(1800);
  const savedState = await ev(`document.getElementById('ledgerState').textContent`);
  check(/已保存/.test(savedState), "保存成功提示", savedState);

  // 刷新后仍在（真落库）
  await send("Page.navigate", { url: BASE + "/settings" });
  await sleep(2500);
  await loginAndOpen();
  const after = await ev(`(function(){return {
    pkg:document.getElementById('ldgPackage').value,
    price:document.getElementById('ldgPrice').value,
    owner:document.getElementById('ldgOwner').value,
    phone:document.getElementById('ldgPhone').value,
    intro:document.getElementById('t1Intro').value,
    cases:document.getElementById('t1Cases').value,
    honors:document.getElementById('t1Honors').value,
    t2:document.querySelectorAll('#t2Accounts .kv').length,
    t3:document.getElementById('t3Words').value,
    website:document.getElementById('ldgWebsite').value
  }})()`);
  check(after.pkg === tag && after.price === "9800" && after.owner === "运营A", "台账落库", JSON.stringify(after).slice(0, 90));
  check(after.intro === tag + "简介" && after.cases === tag + "案例" && after.honors === tag + "荣誉", "表一落库", `${after.intro}/${after.cases}`);
  check(after.t2 === 1, "表二落库", String(after.t2));
  check(after.t3 === tag + "词", "表三落库", after.t3);
  check(after.website === origWebsite, "保存不丢官网", `${origWebsite}→${after.website}`);

  // 通过 API 回读 settings 结构
  const apiSettings = await ev(`(async function(){var d=await (await fetch('/v1/trackers/${TID}')).json();return d.settings||{}})()`);
  check(apiSettings.service?.package === tag && apiSettings.assets?.table3?.geo_words === tag + "词", "API 回读 settings 结构", JSON.stringify(apiSettings).slice(0, 100));

  const failed = results.filter((x) => !x).length;
  console.log(failed === 0 ? "\nALL LEDGER CHECKS PASSED" : `\n${failed} FAILED`);
  process.exitCode = failed === 0 ? 0 : 1;
} catch (e) { console.error("ERROR", e.message); process.exitCode = 1; }
finally { try { ws?.close(); } catch {} chrome.kill(); }
