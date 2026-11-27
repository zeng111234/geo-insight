// 事实清单来源徽章 E2E：审核者要一眼区分「第一方自述」与「外部信源」。
// 后端已把客户登记资料标「客户档案」、人工贴的标「人工参考」，与外部URL区分；
// 前端原来所有来源都是同一灰色小字，审核者看不出哪些是客户自己说的。
// 用法：先 python backend/run.py，再 node backend/scripts/sourcebadgecheck.mjs
// 换端口：GEO_BASE=http://127.0.0.1:8123 node backend/scripts/sourcebadgecheck.mjs
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
const BASE = process.env.GEO_BASE || "http://127.0.0.1:8123";
const PORT = Number(process.env.GEO_CDP_PORT || 9466);
const AID = "5433e581-e6a4-4c85-900f-26145f0c6a75"; // demo 对象下一篇同时有 URL + 人工参考的事实
const CHROME = ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"].filter(existsSync)[0];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--no-sandbox", `--remote-debugging-port=${PORT}`, `--user-data-dir=${process.env.TEMP}/geo-sourcebadge-cdp-${Date.now()}`, "about:blank"], { stdio: "ignore" });
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

  await send("Page.navigate", { url: BASE + "/studio?article=" + AID });
  await sleep(3000);
  if (await ev(`location.pathname`) === "/login") {
    await ev(`(function(){document.querySelector('input[name=email]').value='demo@example.com';
      document.querySelector('input[name=password]').value='geo-demo-2026';
      document.querySelector('form').submit();return true})()`);
    await sleep(2200);
    await send("Page.navigate", { url: BASE + "/studio?article=" + AID });
    await sleep(3000);
  }

  const factsText = await ev(`document.getElementById('facts').innerText`);
  const external = await ev(`document.querySelectorAll('#facts .source-tag.external').length`);
  const firstparty = await ev(`document.querySelectorAll('#facts .source-tag.firstparty').length`);
  const guide = await ev(`(document.querySelector('#facts .fact-guide')||{}).textContent||''`);

  check(external >= 1, "外部信源徽章出现", String(external));
  check(firstparty >= 1, "第一方自述徽章出现", String(firstparty));
  check(/第一方自述/.test(guide) && /人工核实/.test(guide), "口径说明存在", guide.slice(0, 60));
  const link = await ev(`(function(){var a=document.querySelector('#facts .src a');return a?{href:a.getAttribute('href'),rel:a.getAttribute('rel')}:null})()`);
  check(link && /^https?:\/\//.test(link.href), "外部信源可点链接", JSON.stringify(link).slice(0, 70));
  check(link && link.rel === "noopener noreferrer", "外链带安全rel", JSON.stringify(link).slice(0, 70));
  check(errors.length === 0, "全程无 JS 异常", errors.join("; ").slice(0, 100));

  const failed = results.filter((x) => !x).length;
  console.log(failed === 0 ? "\nALL SOURCE BADGE CHECKS PASSED" : `\n${failed} FAILED`);
  process.exitCode = failed === 0 ? 0 : 1;
} catch (e) { console.error("ERROR", e.message); process.exitCode = 1; }
finally { try { ws?.close(); } catch {} chrome.kill(); }
