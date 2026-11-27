// AI 助手 E2E（roadmap E4 收口）：真浏览器打开助手→提问→真调 LLM→回答+扣分显示。
// 诚实边界：只基于系统真实数据快照；按钮明示扣 3 分；失败显式报错。
// 注意：本脚本会真调一次 DeepSeek（花钱），验证意图层必需。
// 用法：先 python backend/run.py（默认鉴权），再 node backend/scripts/assistantcheck.mjs
// 换端口：GEO_BASE=http://127.0.0.1:8153 node backend/scripts/assistantcheck.mjs
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
const BASE = process.env.GEO_BASE || "http://127.0.0.1:8123";
const PORT = Number(process.env.GEO_CDP_PORT || 9475);
const CHROME = ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"].filter(existsSync)[0];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--no-sandbox", `--remote-debugging-port=${PORT}`, `--user-data-dir=${process.env.TEMP}/geo-assistant-cdp-${Date.now()}`, "about:blank"], { stdio: "ignore" });
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

  await send("Page.navigate", { url: BASE + "/" });
  await sleep(3000);
  if (await ev(`location.pathname`) === "/login") {
    await ev(`(function(){document.querySelector('input[name=email]').value='demo@example.com';
      document.querySelector('input[name=password]').value='geo-demo-2026';
      document.querySelector('form').submit();return true})()`);
    await sleep(2200);
    await send("Page.navigate", { url: BASE + "/" });
    await sleep(3000);
  }

  const btn = await ev(`!!document.getElementById('assistantBtn')`);
  check(btn, "工作台有 AI 助手入口", String(btn));
  await ev(`document.getElementById('assistantBtn').click()`);
  await sleep(500);
  const drawerOpen = await ev(`document.getElementById('drawer').classList.contains('show')`);
  check(drawerOpen, "助手抽屉打开", String(drawerOpen));
  const notice = await ev(`document.querySelector('#drawerBody .notice').textContent`);
  check(/真实数据快照/.test(notice) && /DeepSeek/.test(notice) && /3 分/.test(notice),
    "抽屉明示真实数据/真调/扣分", notice.slice(0, 60));

  const balBefore = await ev(`(async function(){var d=await(await fetch('/v1/credits')).json();return d.balance})()`);

  // 真调一次
  await ev(`document.getElementById('asstQuestion').value='这期提及率怎么样？'`);
  await ev(`document.getElementById('asstSend').click()`);
  // 轮询等待回答（LLM 可能要几十秒）
  let done = false;
  for (let i = 0; i < 90; i++) {
    await sleep(1000);
    const n = await ev(`document.querySelectorAll('#asstLog .asst-msg.assistant').length`);
    const err = await ev(`document.querySelectorAll('#asstLog .asst-msg.error').length`);
    if (n > 0 || err > 0) { done = true; break; }
  }
  check(done, "助手返回（等待真调完成）", String(done));
  const answer = await ev(`(function(){var a=document.querySelector('#asstLog .asst-msg.assistant');return a?a.textContent:''})()`);
  check(answer.length > 8 && !/加载中/.test(answer), "回答已渲染", answer.slice(0, 70));
  const meta = await ev(`(function(){var m=document.querySelector('#asstLog .asst-msg.assistant .meta');return m?m.textContent:''})()`);
  check(/扣 3 分/.test(meta) && /余额/.test(meta), "显示扣分与余额", meta.slice(0, 60));

  const balAfter = await ev(`(async function(){var d=await(await fetch('/v1/credits')).json();return d.balance})()`);
  check(balAfter === balBefore - 3, "积分真扣 3 分（账本落库）", `${balBefore}→${balAfter}`);

  // 回读流水，确认 ref_type=assistant 留痕
  const entry = await ev(`(async function(){var d=await(await fetch('/v1/credits')).json();return (d.entries||[]).find(function(e){return e.ref_type==='assistant'})||null})()`);
  check(entry && entry.amount === 3, "积分流水留痕 assistant", JSON.stringify(entry).slice(0, 80));
  check(errors.length === 0, "全程无 JS 异常", errors.join("; ").slice(0, 100));

  const failed = results.filter((x) => !x).length;
  console.log(failed === 0 ? "\nALL ASSISTANT CHECKS PASSED" : `\n${failed} FAILED`);
  process.exitCode = failed === 0 ? 0 : 1;
} catch (e) { console.error("ERROR", e.message); process.exitCode = 1; }
finally { try { ws?.close(); } catch {} chrome.kill(); }
