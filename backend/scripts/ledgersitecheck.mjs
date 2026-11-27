// 跨页面闭环 E2E：台账录入(settings.service/assets) → 信源落地页(/site/data) 读出同样值。
// SOP 第一步(台账) 与 第三步(AI转化通道) 串起来：台账登记的电话/官网/简介要流到公开落地页。
// 两个会话分头做时用共享的 settings 键名对齐，这里守住不断链。
// 用法：先 python backend/run.py，再 node backend/scripts/ledgersitecheck.mjs
// 换端口：GEO_BASE=http://127.0.0.1:8148 node backend/scripts/ledgersitecheck.mjs
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
const BASE = process.env.GEO_BASE || "http://127.0.0.1:8123";
const PORT = Number(process.env.GEO_CDP_PORT || 9458);
const TID = "11111111-1111-1111-1111-111111111111";
const CHROME = ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"].filter(existsSync)[0];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--no-sandbox", `--remote-debugging-port=${PORT}`, `--user-data-dir=${process.env.TEMP}/geo-ledgersite-cdp-${Date.now()}`, "about:blank"], { stdio: "ignore" });
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

  // 登录（鉴权环境；免鉴权时 skip）
  await send("Page.navigate", { url: BASE + "/settings" });
  await sleep(2200);
  if (await ev(`location.pathname`) === "/login") {
    await ev(`(function(){document.querySelector('input[name=email]').value='demo@example.com';
      document.querySelector('input[name=password]').value='geo-demo-2026';
      document.querySelector('form').submit();return true})()`);
    await sleep(2200);
  }

  const tag = "S" + Date.now().toString().slice(-6);
  // 用浏览器 fetch（带 cookie + CSRF）写台账
  const wrote = await ev(`(async function(){
    var csrf=(document.cookie.match(/(?:^|;\\s*)geo_csrf=([^;]+)/)||[])[1]||'';
    var r=await fetch('/v1/trackers/${TID}',{method:'PATCH',
      headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},
      body:JSON.stringify({settings:{service:{phone:'${tag}',website:'https://${tag}.example.com'},
        assets:{table1:{intro:'${tag}简介'}}}})});
    return r.status; })()`);
  check(wrote === 200, "台账写入（浏览器带cookie）", String(wrote));

  // 公开落地页 data（无登录）读出同样值
  await send("Page.navigate", { url: BASE + "/site/" + TID });
  await sleep(2000);
  const data = await ev(`(async function(){var r=await fetch('/site/${TID}/data');return r.json()})()`);
  check(data.phone === tag, "落地页读出台账电话", `${data.phone}`);
  check(data.website === "https://" + tag + ".example.com", "落地页读出台账官网", `${data.website}`);
  check((data.intro || "").includes(tag + "简介"), "落地页读出台账简介", `${(data.intro||'').slice(0,30)}`);

  const failed = results.filter((x) => !x).length;
  console.log(failed === 0 ? "\nALL LEDGER→SITE CHECKS PASSED" : `\n${failed} FAILED`);
  process.exitCode = failed === 0 ? 0 : 1;
} catch (e) { console.error("ERROR", e.message); process.exitCode = 1; }
finally { try { ws?.close(); } catch {} chrome.kill(); }
