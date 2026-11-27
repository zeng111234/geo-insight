// 事实人工审核闭环 E2E：真点「确认/驳回/撤销」→ 服务端回读 verdict 落库 → 页面徽章刷新。
// 后端 PATCH /articles/{id}/facts/{fact_id} 早已可用（三态/幂等/留来源/跨文章校验），
// 但 studio.html 事实区此前只有只读徽章、没有任何 PATCH 调用——审核员点不了。
// 本脚本守住这条接线：按钮存在 + 真点写回 + 服务端为准 + 无 JS 异常。
//
// 用法（脚本自起独立实例+副本库，不碰主库、不碰隔壁 8123）：
//   node backend/scripts/factreviewcheck.mjs
// 可选：GEO_BASE 指向已起的鉴权服务（此时跳过自起）：
//   GEO_BASE=http://127.0.0.1:8123 node backend/scripts/factreviewcheck.mjs
import { spawn } from "node:child_process";
import { existsSync, copyFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const PROJECT = resolve(import.meta.dirname, "../..");    // geo/ (scripts → backend → geo)
const ROOT = join(PROJECT, "backend");                    // backend/
const CHROME = ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"].filter(existsSync)[0];
const PORT = Number(process.env.GEO_PORT || 8157);
const CDP_PORT = Number(process.env.GEO_CDP_PORT || 9470);
const ARTICLE = "5433e581-e6a4-4c85-900f-26145f0c6a75";   // 演示对象里带 2 条 pending 事实的真实草稿
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let srv = null, tmpdirPath = null, chrome = null, ws = null;
const BASE = process.env.GEO_BASE || `http://127.0.0.1:${PORT}`;

async function waitHealth(url, tries = 60) {
  for (let i = 0; i < tries; i++) {
    try { const r = await fetch(url); if (r.ok) return true; } catch {}
    await sleep(250);
  }
  return false;
}

async function target() {
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`);
      const l = await r.json();
      const p = l.find((t) => t.type === "page");
      if (p) return p.webSocketDebuggerUrl;
    } catch {}
    await sleep(250);
  }
  throw Error("no cdp target");
}

let id = 0; const pending = new Map();
const jsErrors = [];
function send(m, p = {}) {
  const mid = ++id;
  ws.send(JSON.stringify({ id: mid, method: m, params: p }));
  return new Promise((res, rej) => pending.set(mid, { resolve: res, reject: rej }));
}
async function ev(e) {
  const r = await send("Runtime.evaluate", { expression: e, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) {
    const d = r.exceptionDetails.exception?.description || r.exceptionDetails.text || "unknown";
    throw new Error("浏览器执行失败: " + String(d).split("\n")[0].slice(0, 160));
  }
  return r.result.value;
}

const results = [];
const check = (ok, label, extra = "") => {
  results.push(ok);
  console.log(`${ok ? "PASS" : "FAIL"}  ${label}${extra ? ` (${extra})` : ""}`);
};

try {
  if (!process.env.GEO_BASE) {
    tmpdirPath = mkdtempSync(join(tmpdir(), "geo-fr-check-"));
    const dbPath = join(tmpdirPath, "review.db");
    copyFileSync(join(PROJECT, "geo.db"), dbPath);
    const env = { ...process.env, GEO_DB_PATH: dbPath, GEO_PORT: String(PORT), GEO_AUTH_REQUIRED: "1" };
    const PY = process.env.GEO_PY || "py";
    srv = spawn(PY, ["-3.12", "run.py", "--no-browser", "--port", String(PORT)], { cwd: ROOT, env, stdio: "ignore" });
    if (!await waitHealth(BASE + "/health")) throw Error("独立服务未就绪 port=" + PORT);
  }

  chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--no-sandbox",
    `--remote-debugging-port=${CDP_PORT}`,
    `--user-data-dir=${join(tmpdir() || ".", "geo-fr-cdp-" + Date.now())}`, "about:blank"], { stdio: "ignore" });
  ws = new WebSocket(await target());
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.method === "Runtime.exceptionThrown") {
      const d = m.params?.exceptionDetails;
      jsErrors.push(String(d?.exception?.description || d?.text || "js error").split("\n")[0]);
    }
    if (m.id && pending.has(m.id)) { const p = pending.get(m.id); pending.delete(m.id); p.resolve(m.result); }
  };
  await send("Page.enable"); await send("Runtime.enable");

  await send("Page.navigate", { url: BASE + "/studio?article=" + ARTICLE });
  await sleep(2600);
  // 默认鉴权：未登录跳 /login，自动登录再回目标页
  if (await ev(`location.pathname`) === "/login") {
    await ev(`(function(){
      document.querySelector('input[name=email]').value='demo@example.com';
      document.querySelector('input[name=password]').value='geo-demo-2026';
      document.querySelector('form').submit(); return true; })()`);
    await sleep(2200);
    await send("Page.navigate", { url: BASE + "/studio?article=" + ARTICLE });
    await sleep(2600);
  }
  check(await ev(`location.pathname`) === "/studio", "进入工作室页", await ev(`location.pathname`));

  // 找到目标文章并确认两条 pending 事实（浏览器内 fetch 带 cookie）
  await ev(`(function(){var c=document.querySelector('[data-id="${ARTICLE}"]');if(c){c.click();return true}return false})()`);
  await sleep(1200);
  const before = await ev(`(async function(){
    var d=await (await fetch('/v1/articles/${ARTICLE}')).json();
    return (d.facts||[]).map(function(f){return {id:f.id,verdict:f.verdict,source_ref:f.source_ref}});
  })()`);
  check(before.length >= 2, "文章有真实事实条目", String(before.length));
  const pend = before.filter((f) => f.verdict === "pending");
  check(pend.length >= 2, "至少两条待核实", String(pend.length));
  check(await ev(`document.querySelectorAll('[data-review="confirmed"]').length`) >= 2,
    "每条事实都有确认按钮", String(await ev(`document.querySelectorAll('[data-review="confirmed"]').length`)));

  const f1 = pend[0], f2 = pend[1];
  if (!f1 || !f2) throw Error("缺少可审核的 pending 事实");

  // 用真 DOM 点击按钮（不是直接 fetch）。给 f1 填核实依据，f2 留空测"未附来源"如实标注。
  await ev(`(function(){
    var i=document.querySelector('.review-src[data-fact-id="${f1.id}"]');
    if(i){i.value='https://example.com/verify/' + '${f1.id}'.slice(0,8);}
    var b=document.querySelector('[data-review="confirmed"][data-fact-id="${f1.id}"]');
    if(b){b.click();return true}return false; })()`);
  await sleep(1800);
  const afterConfirm = await ev(`(async function(){
    var d=await (await fetch('/v1/articles/${ARTICLE}')).json();
    var f=(d.facts||[]).filter(function(x){return x.id==='${f1.id}'})[0];
    return f||{}; })()`);
  check(afterConfirm.verdict === "confirmed", "确认写回服务端", String(afterConfirm.verdict));
  check(!!afterConfirm.source_ref && afterConfirm.source_ref.indexOf("verify") >= 0,
    "确认时填写的依据被保存", String(afterConfirm.source_ref || "").slice(0, 50));
  check(await ev(`(function(){
    var el=document.querySelector('.fact[data-fact-id="${f1.id}"] .verdict');
    return el ? el.textContent : ''; })()`) === "已确认", "页面徽章刷新为已确认");

  // f2 驳回：先清空输入框（该条原本有"人工参考"第一方来源）。后端规则是
  // "没给新来源就保留原来源；原来源也没有才标人工核实（未附来源）"——都不伪造 URL。
  await ev(`(function(){
    var i=document.querySelector('.review-src[data-fact-id="${f2.id}"]');
    if(i){i.value='';}
    var b=document.querySelector('[data-review="rejected"][data-fact-id="${f2.id}"]');
    if(b){b.click();return true}return false; })()`);
  await sleep(1800);
  const afterReject = await ev(`(async function(){
    var d=await (await fetch('/v1/articles/${ARTICLE}')).json();
    var f=(d.facts||[]).filter(function(x){return x.id==='${f2.id}'})[0];
    return f||{}; })()`);
  check(afterReject.verdict === "rejected", "驳回写回服务端", String(afterReject.verdict));
  // 未填新依据时保留原来源（"人工参考"），绝不能凭空造出一个 URL 来假装外部佐证。
  const beforeRef = before.filter((f) => f.id === f2.id)[0].source_ref || "";
  check(afterReject.source_ref === beforeRef,
    "未填依据保留原来源、不伪造", `${beforeRef}→${afterReject.source_ref}`);
  check(!/^https?:/.test(afterReject.source_ref || ""), "驳回不凭空造外部信源", String(afterReject.source_ref || "").slice(0, 40));

  // 撤销 f1 回 pending：三态可回退，证明不是只能前进的假按钮
  await ev(`(function(){
    var b=document.querySelector('[data-review="pending"][data-fact-id="${f1.id}"]');
    if(b){b.click();return true}return false; })()`);
  await sleep(1800);
  const afterUndo = await ev(`(async function(){
    var d=await (await fetch('/v1/articles/${ARTICLE}')).json();
    var f=(d.facts||[]).filter(function(x){return x.id==='${f1.id}'})[0];
    return f||{}; })()`);
  check(afterUndo.verdict === "pending", "撤销回待核实写回服务端", String(afterUndo.verdict));

  check(jsErrors.length === 0, "无 JS 异常", jsErrors.slice(0, 1).join(""));

  const failed = results.filter((x) => !x).length;
  console.log(failed === 0 ? "\nALL FACT REVIEW CHECKS PASSED" : `\n${failed} FAILED`);
  process.exitCode = failed === 0 ? 0 : 1;
} catch (e) {
  console.error("ERROR", e.message);
  process.exitCode = 1;
} finally {
  try { ws?.close(); } catch {}
  try { chrome?.kill(); } catch {}
  try { if (srv) srv.kill(); } catch {}
  try { if (tmpdirPath) rmSync(tmpdirPath, { recursive: true, force: true }); } catch {}
}
