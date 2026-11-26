// 守监控页「已监控」引用筛选的诚实标注：
// 后端 monitoring_available=false 时，选项文本要带「未接入」，点前就看得见；
// 但仍须可选（维持 citeView=monitored 深链），选中后弹诚实提示不冒充「已监控」。
// 用法：GEO_BASE=http://127.0.0.1:8123 node citeviewcheck.mjs

import { spawn } from "node:child_process";
import { existsSync } from "node:fs";

const BASE = process.env.GEO_BASE || "http://127.0.0.1:8123";
const PORT = Number(process.env.GEO_CDP_PORT || 9374);
const PASSWORD = process.env.GEO_PASSWORD || "geo-demo-2026";
const EMAIL = process.env.GEO_EMAIL || "demo@example.com";
const TRACKER = process.env.GEO_TRACKER || "d3aae9ce-cb61-468a-9ab2-51d2f4d28bf1";
const CANDIDATES = [
  process.env.GEO_CHROME,
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
  "/usr/bin/google-chrome", "/usr/bin/chromium",
].filter(Boolean);
const CHROME = CANDIDATES.find(existsSync);
if (!CHROME) { console.error("没找到 Chrome/Edge"); process.exit(1); }

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const chrome = spawn(CHROME, [
  "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
  `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${process.env.TEMP || "/tmp"}/geo-citeviewcheck-${Date.now()}`,
  "about:blank",
], { stdio: "ignore" });

let id = 0;
const pending = new Map();
let ws;
function send(method, params = {}) {
  const mid = ++id;
  ws.send(JSON.stringify({ id: mid, method, params }));
  return new Promise((resolve, reject) => pending.set(mid, { resolve, reject }));
}
async function evaluate(expr) {
  const r = await send("Runtime.evaluate", {
    expression: expr, awaitPromise: true, returnByValue: true,
  });
  if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
  return r.result.value;
}
const results = [];
const check = (ok, label, extra = "") => {
  results.push(ok);
  console.log(`${ok ? "PASS" : "FAIL"}  ${label}${extra ? ` (${extra})` : ""}`);
};
async function waitTarget() {
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/json/list`);
      const page = (await r.json()).find((t) => t.type === "page");
      if (page) return page.webSocketDebuggerUrl;
    } catch { /* 还在起 */ }
    await sleep(250);
  }
  throw new Error("拿不到 CDP 页面");
}

try {
  ws = new WebSocket(await waitTarget());
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) {
      const p = pending.get(m.id);
      pending.delete(m.id);
      m.error ? p.reject(new Error(m.error.message)) : p.resolve(m.result);
    }
  };
  await send("Page.enable");
  await send("Runtime.enable");

  await send("Page.navigate", { url: `${BASE}/monitor?tracker=${TRACKER}` });
  await sleep(2200);
  let url = await evaluate("location.pathname");
  if (url === "/login") {
    await evaluate(`(function(){
      document.querySelector('input[name="email"]').value = ${JSON.stringify(EMAIL)};
      document.querySelector('input[name="password"]').value = ${JSON.stringify(PASSWORD)};
      document.querySelector('form[action="/login"]').submit();
    })()`);
    await sleep(2200);
  }
  await sleep(1500);

  const opt = await evaluate(`(function(){
    var s=document.getElementById('citeView');
    if(!s) return null;
    var m=Array.from(s.options).find(function(o){return o.value==='monitored'});
    return m ? {text:m.textContent, disabled:m.disabled} : null;
  })()`);
  check(!!opt, "引用视图「已监控」选项存在");
  check(opt && /未接入/.test(opt.text), "「已监控」点前就标出未接入", opt ? opt.text : "无");
  check(opt && !opt.disabled, "选项仍可选（维持深链，不 disabled）");

  // 选中后仍弹诚实提示，不冒充「已监控」。
  await evaluate(`(function(){
    var s=document.getElementById('citeView');
    s.value='monitored';
    s.dispatchEvent(new Event('change',{bubbles:true}));
  })()`);
  await sleep(900);
  const citeText = await evaluate(`(document.getElementById('citeBody')||{}).innerText || ''`);
  check(/尚未接入|不能把全部引用冒充/.test(citeText), "选中后仍弹诚实提示", citeText.slice(0, 50));

  // 深链：URL 带 citeView=monitored 时选项应被正确选中（证明加标注没破坏 value 机制）。
  await send("Page.navigate", { url: `${BASE}/monitor?tracker=${TRACKER}&citeView=monitored` });
  await sleep(2600);
  const deep = await evaluate(`(function(){
    var s=document.getElementById('citeView');
    return s ? s.value : null;
  })()`);
  check(deep === "monitored", "深链 citeView=monitored 仍正确选中", `value=${deep}`);
} catch (e) {
  console.error("[x] 验收异常：", e.message);
  results.push(false);
} finally {
  chrome.kill("SIGKILL");
}

const pass = results.filter(Boolean).length;
console.log(`\n已监控标注: ${pass}/${results.length} 通过`);
process.exit(pass === results.length ? 0 : 1);
