// 系统更新页 E2E：状态能正常读取，不再因为 /v1/v1 重复前缀全 404。
// 用法：先 python backend/run.py，再 node backend/scripts/updatecheck.mjs
// 换端口：GEO_BASE=http://127.0.0.1:8137 node backend/scripts/updatecheck.mjs
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
const BASE = process.env.GEO_BASE || "http://127.0.0.1:8123";
const PORT = Number(process.env.GEO_CDP_PORT || 9431);
const CHROME = ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"].filter(existsSync)[0];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--no-sandbox", `--remote-debugging-port=${PORT}`, `--user-data-dir=${process.env.TEMP}/geo-update-cdp-${Date.now()}`, "about:blank"], { stdio: "ignore" });
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
  await send("Page.enable"); await send("Runtime.enable"); await send("Network.enable");
  let bad = [];
  ws.addEventListener("message", (e) => {
    const m = JSON.parse(e.data);
    if (m.method === "Network.responseReceived" && m.params.response.status >= 400) bad.push(m.params.response.status + " " + m.params.response.url.replace(BASE, ""));
  });

  await send("Page.navigate", { url: BASE + "/update" });
  await sleep(3000);

  const cur = await ev(`(document.getElementById('curVersion')||{}).textContent || ''`);
  const verBadge = await ev(`(document.getElementById('verBadge')||{}).textContent || ''`);
  check(cur && cur !== "…" && cur !== "（未知）", "当前版本读出来了", cur);
  check(/^v/.test(verBadge), "版本徽章渲染", verBadge);

  // 检查更新按钮：不应再返回 404（可能返回「还没配置更新源」，那是业务空态，不是报错）
  await ev(`(function(){var b=document.getElementById('check');if(b)b.click();return !!b;})()`);
  await sleep(2500);
  const checkText = await ev(`(document.getElementById('checkResult')||{}).innerText || ''`);
  check(checkText.length > 0 && !/Not Found|读取失败|404/i.test(checkText), "检查更新返回正常内容", checkText.slice(0, 50));

  const has404 = bad.filter((x) => /\/v1\/v1\//.test(x));
  check(has404.length === 0, "无 /v1/v1 重复前缀请求", has404.join("; ").slice(0, 100));
  check(bad.length === 0, "无 4xx/5xx 响应", bad.slice(0, 3).join("; ").slice(0, 100));

  const failed = results.filter((x) => !x).length;
  console.log(failed === 0 ? "\nALL UPDATE CHECKS PASSED" : `\n${failed} FAILED`);
  process.exitCode = failed === 0 ? 0 : 1;
} catch (e) { console.error("ERROR", e.message); process.exitCode = 1; }
finally { try { ws?.close(); } catch {} chrome.kill(); }
