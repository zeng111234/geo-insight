// A3 壳的 CDP 真点检查：积分徽章读真账本 + AI 助手抽屉诚实标注未接入。
// browsercheck/pagecheck 只验静态 DOM，这里真点按钮、真读 API 对账，守住
// 「徽章不是写死数字、抽屉不是假生成入口」这两条 A3 硬约束。
// 用法：先 python backend/run.py，再 node backend/scripts/p5check.mjs
// 换端口：GEO_BASE=http://127.0.0.1:8129 node backend/scripts/p5check.mjs
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
const BASE = process.env.GEO_BASE || "http://127.0.0.1:8123";
const PORT = Number(process.env.GEO_CDP_PORT || 9395);
const CHROME = [
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
].filter(existsSync)[0];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--no-sandbox",
  `--remote-debugging-port=${PORT}`, `--user-data-dir=${process.env.TEMP}/geo-p5-cdp`, "about:blank"],
  { stdio: "ignore" });
async function target() {
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/json/list`);
      const list = await r.json();
      const page = list.find((t) => t.type === "page");
      if (page) return page.webSocketDebuggerUrl;
    } catch {}
    await sleep(250);
  }
  throw new Error("no CDP page");
}
let id = 0; const pending = new Map(); let ws;
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
const results = [];
const check = (ok, label, extra = "") => { results.push(ok); console.log(`${ok ? "PASS" : "FAIL"}  ${label}${extra ? ` (${extra})` : ""}`); };
try {
  const apiBalance = (await (await fetch(BASE + "/v1/credits")).json()).balance;

  ws = new WebSocket(await target());
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) {
      const p = pending.get(m.id); pending.delete(m.id);
      m.error ? p.reject(new Error(m.error.message)) : p.resolve(m.result);
    }
  };
  await send("Page.enable"); await send("Runtime.enable");
  const errors = [];
  ws.addEventListener("message", (e) => {
    const m = JSON.parse(e.data);
    if (m.method === "Runtime.exceptionThrown") errors.push(m.params.exceptionDetails.text || "exception");
  });

  await send("Page.navigate", { url: `${BASE}/monitor` });
  await sleep(3000);
  const badge = await evaluate(`document.getElementById('globalCreditBalance').textContent`);
  check(badge === apiBalance + " 分", "徽章余额 = API 真账本", `${badge} vs ${apiBalance} 分`);

  const btnText = await evaluate(`(document.getElementById('globalAiButton')||{}).textContent || ''`);
  check(btnText === "AI 助手", "顶部栏有 AI 助手按钮", btnText);

  await evaluate(`document.getElementById('globalAiButton').click()`);
  await sleep(300);
  const drawerOpen = await evaluate(`document.getElementById('aiDrawer').classList.contains('show')`);
  check(drawerOpen, "点按钮打开抽屉");
  const drawerText = await evaluate(`document.getElementById('aiDrawer').innerText`);
  check(drawerText.includes("需 key") && drawerText.includes("未接入"), "抽屉明写未接入",
    drawerText.replace(/\n/g, " ").slice(0, 70));
  check(!/生成草稿|立即分析|开始分析/.test(drawerText), "抽屉无假生成动作");
  const hasForm = await evaluate(`document.querySelectorAll('#aiDrawer form, #aiDrawer [type=submit]').length`);
  check(hasForm === 0, "抽屉无表单/提交", String(hasForm));

  // 关闭后点徽章，验流水抽屉只读真账本（空账本也应显示"暂无流水"而非编造）
  await evaluate(`document.getElementById('aiDrawerClose').click()`);
  await sleep(200);
  await evaluate(`document.getElementById('globalCreditBadge').click()`);
  await sleep(500);
  const ledgerText = await evaluate(`document.getElementById('globalCreditList').innerText`);
  check(ledgerText.length > 0 && !/NaN|undefined|加载中/.test(ledgerText), "积分流水抽屉显示真值或空态",
    ledgerText.slice(0, 40));

  // 打印/详情页不该有按钮
  await send("Page.navigate", { url: `${BASE}/pilot` });
  await sleep(2000);
  const pilotBtn = await evaluate(`!!document.getElementById('globalAiButton')`);
  check(!pilotBtn, "打印页无 AI 助手按钮");

  check(errors.length === 0, "全程无 JS 异常", errors.join("; "));
  const failed = results.filter((x) => !x).length;
  console.log(failed === 0 ? "\nALL P5 E2E CHECKS PASSED" : `\n${failed} FAILED`);
  process.exitCode = failed === 0 ? 0 : 1;
} catch (e) {
  console.error("ERROR", e.message); process.exitCode = 1;
} finally {
  try { ws?.close(); } catch {}
  chrome.kill();
}
