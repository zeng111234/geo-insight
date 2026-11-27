// P2 行内「截图 / 纠正」的 CDP 真点验收。
//
// 为什么单独脚本：interactioncheck.mjs 打的是 8123（可能是旧进程、没有 P2 的新接口），
// 本脚本打 GEO_BASE（默认 8137）上刚起的新实例，避免误测旧代码、也不打扰隔壁的浏览器检查。
//
// 用法：先起新服务（见脚本尾部说明），再 node backend/scripts/p2check.mjs
// 必须用临时库或记得清理：本脚本写入一条纠正后会 DELETE 掉自己的记录。

import { spawn } from "node:child_process";
import { existsSync } from "node:fs";

const BASE = process.env.GEO_BASE || "http://127.0.0.1:8137";
const PORT = Number(process.env.GEO_CDP_PORT || 9361);
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
  `--user-data-dir=${process.env.TEMP || "/tmp"}/geo-p2check`,
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

// 默认开鉴权：本脚本有 Node fetch 写操作(DELETE correction)不带 cookie 会 401，
// 早点说清楚，别让人对着假 FAIL 猜（同类问题第 4 次）。
const probe = await fetch(`${BASE}/v1/trackers/11111111-1111-1111-1111-111111111111/report`);
if (probe.status === 401) {
  console.log("FAIL  服务开启了鉴权，本脚本的 Node fetch 不带 cookie，会静默失败。");
  console.log("      用 GEO_AUTH_REQUIRED=0 python backend/run.py 启动后再跑本脚本。");
  process.exit(1);
}

