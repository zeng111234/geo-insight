// AI 生成草稿的「事实 + 积分流水」落库 E2E。
// 后端 6b0bdd0 修了真 bug：create_article 内部 commit 过一次，fact/ledger 是新事务，
// 不 commit 连接一关就回滚——草稿留了、事实与账本全丢。此前 UI E2E 只验文章数，
// 是盲区。这里补上：真点按钮生成 → 验 facts 非空 + 流水 ref 指向新文章。
// 用法：先 python backend/run.py，再 node backend/scripts/aifactcheck.mjs
// 换端口：GEO_BASE=http://127.0.0.1:8143 node backend/scripts/aifactcheck.mjs
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
const BASE = process.env.GEO_BASE || "http://127.0.0.1:8123";
const PORT = Number(process.env.GEO_CDP_PORT || 9438);
const CHROME = ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"].filter(existsSync)[0];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--no-sandbox", `--remote-debugging-port=${PORT}`, `--user-data-dir=${process.env.TEMP}/geo-aifact-cdp-${Date.now()}`, "about:blank"], { stdio: "ignore" });
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

  await send("Page.navigate", { url: BASE + "/studio" });
  await sleep(2500);
  // 项目默认开鉴权：未登录会被 302 到 /login，按钮不存在会报晦涩异常。
  // 自动登录再继续，鉴权/免鉴权两种环境都能跑。
  if (await ev(`location.pathname`) === "/login") {
    await ev(`(function(){
      document.querySelector('input[name=email]').value='demo@example.com';
      document.querySelector('input[name=password]').value='geo-demo-2026';
      document.querySelector('form').submit(); return true; })()`);
    await sleep(2200);
    await send("Page.navigate", { url: BASE + "/studio" });
    await sleep(2500);
  }
  await ev(`document.getElementById('newArticle').click()`);
  await sleep(1000);
  await ev(`(function(){var s=document.getElementById('newQuestion');if(s.options.length>1){s.selectedIndex=1}return true})()`);

  // 生成前流水里 ai_draft 条数
  const beforeLedger = await ev(`(async function(){var d=await (await fetch('/v1/credits')).json();return (d.entries||[]).filter(function(e){return e.ref_type==='ai_draft'}).length})()`);

  await ev(`document.getElementById('aiGenerate').click()`);

  // 等生成完成：找最新一篇 ai_draft 模板文章
  let newArticle = null;
  for (let i = 0; i < 24; i++) {
    await sleep(1500);
    newArticle = await ev(`(async function(){
      var d=await (await fetch('/v1/trackers/11111111-1111-1111-1111-111111111111/articles')).json();
      var a=(d.items||[]).filter(function(x){return x.template_key==='ai_draft'}).sort(function(x,y){return (y.created_at||'').localeCompare(x.created_at||'')})[0];
      return a ? {id:a.id, created_at:a.created_at} : null; })()`);
    if (newArticle) {
      const detail = await ev(`(async function(){var d=await (await fetch('/v1/articles/${newArticle.id}')).json();return (d.facts||[]).length})()`);
      if (detail > 0) break;
    }
  }
  check(!!newArticle, "AI 草稿生成", newArticle ? newArticle.id : "none");

  // 事实落库：GET /articles/{id} 带 facts
  const facts = await ev(`(async function(){var d=await (await fetch('/v1/articles/${newArticle.id}')).json();return d.facts||[]})()`);
  check(facts.length > 0, "事实条目真落库", String(facts.length));
  check(facts.some((f) => f.verdict === "pending"), "事实默认待核实", JSON.stringify(facts[0] || {}).slice(0, 80));

  // 积分流水落库：/credits 里有 ref_type=ai_draft & ref_id=新文章 id 的 SPEND
  const ledger = await ev(`(async function(){var d=await (await fetch('/v1/credits')).json();return (d.entries||[]).filter(function(e){return e.ref_type==='ai_draft'&&e.ref_id==='${newArticle.id}'})})()`);
  check(ledger.length === 1, "积分流水真落库", String(ledger.length));
  check(ledger.length === 1 && ledger[0].direction === "spend", "流水方向是 SPEND", JSON.stringify(ledger[0] || {}).slice(0, 80));
  check(ledger.length === 1 && ledger[0].amount === 5, "流水金额 5 分", String(ledger[0]?.amount));

  const afterLedger = await ev(`(async function(){var d=await (await fetch('/v1/credits')).json();return (d.entries||[]).filter(function(e){return e.ref_type==='ai_draft'}).length})()`);
  check(afterLedger === beforeLedger + 1, "ai_draft 流水总数+1", `${beforeLedger}→${afterLedger}`);

  const failed = results.filter((x) => !x).length;
  console.log(failed === 0 ? "\nALL AI FACT/LEDGER CHECKS PASSED" : `\n${failed} FAILED`);
  process.exitCode = failed === 0 ? 0 : 1;
} catch (e) { console.error("ERROR", e.message); process.exitCode = 1; }
finally { try { ws?.close(); } catch {} chrome.kill(); }
