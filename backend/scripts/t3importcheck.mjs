// 表三 GEO 词 → 填入导入框 E2E：省去手动复制，但绝不自动导入。
// 用户拍板「表三与问题集保持分离、界面讲清楚」，此功能只填文本、不替用户决定层级。
// 用法：先 python backend/run.py，再 node backend/scripts/t3importcheck.mjs
// 换端口：GEO_BASE=http://127.0.0.1:8155 node backend/scripts/t3importcheck.mjs
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
const BASE = process.env.GEO_BASE || "http://127.0.0.1:8123";
const PORT = Number(process.env.GEO_CDP_PORT || 9482);
const TID = "11111111-1111-1111-1111-111111111111";
const CHROME = ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"].filter(existsSync)[0];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--no-sandbox", `--remote-debugging-port=${PORT}`, `--user-data-dir=${process.env.TEMP}/geo-t3import-cdp-${Date.now()}`, "about:blank"], { stdio: "ignore" });
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
  await sleep(2000);

  const btn = await ev(`!!document.getElementById('t3FillImport')`);
  check(btn, "表三有「填入导入框」按钮", String(btn));

  // 问题导入前条数（确认不自动导入）
  const qBefore = await ev(`(async function(){var d=await(await fetch('/v1/trackers/${TID}/questions')).json();return (d.items||[]).length})()`);

  // 填入带重复、中英文逗号、顿号的词
  await ev(`(function(){document.getElementById('t3Words').value='律所，刑事辩护、离婚,交通事故；律所';
    document.getElementById('t3FillImport').click();return true})()`);
  await sleep(400);
  const filled = await ev(`document.getElementById('importText').value`);
  const lines = filled.split("\n").filter(Boolean);
  check(lines.length === 4, "拆成一行一条且去重", JSON.stringify(lines));
  check(lines.join("|") === "律所|刑事辩护|离婚|交通事故", "词顺序与内容正确", lines.join("|"));
  check(!filled.includes("*"), "不替用户加 * 核心标记", filled.replace(/\n/g, "|"));

  // 绝不自动导入：问题数不变
  const qAfter = await ev(`(async function(){var d=await(await fetch('/v1/trackers/${TID}/questions')).json();return (d.items||[]).length})()`);
  check(qAfter === qBefore, "只填文本不自动导入（问题数不变）", `${qBefore}→${qAfter}`);

  // 已有内容时追加不覆盖
  await ev(`(function(){document.getElementById('t3Words').value='新词';
    document.getElementById('t3FillImport').click();return true})()`);
  await sleep(300);
  const appended = await ev(`document.getElementById('importText').value`);
  check(appended.includes("律所") && appended.endsWith("新词"), "已有内容时追加不覆盖", appended.replace(/\n/g, "|"));

  check(errors.length === 0, "全程无 JS 异常", errors.join("; ").slice(0, 100));
  const failed = results.filter((x) => !x).length;
  console.log(failed === 0 ? "\nALL T3 IMPORT CHECKS PASSED" : `\n${failed} FAILED`);
  process.exitCode = failed === 0 ? 0 : 1;
} catch (e) { console.error("ERROR", e.message); process.exitCode = 1; }
finally { try { ws?.close(); } catch {} chrome.kill(); }
