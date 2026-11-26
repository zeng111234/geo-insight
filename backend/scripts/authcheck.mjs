// 鉴权开启下的真浏览器端到端验证：登录 → cookie → CSRF 双提交写操作。
//
// 为什么单独要这一层：所有其他浏览器 E2E 都用 AUTH_REQUIRED=0 跑，
// test_auth_http.py 只测 HTTP 层（TestClient）。但 CSRF 的另一半在前端 JS——
// app.js 要读 geo_csrf cookie、放进 X-CSRF-Token 头。这块只有真浏览器点一遍才知道。
// 部署后第一步就是登录；如果这里断了，用户能登录但所有写操作静默失败（403）。
//
// 全程自包含：临时库副本 + 鉴权实例(独立端口) + Chrome(独立 CDP)，
// 不碰 8123、不碰真实 geo.db。写操作做完会改回去，临时库用完删掉。
//
// 用法：node backend/scripts/authcheck.mjs
// 指定浏览器：GEO_CHROME="C:\path\to\chrome.exe"

import { spawn } from "node:child_process";
import { existsSync, copyFileSync, rmSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("../..", import.meta.url)).replace(/[\\/]$/, "");
const BASE_PORT = 8139;
const CDP_PORT = 9357;
const BASE = `http://127.0.0.1:${BASE_PORT}`;
const EMAIL = "admin@authcheck.local";
const PASSWORD = "AuthCheck-Strong-2026!";
const TRACKER = "11111111-1111-1111-1111-111111111111";

const CANDIDATES = [
  process.env.GEO_CHROME,
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
].filter(Boolean);
const CHROME = CANDIDATES.find(existsSync);
if (!CHROME) { console.error("没有找到 Chrome/Edge，用 GEO_CHROME 指定"); process.exit(1); }

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
const check = (ok, label, extra = "") => {
  results.push(ok);
  console.log(`${ok ? "PASS" : "FAIL"}  ${label}${extra ? ` (${extra})` : ""}`);
};

const tmp = mkdtempSync(join(tmpdir(), "geo-authcheck-"));
const dbPath = join(tmp, "authcheck.db");
copyFileSync(join(ROOT, "geo.db"), dbPath); // 已迁移好的库副本

// 用 Python 解释器跑服务，不是 node（process.execPath 是 node 自己）。
const PY = process.env.GEO_PYTHON || "py";
const server = spawn(PY, ["-3.12", join(ROOT, "backend", "run.py"), "--no-browser"], {
  cwd: join(ROOT, "backend"),
  env: {
    ...process.env,
    GEO_DB_PATH: dbPath,
    GEO_PORT: String(BASE_PORT),
    GEO_HOST: "127.0.0.1",
    GEO_AUTH_REQUIRED: "1",
    GEO_ADMIN_EMAIL: EMAIL,
    GEO_ADMIN_PASSWORD: PASSWORD,
    GEO_PUBLISH_WORKER: "0",
  },
  stdio: "ignore",
});
const chrome = spawn(CHROME, [
  "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
  `--remote-debugging-port=${CDP_PORT}`,
  "--window-size=1680,1000",
  `--user-data-dir=${join(tmp, "chrome")}`,
  "about:blank",
], { stdio: "ignore" });

let ws;
let id = 0;
const pending = new Map();
const sentRequests = [];
function send(method, params = {}) {
  const mid = ++id;
  ws.send(JSON.stringify({ id: mid, method, params }));
  return new Promise((resolve, reject) => pending.set(mid, { resolve, reject }));
}
async function evaluate(expr) {
  const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
  return r.result.value;
}

