// 安全加固的跨切面回归 E2E：确认安全头/CORS 改动没破坏真实场景。
// 与 test_security_headers.py（单测守中间件行为）互补：这里守「用户真会用的路径」。
// 覆盖：①导出下载(StreamingResponse经BaseHTTPMiddleware) ②公开落地页 ③三安全头
//       ④CORS 恶意源拒绝/本地端口放行。
// 用法：先 python backend/run.py（默认鉴权），再 node backend/scripts/securityregressioncheck.mjs
// 换端口：GEO_BASE=http://127.0.0.1:8154 node backend/scripts/securityregressioncheck.mjs
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
const BASE = process.env.GEO_BASE || "http://127.0.0.1:8123";
const PORT = Number(process.env.GEO_CDP_PORT || 9480);
const TID = "11111111-1111-1111-1111-111111111111";
const CHROME = ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"].filter(existsSync)[0];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--no-sandbox", `--remote-debugging-port=${PORT}`, `--user-data-dir=${process.env.TEMP}/geo-secreg-cdp-${Date.now()}`, "about:blank"], { stdio: "ignore" });
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

  // --- CORS 用 Node 侧原生 fetch（带 Origin 头），无需登录 ---
  const evil = await fetch(BASE + "/v1/meta/methodology", { headers: { Origin: "https://evil.example.com" } });
  check(!evil.headers.get("access-control-allow-origin"), "CORS 拒绝恶意源", evil.headers.get("access-control-allow-origin") || "无(正确)");
  const local = await fetch(BASE + "/v1/meta/methodology", { headers: { Origin: "http://127.0.0.1:9999" } });
  check(local.headers.get("access-control-allow-origin") === "http://127.0.0.1:9999", "CORS 放行本地回环端口", local.headers.get("access-control-allow-origin") || "无");
  const pubHdr = evil.headers;
  check(pubHdr.get("x-content-type-options") === "nosniff" &&
        pubHdr.get("referrer-policy") === "strict-origin-when-cross-origin" &&
        pubHdr.get("x-frame-options") === "SAMEORIGIN",
    "三安全头齐全（含 401 响应）", `${pubHdr.get("x-content-type-options")}/${pubHdr.get("x-frame-options")}`);

  // --- 登录态：下载 + 落地页 ---
  await send("Page.navigate", { url: BASE + "/login" });
  await sleep(2000);
  if (await ev(`location.pathname`) === "/login") {
    await ev(`(function(){document.querySelector('input[name=email]').value='demo@example.com';
      document.querySelector('input[name=password]').value='geo-demo-2026';
      document.querySelector('form').submit();return true})()`);
    await sleep(2200);
  }
  const exp = await ev(`(async function(){
    var r=await fetch('/v1/trackers/${TID}/answers/export?from=2026-11-01&to=2026-11-03&format=csv');
    var body=await r.text();
    return {status:r.status, nosniff:r.headers.get('x-content-type-options'),
      xframe:r.headers.get('x-frame-options'), len:body.length, head:body.slice(0,40)};
  })()`);
  check(exp.status === 200 && exp.len > 20, "导出CSV下载未被中间件破坏", `${exp.status} len=${exp.len}`);
  check(exp.nosniff === "nosniff" && exp.xframe === "SAMEORIGIN", "下载响应仍带安全头", `${exp.nosniff}/${exp.xframe}`);
  check(/question|问题|answer_id/.test(exp.head), "导出内容非空壳", exp.head.replace(/\n/g, " ").slice(0, 50));

  // 落地页未登录可访问：用无 cookie 的 Node fetch
  const site = await fetch(BASE + "/site/" + TID);
  const siteHtml = await site.text();
  check(site.status === 200 && /信源|<html/i.test(siteHtml), "公开落地页未登录可访问", `${site.status}`);
  check(site.headers.get("x-frame-options") === "SAMEORIGIN", "落地页带安全头", site.headers.get("x-frame-options") || "无");

  const failed = results.filter((x) => !x).length;
  console.log(failed === 0 ? "\nALL SECURITY REGRESSION CHECKS PASSED" : `\n${failed} FAILED`);
  process.exitCode = failed === 0 ? 0 : 1;
} catch (e) { console.error("ERROR", e.message); process.exitCode = 1; }
finally { try { ws?.close(); } catch {} chrome.kill(); }
