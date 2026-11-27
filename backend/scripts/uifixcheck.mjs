// UI 修复 E2E：① DeepSeek 头像是鲸鱼 SVG 不是字母 D；② AI 助手回复的 markdown 被渲染。
// 用真浏览器（CDP）打开真页面，注入可控的助手消息验证渲染，不花钱、不依赖 LLM 返回。
// 头像验证走真实 modelBadge()（多个页面共用），markdown 验证走真实 renderMd()+assistantHtml()。
// 用法：node backend/scripts/uifixcheck.mjs [BASE]
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
const BASE = process.argv[2] || "http://127.0.0.1:8123";
const PORT = Number(process.env.GEO_CDP_PORT || 9475);
const CHROME = ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"].filter(existsSync)[0];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--no-sandbox",
  `--remote-debugging-port=${PORT}`, `--user-data-dir=${process.env.TEMP}/geo-uifix-cdp-${Date.now()}`, "about:blank"], { stdio: "ignore" });
async function target() { for (let i = 0; i < 60; i++) { try { const r = await fetch(`http://127.0.0.1:${PORT}/json/list`); const l = await r.json(); const p = l.find((t) => t.type === "page"); if (p) return p.webSocketDebuggerUrl; } catch {} await sleep(250); } throw Error("no cdp"); }
let id = 0; const pending = new Map(); const jsErrors = [];
function send(m, p = {}) { const mid = ++id; ws.send(JSON.stringify({ id: mid, method: m, params: p })); return new Promise((res, rej) => pending.set(mid, { resolve: res, reject: rej })); }
async function ev(e) { const r = await send("Runtime.evaluate", { expression: e, returnByValue: true, awaitPromise: true }); if (r.exceptionDetails) { const d = r.exceptionDetails.exception?.description || r.exceptionDetails.text || "?"; throw new Error("浏览器执行失败: " + String(d).split("\n")[0].slice(0, 160)); } return r.result.value; }
const results = [];
const check = (ok, label, extra = "") => { results.push(ok); console.log(`${ok ? "PASS" : "FAIL"}  ${label}${extra ? ` (${extra})` : ""}`); };
let ws;
try {
  ws = new WebSocket(await target());
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.method === "Runtime.exceptionThrown") { const d = m.params?.exceptionDetails; jsErrors.push(String(d?.exception?.description || d?.text || "js error").split("\n")[0]); } if (m.id && pending.has(m.id)) { const p = pending.get(m.id); pending.delete(m.id); p.resolve(m.result); } };
  await send("Page.enable"); await send("Runtime.enable");

  // 先统一登录（首页引用 app.js，modelBadge 与 renderMd 都在这页验证）
  await send("Page.navigate", { url: BASE + "/" });
  await sleep(2600);
  if (await ev(`location.pathname`) === "/login") {
    await ev(`(function(){document.querySelector('input[name=email]').value='demo@example.com';document.querySelector('input[name=password]').value='geo-demo-2026';document.querySelector('form').submit();return true})()`);
    await sleep(2200);
    await send("Page.navigate", { url: BASE + "/" });
    await sleep(2600);
  }

  // 1) DeepSeek 头像（modelBadge 是 app.js 共用函数，首页引用 app.js）
  check(await ev(`typeof GEO !== 'undefined' && typeof GEO.modelBadge === 'function'`) === true,
    "GEO.modelBadge 已加载", "");
  // GEO.modelBadge 在 app.js 里定义，所有页面引用 app.js
  const badge = await ev(`GEO.modelBadge('deepseek')`);
  check(typeof badge === "string" && badge.indexOf("<svg") >= 0, "DeepSeek 头像用 SVG", String(badge).slice(0, 60));
  check(typeof badge === "string" && !/>D</.test(badge), "头像不再是字母 D", badge.match(/model-badge-av[^>]*>(.{0,20})/)?.[1] || "");
  check(typeof badge === "string" && badge.indexOf("DeepSeek") >= 0, "模型名仍是 DeepSeek", "");
  // 其它模型仍用字母（回退不能被破坏）
  const kb = await ev(`GEO.modelBadge('kimi')`);
  check(typeof kb === "string" && />K</.test(kb), "其它模型仍用字母回退(Kimi K)", String(kb).slice(0, 70));

  // 2) AI 助手 markdown 渲染：注入可控消息，走真实 renderMd（仍在首页）
  const mdResult = await ev(`(function(){
    // renderMd 是 index.html 顶层函数，直接调用
    var html = renderMd('# 标题行\\n普通段落 **加粗** 和 \u0060行内码\u0060\\n- 列表一\\n* 列表二\\n## 小标题');
    var box = document.createElement('div'); box.innerHTML = html; document.body.appendChild(box);
    return {
      html: html,
      hasH4: !!box.querySelector('h4'),
      hasUl: !!box.querySelector('ul'),
      liCount: box.querySelectorAll('li').length,
      hasStrong: !!box.querySelector('strong'),
      hasCode: !!box.querySelector('code'),
      rawHash: /#\\s/.test(box.textContent),
      rawStarLine: /(^|\\n)\\s*[-*]\\s/.test(box.textContent),
      rawDStar: box.textContent.indexOf('**') >= 0,
      textIncludesTitle: box.textContent.indexOf('标题行') >= 0
    };
  })()`);
  check(mdResult.hasH4, "markdown # 标题 → <h4>", "");
  check(mdResult.hasUl && mdResult.liCount === 2, "markdown -/* 列表 → <ul><li>", "li=" + mdResult.liCount);
  check(mdResult.hasStrong, "markdown **加粗 → <strong>", "");
  check(mdResult.hasCode, "markdown `行内码` → <code>", "");
  check(!mdResult.rawHash && !mdResult.rawStarLine && !mdResult.rawDStar, "无残留 # / - / ** 标记", JSON.stringify(mdResult).slice(0, 90));
  check(mdResult.textIncludesTitle, "正文内容没丢", "");

  // 3) XSS 防线：模型输出带 <script> 不能变成真标签
  const xss = await ev(`(function(){
    var html = renderMd('<script>window.__pwned=1<\/script> **粗**');
    var box = document.createElement('div'); box.innerHTML = html; document.body.appendChild(box);
    return { hasScript: !!box.querySelector('script'), pwned: !!window.__pwned, escaped: html.indexOf('&lt;script&gt;') >= 0 };
  })()`);
  check(!xss.hasScript && !xss.pwned && xss.escaped, "LLM 输出的 <script> 被转义不执行", JSON.stringify(xss));

  check(jsErrors.length === 0, "无 JS 异常", jsErrors.slice(0, 1).join(""));
  const failed = results.filter((x) => !x).length;
  console.log(failed === 0 ? "\nALL UI FIX CHECKS PASSED" : `\n${failed} FAILED`);
  process.exitCode = failed === 0 ? 0 : 1;
} catch (e) { console.error("ERROR", e.message); process.exitCode = 1; }
finally { try { ws?.close(); } catch {} chrome.kill(); }