let answerId = "";
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
  const errors = [];
  ws.addEventListener("message", (e) => {
    const m = JSON.parse(e.data);
    if (m.method === "Runtime.exceptionThrown") errors.push(m.params.exceptionDetails.text || "exception");
  });

  // 拦下载：记录 created download 名和 href 前缀，不真落盘
  await send("Page.addScriptToEvaluateOnNewDocument", {
    source: `window.__shot = null;
      (function () {
        var orig = HTMLAnchorElement.prototype.click;
        HTMLAnchorElement.prototype.click = function () {
          if (this.download && (this.href || "").startsWith("data:image/png")) {
            window.__shot = { name: this.download, len: this.href.length };
          } else { orig.call(this); }
        };
      })();`,
  });

  // 取一个真实回答 id（用有数据的演示对象与周期）
  const list = await (await fetch(
    `${BASE}/v1/trackers/11111111-1111-1111-1111-111111111111/answers?from=2026-11-01&to=2026-11-03&limit=1`
  )).json();
  answerId = list.items?.[0]?.id || "";
  if (!answerId) throw new Error("取不到回答，无法做 P2 验收");
  // 先清掉可能的历史纠正，保证从"未纠正"开始
  await fetch(`${BASE}/v1/answers/${answerId}/correction`, { method: "DELETE" }).catch(() => {});

  await send("Page.navigate", {
    url: `${BASE}/monitor?tab=answers&tracker=11111111-1111-1111-1111-111111111111&from=2026-11-01&to=2026-11-03`,
  });
  await sleep(3500);

  // 1. 行内按钮真的渲染出来（阅读视图）
  const buttons = await evaluate(`({
    shot: document.querySelectorAll('[data-shot]').length,
    correct: document.querySelectorAll('[data-correct]').length,
    copy: document.querySelectorAll('[data-copy]').length,
  })`);
  check(buttons.shot > 0 && buttons.correct > 0 && buttons.copy > 0,
    "行内渲染截图/纠正/复制三个按钮", JSON.stringify(buttons));

  // 2. 点截图 → 产出 PNG data URL（拦截到下载）
  await evaluate(`document.querySelector('[data-shot]').click()`);
  await sleep(500);
  const shot = await evaluate(`window.__shot`);
  check(shot && shot.name.endsWith(".png") && shot.len > 1000,
    "点截图产出 PNG 图片", shot ? `${shot.name} ${shot.len}B` : "无产物");

  // 3. 点纠正 → 表单弹出，初始从接口回显（当前为空）
  // 记下页面实际点的那条 id：列表顺序可能与 API 第一条不同，清理必须用同一个 id。
  const clickedId = await evaluate(`document.querySelector('[data-correct]').dataset.correct`);
  if (clickedId) answerId = clickedId;
  await evaluate(`document.querySelector('[data-correct]').click()`);
  await sleep(600);
  const form = await evaluate(`({
    open: !!document.querySelector('.correction-form'),
    answer: document.querySelector('.correction-form')?.dataset.answer || '',
  })`);
  check(form.open, "点纠正弹出表单");

  // 4. 填表并保存 → 后端写入 + 前端回显"已纠正"
  await evaluate(`(function () {
    var b = document.querySelector('.correction-form');
    b.querySelector('[data-field=own_mentioned]').value = '1';
    b.querySelector('[data-field=sentiment]').value = 'pos';
    b.querySelector('[data-field=note]').value = 'CDP 真点验收';
    document.querySelector('.correction-form [data-act=save]').click();
  })()`);
  await sleep(1600);
  const saved = await evaluate(`({
    tag: Array.from(document.querySelectorAll('.tag.corrected')).length,
    formGone: !document.querySelector('.correction-form'),
  })`);
  check(saved.tag > 0 && saved.formGone, "保存纠正后列表回显已纠正", JSON.stringify(saved));

  // 5. 刷新页面 → 回显还在（真落库，不是前端状态）
  await send("Page.navigate", {
    url: `${BASE}/monitor?tab=answers&tracker=11111111-1111-1111-1111-111111111111&from=2026-11-01&to=2026-11-03`,
  });
  await sleep(3200);
  const afterReload = await evaluate(`document.querySelectorAll('.tag.corrected').length`);
  check(afterReload > 0, "刷新后纠正回显仍在（真落库）", `标记数=${afterReload}`);

  // 6. 再打开纠正表单 → 读回上次填的值（写入并回显完整闭环）
  await evaluate(`document.querySelector('[data-correct]').click()`);
  await sleep(700);
  const echoed = await evaluate(`({
    note: document.querySelector('.correction-form [data-field=note]')?.value || '',
    own: document.querySelector('.correction-form [data-field=own_mentioned]')?.value || '',
  })`);
  check(echoed.note === "CDP 真点验收" && echoed.own === "1",
    "再打开纠正表单回显上次写入值", JSON.stringify(echoed));

  // 7. 取消不改数据
  await evaluate(`document.querySelector('.correction-form [data-act=cancel]').click()`);
  await sleep(300);
  const cancelled = await evaluate(`!document.querySelector('.correction-form')`);
  check(cancelled, "取消纠正关闭表单且不写入");

  check(errors.length === 0, "全程无 JS 异常", errors.join(" | "));
} catch (e) {
  console.error("[x] P2 验收异常：", e.message);
  results.push(false);
} finally {
  // 清理：删掉自己写入的纠正并**回读确认**。
  // 第一版只发 DELETE 没 await 结果，进程退出导致请求没落地——
  // 声称"清理了"但库里还留着测试纠正，这种假清理比不清理更糟。
  if (answerId) {
    try {
      await fetch(`${BASE}/v1/answers/${answerId}/correction`, { method: "DELETE" });
      const after = await (await fetch(`${BASE}/v1/answers/${answerId}/correction`)).json();
      if (after.correction) {
        console.error(`[x] 清理失败：${answerId} 还有纠正记录，请手工删除`);
        process.exitCode = 1;
      }
    } catch (e) {
      console.error(`[x] 清理异常：${e.message}，请手工删除 ${answerId} 的纠正`);
      process.exitCode = 1;
    }
  }
  chrome.kill("SIGKILL");
}

const pass = results.filter(Boolean).length;
console.log(`\nP2: ${pass}/${results.length} 通过`);
if (pass !== results.length) process.exitCode = 1;
// 不清零：finally 里若清理失败已设 exitCode=1，不能用 success 覆盖掉它
process.exit(process.exitCode || 0);
