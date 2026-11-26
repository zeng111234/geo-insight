// 全局模型头像徽章 E2E：所有显示模型名的表格/列表都用「头像+名字」，不再是纯文字。
// 老板反馈参考系统里 AI平台列带 Kimi 头像+名字，我们此前全是纯文字 model_key。
// 用法：先 python backend/run.py，再 node backend/scripts/modelbadgecheck.mjs
// 换端口：GEO_BASE=http://127.0.0.1:8136 node backend/scripts/modelbadgecheck.mjs
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
const BASE = process.env.GEO_BASE || "http://127.0.0.1:8123";
const PORT = Number(process.env.GEO_CDP_PORT || 9419);
const CHROME = ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"].filter(existsSync)[0];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--no-sandbox",
  `--remote-debugging-port=${PORT}`, `--user-data-dir=${process.env.TEMP}/geo-badge-cdp-${Date.now()}`, "about:blank"], { stdio: "ignore" });
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

// 有模型名展示的页面（默认日期窗口内有数据）
const PAGES = ["/", "/monitor", "/insight", "/opportunities", "/publish", "/pilot"];
try {
  ws = new WebSocket(await target());
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) {
    const p = pending.get(m.id); pending.delete(m.id); m.error ? p.reject(new Error(m.error.message)) : p.resolve(m.result); } };
  await send("Page.enable"); await send("Runtime.enable");
  const errors = [];
  ws.addEventListener("message", (e) => { const m = JSON.parse(e.data);
    if (m.method === "Runtime.exceptionThrown") errors.push(m.params.exceptionDetails.text || "exception"); });

  let totalBadges = 0;
  for (const path of PAGES) {
    errors.length = 0;
    await send("Page.navigate", { url: BASE + path });
    await sleep(2500);
    // monitor 默认「指标看板」没有模型列，模型名在「问答明细」tab。
    if (path === "/monitor") {
      await evaluate(`(function(){var b=document.querySelector('[data-tab="answers"]');if(b)b.click();return !!b;})()`);
      await sleep(2000);
    }
    const info = await evaluate(`(function(){
      var bs = document.querySelectorAll('.model-badge');
      var withAv = Array.from(bs).filter(function(b){return b.querySelector('.model-badge-av');}).length;
      var names = Array.from(bs).slice(0,3).map(function(b){return b.textContent.trim();});
      // 是否还有裸露的纯文字模型名单元格（精确匹配 td 内容）
      var rawCells = Array.from(document.querySelectorAll('td')).filter(function(td){
        return /^(doubao|deepseek|kimi|qwen|yuanbao|wenxin|quark|bing)$/.test(td.textContent.trim());
      }).length;
      return { count: bs.length, withAv: withAv, names: names, rawCells: rawCells };
    })()`);
    totalBadges += info.count;
    check(info.count > 0 && info.withAv === info.count, `${path} 模型徽章带头像`, `${info.count}个/${info.withAv}带头像`);
    check(info.rawCells === 0, `${path} 无纯文字模型单元格`, String(info.rawCells));
    check(info.names.every(function(n){return n.length > 1;}), `${path} 徽章含名字`, info.names.join(","));
    check(errors.length === 0, `${path} 无 JS 异常`, errors.join("; ").slice(0, 80));
  }
  check(totalBadges > 10, "全局徽章总量", String(totalBadges));

  const failed = results.filter((x) => !x).length;
  console.log(failed === 0 ? "\nALL MODEL BADGE CHECKS PASSED" : `\n${failed} FAILED`);
  process.exitCode = failed === 0 ? 0 : 1;
} catch (e) { console.error("ERROR", e.message); process.exitCode = 1; }
finally { try { ws?.close(); } catch {} chrome.kill(); }
