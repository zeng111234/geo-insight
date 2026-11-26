// 守一个真 bug：设置页「切换到此集 / 新建问题集」的事件监听曾被错误地
// 注册在「导入并去重」的 handler 内部——首次进入页面点这两个按钮完全没反应，
// 必须先点一次导入才绑定。这里用真浏览器验证：不点导入，直接点「新建问题集」
// 必须触发 prompt；点「切换到此集」必须给出非「未绑定」的反馈。
// 用法：GEO_BASE=http://127.0.0.1:8123 node qsetbtncheck.mjs

import { spawn } from "node:child_process";
import { existsSync } from "node:fs";

const BASE = process.env.GEO_BASE || "http://127.0.0.1:8123";
const PORT = Number(process.env.GEO_CDP_PORT || 9371);
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
  `--user-data-dir=${process.env.TEMP || "/tmp"}/geo-qsetbtncheck`,
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

  await send("Page.navigate", { url: `${BASE}/settings?tracker=${TRACKER}` });
  await sleep(2500);
  let url = await evaluate("location.pathname");
  if (url === "/login") {
    await evaluate(`(function(){
      document.querySelector('input[name="email"]').value = ${JSON.stringify(EMAIL)};
      document.querySelector('input[name="password"]').value = ${JSON.stringify(PASSWORD)};
      document.querySelector('form[action="/login"]').submit();
    })()`);
    await sleep(2000);
  }
  url = await evaluate("location.pathname");
  check(url === "/settings", "登录后到达设置页", url);

  // 首次加载就覆盖 prompt 与 toast，记录调用；绝不点「导入并去重」。
  await evaluate(`(function(){
    window.__promptCalls = 0;
    window.prompt = function(){ window.__promptCalls++; return null; };
    window.__toasts = [];
    var oldToast = window.toast;
    window.toast = function(msg){ window.__toasts.push(String(msg)); if(oldToast) oldToast(msg); };
  })()`);

  const btnExists = await evaluate(`!!document.getElementById('qsetNew') && !!document.getElementById('qsetSwitch')`);
  check(btnExists, "两个按钮在页面上存在");

  // 直接点「新建问题集」——修复前监听器还没注册，prompt 不会被调用。
  await evaluate(`document.getElementById('qsetNew').click()`);
  await sleep(600);
  const promptCalls = await evaluate("window.__promptCalls");
  check(promptCalls === 1, "首次加载直接点「新建问题集」触发 prompt（无需先点导入）", `calls=${promptCalls}`);

  // 直接点「切换到此集」——修复前无反应，修复后应给出 toast 反馈。
  await evaluate(`document.getElementById('qsetSwitch').click()`);
  await sleep(600);
  const toasts = await evaluate("window.__toasts.join(' | ')");
  check(/请先选择|已切换|切换失败/.test(toasts), "首次加载直接点「切换到此集」有反馈", toasts || "无反馈");

  // 变异对照：确认这个检查真的在测监听器绑定——临时把按钮替换成克隆节点（丢监听），
  // 再点应无 prompt。这只动内存里的 DOM，不改文件、不改数据。
  await evaluate(`(function(){
    window.__promptCalls = 0;
    var b = document.getElementById('qsetNew');
    var clone = b.cloneNode(true);
    b.parentNode.replaceChild(clone, b);
  })()`);
  await evaluate(`document.getElementById('qsetNew').click()`);
  await sleep(400);
  const mutatedCalls = await evaluate("window.__promptCalls");
  check(mutatedCalls === 0, "变异：去掉监听后不触发 prompt（证明检查有效）", `calls=${mutatedCalls}`);
} catch (e) {
  console.error("[x] 验收异常：", e.message);
  results.push(false);
} finally {
  chrome.kill("SIGKILL");
}

const pass = results.filter(Boolean).length;
console.log(`\n问题集按钮: ${pass}/${results.length} 通过`);
process.exit(pass === results.length ? 0 : 1);
