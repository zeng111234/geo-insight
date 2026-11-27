// 业务闭环 E2E：机会页「写这篇」→ 工作室在正确对象上建草稿。
// 守住"发现机会→产出内容"这段真能用——之前 studio 硬编码演示 tracker，
// 真实试点对象点「写这篇」会被后端 QuestionNotFoundError 拒绝。
// 用法：先 python backend/run.py，再 node backend/scripts/loopcheck.mjs
// 换端口：GEO_BASE=http://127.0.0.1:8133 node backend/scripts/loopcheck.mjs
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
const BASE = process.env.GEO_BASE || "http://127.0.0.1:8123";
const PORT = Number(process.env.GEO_CDP_PORT || 9405);
const TRACKER = "78212285-75c2-4066-b4fc-f2d77288a7dd";
const CHROME = ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"].filter(existsSync)[0];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--no-sandbox",
  `--remote-debugging-port=${PORT}`, `--user-data-dir=${process.env.TEMP}/geo-loop-cdp-${Date.now()}`, "about:blank"], { stdio: "ignore" });
async function target() {
  for (let i = 0; i < 60; i++) {
    try { const r = await fetch(`http://127.0.0.1:${PORT}/json/list`); const l = await r.json();
      const p = l.find((t) => t.type === "page"); if (p) return p.webSocketDebuggerUrl; } catch {}
    await sleep(250);
  }
  throw new Error("no CDP");
}
let id = 0; const pending = new Map(); let ws;
function send(method, params = {}) { const mid = ++id; ws.send(JSON.stringify({ id: mid, method, params }));
  return new Promise((res, rej) => pending.set(mid, { resolve: res, reject: rej })); }
async function evaluate(expr) { const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails)); return r.result.value; }
const results = [];
const check = (ok, label, extra = "") => { results.push(ok); console.log(`${ok ? "PASS" : "FAIL"}  ${label}${extra ? ` (${extra})` : ""}`); };
const count = async (tracker) => (await (await fetch(`${BASE}/v1/trackers/${tracker}/articles`)).json()).items?.length ?? -1;
// 默认开鉴权：Node fetch 不带 cookie 会静默 401，早点说清楚（同类问题第 4 次）。
const probe = await fetch(`${BASE}/v1/trackers/11111111-1111-1111-1111-111111111111/articles`);
if (probe.status === 401) {
  console.log("FAIL  服务开启了鉴权，本脚本的 Node fetch 不带 cookie，会静默失败。");
  console.log("      用 GEO_AUTH_REQUIRED=0 python backend/run.py 启动后再跑本脚本。");
  process.exit(1);
}
try {
  const beforeTrial = await count(TRACKER);
  const beforeDemo = await count("11111111-1111-1111-1111-111111111111");

  ws = new WebSocket(await target());
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) {
    const p = pending.get(m.id); pending.delete(m.id); m.error ? p.reject(new Error(m.error.message)) : p.resolve(m.result); } };
  await send("Page.enable"); await send("Runtime.enable");
  const errors = [];
  ws.addEventListener("message", (e) => { const m = JSON.parse(e.data);
    if (m.method === "Runtime.exceptionThrown") errors.push(m.params.exceptionDetails.text || "exception"); });

  // 机会页带 tracker 打开，等列表渲染
  await send("Page.navigate", { url: `${BASE}/opportunities?tracker=${TRACKER}` });
  await sleep(3000);
  const link = await evaluate(`(function(){
    var a = Array.from(document.querySelectorAll('a.plain')).find(function(x){return x.textContent.indexOf('写这篇')>=0;});
    return a ? a.getAttribute('href') : null; })()`);
  check(link && link.includes("tracker=" + TRACKER), "写这篇链接带当前 tracker", String(link).slice(0, 90));
  const qid = link && new URL(link, BASE).searchParams.get("question");
  check(!!qid, "链接带问题 id", String(qid).slice(0, 20));

  // 真点进工作室
  await send("Page.navigate", { url: BASE + link });
  await sleep(3000);
  const label = await evaluate(`(document.getElementById('trackerLabel')||{}).textContent || ''`);
  check(label.includes("辽征试点") || label.includes("试点"), "工作室显示试点对象名", label);
  const selected = await evaluate(`(function(){
    var q=document.getElementById('newQuestion');
    return q && q.value; })()`);
  check(selected === qid, "问题预选正确（后端不会拒归属）", `${String(selected).slice(0,20)} vs ${String(qid).slice(0,20)}`);

  // 建一篇草稿：填标题正文，点创建
  await evaluate(`(function(){
    document.getElementById('newTitle').value='闭环验证-可删除';
    var ta=document.querySelector('#newPanel textarea')||document.getElementById('newBody');
    if(ta) ta.value='闭环验证正文，用于确认真实试点对象能落成草稿。';
    return true; })()`);
  await evaluate(`document.getElementById('createArticle').click()`);
  await sleep(2500);
  const afterTrial = await count(TRACKER);
  const afterDemo = await count("11111111-1111-1111-1111-111111111111");
  check(afterTrial === beforeTrial + 1, "草稿落到试点对象", `${beforeTrial}→${afterTrial}`);
  check(afterDemo === beforeDemo, "演示对象文章数不变", `${beforeDemo}→${afterDemo}`);

  check(errors.length === 0, "全程无 JS 异常", errors.join("; ").slice(0, 120));
  const failed = results.filter((x) => !x).length;
  console.log(failed === 0 ? "\nALL LOOP E2E CHECKS PASSED" : `\n${failed} FAILED`);
  process.exitCode = failed === 0 ? 0 : 1;
} catch (e) { console.error("ERROR", e.message); process.exitCode = 1; }
finally { try { ws?.close(); } catch {} chrome.kill(); }
