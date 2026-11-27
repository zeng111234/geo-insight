// AI 生成草稿按钮 E2E：真点按钮 → 真调 LLM → 草稿载入编辑器 + 诚实提示。
// 后端 ffedaa5 已落地 POST /trackers/{id}/articles/generate，前端按钮此前没接。
// 用法：先 python backend/run.py（需 DeepSeek key），再 node backend/scripts/aigenbuttoncheck.mjs
// 换端口：GEO_BASE=http://127.0.0.1:8138 node backend/scripts/aigenbuttoncheck.mjs
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
const BASE = process.env.GEO_BASE || "http://127.0.0.1:8123";
const PORT = Number(process.env.GEO_CDP_PORT || 9434);
const CHROME = ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"].filter(existsSync)[0];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--no-sandbox", `--remote-debugging-port=${PORT}`, `--user-data-dir=${process.env.TEMP}/geo-aigen-cdp-${Date.now()}`, "about:blank"], { stdio: "ignore" });
async function target() { for (let i = 0; i < 60; i++) { try { const r = await fetch(`http://127.0.0.1:${PORT}/json/list`); const l = await r.json(); const p = l.find((t) => t.type === "page"); if (p) return p.webSocketDebuggerUrl; } catch {} await sleep(250); } throw Error("no cdp"); }
let id = 0; const pending = new Map(); let ws;
function send(m, p = {}) { const mid = ++id; ws.send(JSON.stringify({ id: mid, method: m, params: p })); return new Promise((res, rej) => pending.set(mid, { resolve: res, reject: rej })); }
async function ev(e) { const r = await send("Runtime.evaluate", { expression: e, returnByValue: true, awaitPromise: true }); if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 200)); return r.result.value; }
const results = [];
const check = (ok, label, extra = "") => { results.push(ok); console.log(`${ok ? "PASS" : "FAIL"}  ${label}${extra ? ` (${extra})` : ""}`); };
const count = async () => (await (await fetch(`${BASE}/v1/trackers/11111111-1111-1111-1111-111111111111/articles`)).json()).items?.length ?? -1;
try {
  ws = new WebSocket(await target());
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { const p = pending.get(m.id); pending.delete(m.id); p.resolve(m.result); } };
  await send("Page.enable"); await send("Runtime.enable");
  const errors = [];
  ws.addEventListener("message", (e) => { const m = JSON.parse(e.data); if (m.method === "Runtime.exceptionThrown") errors.push(m.params.exceptionDetails.text || "exception"); });

  await send("Page.navigate", { url: BASE + "/studio" });
  await sleep(3000);
  const before = await count();

  // 打开新建面板并选一个问题
  await ev(`document.getElementById('newArticle').click()`);
  await sleep(1200);
  const selOk = await ev(`(function(){var s=document.getElementById('newQuestion');
    if(s.options.length>1){s.selectedIndex=1;return true}return false})()`);
  check(selOk, "问题下拉可选", String(selOk));

  // 真点 AI 生成按钮（真调 LLM，可能几秒）
  await ev(`document.getElementById('aiGenerate').click()`);
  // 等生成完成：hint 清空 + 文章列表增加
  let loaded = null;
  for (let i = 0; i < 24; i++) {
    await sleep(1500);
    loaded = await ev(`(function(){
      var hint=document.getElementById('aiHint').textContent;
      var panel=document.getElementById('newPanel');
      var title=(document.getElementById('draftMeta')||{}).textContent||'';
      return {hint:hint, panelHidden:panel.style.display==='none', draftMeta:title};
    })()`);
    const after = await count();
    if (after === before + 1 && loaded.panelHidden) break;
  }
  const after = await count();
  check(after === before + 1, "AI 生成后文章数+1", `${before}→${after}`);
  check(loaded && loaded.panelHidden, "新建面板收起（草稿已载入）", JSON.stringify(loaded).slice(0, 80));
  const editorTitle = await ev(`(document.querySelector('#editorTitle')||{}).value || (document.getElementById('draftMeta')||{}).textContent || ''`);
  check(!!editorTitle, "编辑器有内容", String(editorTitle).slice(0, 50));
  check(errors.length === 0, "全程无 JS 异常", errors.join("; ").slice(0, 100));

  const failed = results.filter((x) => !x).length;
  console.log(failed === 0 ? "\nALL AI GEN BUTTON CHECKS PASSED" : `\n${failed} FAILED`);
  process.exitCode = failed === 0 ? 0 : 1;
} catch (e) { console.error("ERROR", e.message); process.exitCode = 1; }
finally { try { ws?.close(); } catch {} chrome.kill(); }
