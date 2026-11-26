// 守首页数据来源标识真实化：scope 必须按 /data-provenance 渲染，
// 不能无条件写死「演示数据」。真采集对象要显示「真实采集」，
// 无数据显示「暂无数据」，接口挂了显示「来源未知」——拿不准不猜真假。
// 用法：GEO_BASE=http://127.0.0.1:8123 node scopecheck.mjs

import { spawn } from "node:child_process";
import { existsSync } from "node:fs";

const BASE = process.env.GEO_BASE || "http://127.0.0.1:8123";
const PORT = Number(process.env.GEO_CDP_PORT || 9372);
const PASSWORD = process.env.GEO_PASSWORD || "geo-demo-2026";
const EMAIL = process.env.GEO_EMAIL || "demo@example.com";
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
  `--user-data-dir=${process.env.TEMP || "/tmp"}/geo-scopecheck-${Date.now()}`,
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

  await send("Page.navigate", { url: `${BASE}/` });
  await sleep(2200);
  let url = await evaluate("location.pathname");
  if (url === "/login") {
    await evaluate(`(function(){
      document.querySelector('input[name="email"]').value = ${JSON.stringify(EMAIL)};
      document.querySelector('input[name="password"]').value = ${JSON.stringify(PASSWORD)};
      document.querySelector('form[action="/login"]').submit();
    })()`);
    await sleep(2000);
  }
  await sleep(1800);

  const state = await evaluate(`(function(){
    var el=document.getElementById('scope');
    var sel=document.getElementById('tracker');
    var opt=sel && sel.selectedOptions[0];
    return { text: el ? el.textContent : '', kind: el ? el.dataset.sourceKind : null,
             value: sel ? sel.value : '', count: sel ? sel.options.length : 0 };
  })()`);
  check(state.count > 0, "监控对象下拉已加载", `count=${state.count}`);
  check(!/^加载中/.test(state.text) && state.text.length > 0, "scope 已从「加载中」更新", state.text);
  check(["mixed", "real", "demo", "empty"].includes(state.kind),
    "scope 带真实来源类型标记（非写死）", `kind=${state.kind}`);
  // 核心：默认第一个对象可能全是演示数据，也可能有真数据——关键是标识与接口一致。
  const prov = await evaluate(`(async function(){
    var sel=document.getElementById('tracker');
    if(!sel || !sel.value) return null;
    var r=await fetch('/v1/trackers/'+sel.value+'/data-provenance');
    return await r.json();
  })()`);
  check(!!prov, "provenance 接口可读", prov ? `total=${prov.total}` : "null");
  if (prov) {
    const expectKind = !prov.total ? "empty"
      : (prov.has_mixed ? "mixed" : (prov.real_count > 0 ? "real" : "demo"));
    check(state.kind === expectKind, "scope 类型与接口构成一致", `ui=${state.kind} api=${expectKind}`);
    const hasCount = /[0-9]/.test(state.text) || state.kind === "empty";
    check(hasCount || state.kind === "mixed", "标识含真实条数或明确空态", state.text);
  }

  // 切换到最后一个对象，scope 类型必须跟着变（证明切换时重算，不是只算一次）。
  const before = state.kind;
  await evaluate(`(function(){
    var sel=document.getElementById('tracker');
    sel.selectedIndex = sel.options.length - 1;
    sel.dispatchEvent(new Event('change',{bubbles:true}));
    document.getElementById('load').click();
  })()`);
  await sleep(2200);
  const after = await evaluate(`(function(){
    var el=document.getElementById('scope');
    return { text: el.textContent, kind: el.dataset.sourceKind };
  })()`);
  check(["mixed", "real", "demo", "empty"].includes(after.kind), "切换对象后 scope 仍是合法来源类型", `${before}→${after.kind}`);
  check(after.text !== state.text, "切换对象后 scope 已刷新", after.text);
} catch (e) {
  console.error("[x] 验收异常：", e.message);
  results.push(false);
} finally {
  chrome.kill("SIGKILL");
}

const pass = results.filter(Boolean).length;
console.log(`\n首页来源标识: ${pass}/${results.length} 通过`);
process.exit(pass === results.length ? 0 : 1);
