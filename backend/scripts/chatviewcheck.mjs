// 回答详情对话样式 E2E：答案原文呈现成「用户提问 / AI 回答」气泡，
// 模型带头像+名字（如 DeepSeek 的 D 蓝头像），不再是纯文字墙。
// 用法：先 python backend/run.py，再 node backend/scripts/chatviewcheck.mjs
// 换端口：GEO_BASE=http://127.0.0.1:8135 node backend/scripts/chatviewcheck.mjs
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
const BASE = process.env.GEO_BASE || "http://127.0.0.1:8123";
const PORT = Number(process.env.GEO_CDP_PORT || 9417);
const ANSWER_ID = process.env.GEO_ANSWER_ID || "977b159d-1d2e-496f-b84c-1eb437d24c5b";
const CHROME = ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"].filter(existsSync)[0];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--no-sandbox",
  `--remote-debugging-port=${PORT}`, `--user-data-dir=${process.env.TEMP}/geo-chatview-cdp-${Date.now()}`, "about:blank"], { stdio: "ignore" });
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
try {
  ws = new WebSocket(await target());
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) {
    const p = pending.get(m.id); pending.delete(m.id); m.error ? p.reject(new Error(m.error.message)) : p.resolve(m.result); } };
  await send("Page.enable"); await send("Runtime.enable");
  const errors = [];
  ws.addEventListener("message", (e) => { const m = JSON.parse(e.data);
    if (m.method === "Runtime.exceptionThrown") errors.push(m.params.exceptionDetails.text || "exception"); });

  await send("Page.navigate", { url: `${BASE}/answer/${ANSWER_ID}` });
  await sleep(3000);

  const head = await evaluate(`(function(){
    var h=document.querySelector('.chat-head');
    return h ? { av: (h.querySelector('.bubble-av')||{}).textContent,
      name: (h.querySelector('.meta b')||{}).textContent } : null; })()`);
  check(!!head, "对话头渲染出来", JSON.stringify(head));
  check(head && head.name && head.name.length > 0, "顶部显示模型名字", head?.name || "none");
  check(head && head.av && head.av.length > 0, "顶部显示模型头像", head?.av || "none");

  const bubbles = await evaluate(`({
    user: document.querySelectorAll('.bubble.user').length,
    ai: document.querySelectorAll('.bubble.ai').length,
    mineRows: document.querySelectorAll('.bubble-row.mine').length,
    userText: (document.querySelector('.bubble.user')||{}).textContent || '',
    aiLen: ((document.querySelector('.bubble.ai')||{}).textContent || '').length
  })`);
  check(bubbles.user === 1 && bubbles.ai === 1, "用户与AI各一个气泡", JSON.stringify({u:bubbles.user,a:bubbles.ai}));
  check(bubbles.mineRows === 1, "提问气泡在右侧（mine）", String(bubbles.mineRows));
  check(bubbles.userText.length > 0 && bubbles.aiLen > 100, "气泡含真实文本", `问${bubbles.userText.length}/答${bubbles.aiLen}`);

  // 头像配色：不同模型应有不同颜色（这里验 DeepSeek 头像有内联背景色）
  const avColor = await evaluate(`(function(){
    var av=document.querySelector('.chat-head .bubble-av');
    return av ? av.getAttribute('style') : ''; })()`);
  check(/background/.test(avColor), "头像带品牌色", avColor.slice(0, 60));

  check(errors.length === 0, "全程无 JS 异常", errors.join("; ").slice(0, 120));
  const failed = results.filter((x) => !x).length;
  console.log(failed === 0 ? "\nALL CHAT VIEW CHECKS PASSED" : `\n${failed} FAILED`);
  process.exitCode = failed === 0 ? 0 : 1;
} catch (e) { console.error("ERROR", e.message); process.exitCode = 1; }
finally { try { ws?.close(); } catch {} chrome.kill(); }