let cleanupNote = "";
try {
  // 等服务起来
  let ready = false;
  for (let i = 0; i < 40; i++) {
    try { if ((await fetch(`${BASE}/health`)).status === 200) { ready = true; break; } } catch { /* 还没起 */ }
    await sleep(250);
  }
  if (!ready) throw new Error("鉴权实例没起来（health 非 200）");

  // 拿 CDP 页面
  let page;
  for (let i = 0; i < 60; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
      page = list.find((t) => t.type === "page");
      if (page) break;
    } catch { /* 浏览器还在起 */ }
    await sleep(250);
  }
  if (!page) throw new Error("拿不到 CDP 页面");

  ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) {
      const p = pending.get(m.id);
      pending.delete(m.id);
      m.error ? p.reject(new Error(m.error.message)) : p.resolve(m.result);
    }
    // 抓所有发出的请求头，后面断言 PATCH 带了 CSRF 头
    if (m.method === "Network.requestWillBeSent") {
      sentRequests.push({
        url: m.params.request.url,
        method: m.params.request.method,
        headers: m.params.request.headers || {},
      });
    }
    if (m.method === "Network.responseReceived" && m.params.response.url.includes("/questions/")) {
      sentRequests.push({ responseStatus: m.params.response.status, url: m.params.response.url });
    }
  };
  await send("Page.enable");
  await send("Runtime.enable");
  await send("Network.enable");

  // 1. 未登录打开页面 → 跳登录页
  await send("Page.navigate", { url: `${BASE}/settings?tracker=${TRACKER}` });
  await sleep(1800);
  const url1 = await evaluate("location.pathname");
  check(url1 === "/login", "未登录访问设置页跳到登录页", url1);

  // 2. 真填表单并提交（模拟真人输入，不用 fetch 直接打接口）
  await evaluate(`(function () {
    document.querySelector('input[name="email"]').value = ${JSON.stringify(EMAIL)};
    document.querySelector('input[name="password"]').value = ${JSON.stringify(PASSWORD)};
    document.querySelector('form[action="/login"]').submit();
  })()`);
  await sleep(2500);
  const url2 = await evaluate("location.pathname");
  check(url2 !== "/login", "登录成功跳回业务页", url2);

  // 3. cookie 落地：会话 + CSRF 两个都在
  const cookieStr = await evaluate("document.cookie");
  // 会话 cookie 必须 HttpOnly：JS document.cookie 读不到才对（读得到反而说明安全属性丢了）。
  check(!cookieStr.includes("geo_session="), "会话 cookie 是 HttpOnly（JS 读不到）", "正确");
  const csrfMatch = cookieStr.match(/geo_csrf=([^;]+)/);
  check(!!csrfMatch, "CSRF cookie 可被 JS 读取（双提交的另一半）", csrfMatch ? "已读到" : "缺失");

  // 4. 等设置页问题列表出来，记下第一条问题当前层级
  await sleep(1200);
  const first = await evaluate(`(function () {
    var b = document.querySelector('[data-question][data-next]');
    return b ? { id: b.dataset.question, next: b.dataset.next } : null;
  })()`);
  check(!!first, "设置页问题列表已渲染（登录后接口能带 cookie 读数据）", first ? first.id : "空");

  // 5. 真点「设为核心/改为长尾」按钮 → 触发 PATCH 写操作
  await evaluate(`document.querySelector('[data-question][data-next]').click()`);
  await sleep(1800);

  const patch = sentRequests.find(
    (r) => r.method === "PATCH" && r.url.includes("/questions/")
  );
  check(!!patch, "写操作真的发出了 PATCH 请求", patch ? patch.url : "没抓到");
  if (patch) {
    const header = Object.entries(patch.headers).find(([k]) => k.toLowerCase() === "x-csrf-token");
    const tokenFromCookie = csrfMatch ? decodeURIComponent(csrfMatch[1]) : "";
    check(!!header && header[1] === tokenFromCookie,
      "PATCH 带 X-CSRF-Token，且与页面 cookie 里的 token 一致",
      header ? `header=${String(header[1]).slice(0, 8)}… cookie=${tokenFromCookie.slice(0, 8)}…`
             : `缺头；实际头=${JSON.stringify(patch.headers)}`);
    const resp = sentRequests.find((r) => r.responseStatus && r.url.includes("/questions/"));
    console.log(`         PATCH 响应状态=${resp ? resp.responseStatus : "未抓到"}`);
  }

  // 6. 回读确认写真的生效（不是 403 假成功）。
  //    直接读服务端 API 最可靠——DOM 重渲染有延迟，不能拿它当"是否写入"的判据。
  // 用页面自己的 fetch（带会话 cookie）回读，避免脚本手动拼 cookie。
  const tierFromPage = await evaluate(`(async function () {
    var r = await fetch('/v1/trackers/' + ${JSON.stringify(TRACKER)} + '/questions');
    var d = await r.json();
    var q = (d.items || []).find(function (x) { return x.id === ${JSON.stringify(first.id)}; });
    return q ? q.tier : null;
  })()`);
  // first.next 是"目标层级"：若目标是 long_tail 则写入后当前层级应为 long_tail。
  check(tierFromPage === first.next,
    "写操作服务端接受（回读层级与目标一致，非 403）",
    `目标=${first.next} 回读=${tierFromPage}`);

  // 恢复：用页面 fetch 把层级写回原值（临时库本来会删，但清理动作要真实可验证，不能假装）。
  // 原层级 = 与 first.next 相反的值。
  const original = first.next === "core" ? "long_tail" : "core";
  const restored = await evaluate(`(async function () {
    var t = document.cookie.match(/(?:^|;\\s*)geo_csrf=([^;]+)/);
    var tok = t ? decodeURIComponent(t[1]) : '';
    var r = await fetch('/v1/questions/' + ${JSON.stringify(first.id)}, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': tok },
      body: JSON.stringify({ tier: ${JSON.stringify(original)} })
    });
    var d = await r.json().catch(function () { return {}; });
    return { status: r.status, tier: d.tier };
  })()`);
  check(restored.status === 200 && restored.tier === original,
    "恢复到原层级（清理真实生效，非口头声称）", JSON.stringify(restored));

  cleanupNote = first.id;
} catch (e) {
  console.error("FAIL  脚本异常：", e.message);
  results.push(false);
} finally {
  if (ws) try { ws.close(); } catch { /* 已断 */ }
  try { chrome.kill("SIGKILL"); } catch { /* 已退 */ }
  try { server.kill("SIGKILL"); } catch { /* 已退 */ }
  await sleep(300);
  try { rmSync(tmp, { recursive: true, force: true }); } catch { /* 系统占用，忽略 */ }
}

const pass = results.filter(Boolean).length;
console.log(`\nauthcheck: ${pass}/${results.length} 通过（临时库已删，未碰真实 geo.db / 8123）`);
process.exitCode = pass === results.length ? 0 : 1;
