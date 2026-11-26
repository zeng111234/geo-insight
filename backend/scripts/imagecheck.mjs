// 文章配图的真浏览器交互检查：选文章→选图→上传→预览→取图→删除。
// browsercheck 只验 DOM 渲染，这里用 CDP 真选文件，守住 file input → 上传端点
// → 预览图 → 删除这条只有真浏览器才能走完的链路。
//
// 用法：先 python backend/run.py，再 node backend/scripts/imagecheck.mjs
// 换端口：GEO_BASE=http://127.0.0.1:8124 node backend/scripts/imagecheck.mjs
import { spawn } from "node:child_process";
import { existsSync, writeFileSync, unlinkSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

const BASE = process.env.GEO_BASE || "http://127.0.0.1:8123";
// 1x1 合法 PNG（带正确签名，能过上传端点的文件头校验），自包含生成、用完即删
const IMAGE = join(tmpdir(), "geo-imagecheck-test.png");
writeFileSync(IMAGE, Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
  "base64",
));
const PORT = Number(process.env.GEO_CDP_PORT || 9371);
const CANDIDATES = [
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
].filter(existsSync);
const CHROME = CANDIDATES[0];
if (!CHROME) { console.error("no chrome"); process.exit(1); }

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const chrome = spawn(CHROME, [
  "--headless=new", "--disable-gpu", "--no-sandbox",
  `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${process.env.TEMP}/geo-img-e2e`,
  "about:blank",
], { stdio: "ignore" });

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

try {
  ws = new WebSocket(await target());
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
  await send("DOM.enable");

  const errors = [];
  ws.addEventListener("message", (e) => {
    const m = JSON.parse(e.data);
    if (m.method === "Runtime.exceptionThrown") {
      errors.push(m.params.exceptionDetails.text || "exception");
    }
  });

  await send("Page.navigate", { url: `${BASE}/studio` });
  await sleep(3500);

  // 页面会自动选中第一篇文章；从渲染出的 chip 拿 id
  const articleId = await evaluate(`document.querySelector('.chip[data-id]')?.dataset.id || ''`);
  check(!!articleId, "工作室渲染出文章且自动选中第一篇", articleId);
  if (!articleId) throw new Error("no article selected");

  // 清掉这篇可能已有的图，保证从零开始（鉴权关闭模式下无需 CSRF）
  await evaluate(`
    (async () => {
      const r = await fetch('/v1/articles/' + ${JSON.stringify(articleId)});
      const a = await r.json();
      for (const name of (a.images || [])) {
        await fetch('/v1/articles/' + ${JSON.stringify(articleId)} + '/images/' + encodeURIComponent(name), { method: 'DELETE' });
      }
    })()
  `);
  await sleep(600);
  // 点 chip 触发 selectArticle（脚本内函数，不能直接 evaluate 调）
  await evaluate(`document.querySelector('.chip[data-id=' + CSS.escape(${JSON.stringify(articleId)}) + ']').click()`);
  await sleep(800);

  const before = await evaluate(`document.querySelectorAll('#imageGrid .image-item').length`);
  check(before === 0, "上传前图片网格为空", `${before} 项`);

  // 用 CDP 把真 PNG 塞进 file input（JS 无法直接设 files，必须走 DOM domain）
  const doc = await send("DOM.getDocument");
  const node = await send("DOM.querySelector", { nodeId: doc.root.nodeId, selector: "#imageFile" });
  check(!!node.nodeId, "找到 file input 节点", `${node.nodeId}`);
  await send("DOM.setFileInputFiles", { nodeId: node.nodeId, files: [IMAGE] });

  // change 事件会触发 uploadImages（异步），轮询直到网格出现图片
  let after = 0;
  for (let i = 0; i < 40; i++) {
    await sleep(250);
    after = await evaluate(`document.querySelectorAll('#imageGrid .image-item').length`);
    if (after >= 1) break;
  }
  check(after >= 1, "真浏览器上传后网格出现图片", `${after} 项`);

  const stateText = await evaluate(`document.getElementById('imageState').textContent`);
  check(/张图/.test(stateText) && /抖音/.test(stateText), "图片状态文案更新", stateText);

  // 取图：<img src> 指向的 URL 真能拿到图片字节
  const imgOk = await evaluate(`
    (async () => {
      const img = document.querySelector('#imageGrid .image-item img');
      if (!img) return false;
      const r = await fetch(img.src);
      const buf = await r.arrayBuffer();
      const head = new Uint8Array(buf.slice(0, 8));
      const pngSig = [137,80,78,71,13,10,26,10];
      return r.status === 200 && pngSig.every((b,i) => head[i] === b);
    })()
  `);
  check(imgOk === true, "预览图 URL 返回真 PNG 字节", `${imgOk}`);

  // 删除：点 × 按钮，网格回到空
  await evaluate(`document.querySelector('#imageGrid [data-del-image]').click()`);
  let removed = -1;
  for (let i = 0; i < 40; i++) {
    await sleep(250);
    removed = await evaluate(`document.querySelectorAll('#imageGrid .image-item').length`);
    if (removed === 0) break;
  }
  check(removed === 0, "删除后网格回到空", `${removed} 项`);

  check(errors.length === 0, "全程无 JS 异常", errors.join("; "));

  const failed = results.filter((x) => !x).length;
  console.log(failed === 0 ? "\nALL IMAGE E2E CHECKS PASSED" : `\n${failed} FAILED`);
  process.exitCode = failed === 0 ? 0 : 1;
} catch (e) {
  console.error("ERROR", e.message);
  process.exitCode = 1;
} finally {
  try { ws?.close(); } catch {}
  chrome.kill();
  try { unlinkSync(IMAGE); } catch {}
}
