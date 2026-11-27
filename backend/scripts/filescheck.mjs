// 客户资料归档页 CDP E2E：真登录 → 打开 /files → CDP 塞真PNG → 上传 → 缩略图出现 → 删除。
// 遵守项目规则：默认鉴权环境自动登录、浏览器内 fetch 带 cookie、异常可读。
// 用法：GEO_BASE=http://127.0.0.1:8149 GEO_IMAGE=/tmp/geo-test-photo.png node filescheck.mjs
import { spawn } from "node:child_process";
import { existsSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
const BASE = process.env.GEO_BASE || "http://127.0.0.1:8149";
// 用 Node 的 tmpdir()（Windows 真实路径），Chrome 才能读到；Git Bash 的 /tmp 不行。
const IMAGE = join(tmpdir(), "geo-filescheck-test.png");
writeFileSync(IMAGE, Buffer.from("89504e470d0a1a0a" + "00".repeat(32), "hex"));
const TRACKER = process.env.GEO_TRACKER || "d3aae9ce-cb61-468a-9ab2-51d2f4d28bf1";
const CHROME = ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"].filter(existsSync)[0];
const PORT = Number(process.env.GEO_CDP_PORT || 9461);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--no-sandbox", `--remote-debugging-port=${PORT}`, `--user-data-dir=${process.env.TEMP}/geo-files-cdp-${Date.now()}`, "about:blank"], { stdio: "ignore" });
let id = 0; const pending = new Map(); let ws;
function send(m, p = {}) { const mid = ++id; ws.send(JSON.stringify({ id: mid, method: m, params: p })); return new Promise((res, rej) => pending.set(mid, { resolve: res, reject: rej })); }
async function ev(e) { const r = await send("Runtime.evaluate", { expression: e, returnByValue: true, awaitPromise: true }); if (r.exceptionDetails) throw new Error((r.exceptionDetails.exception?.description || r.exceptionDetails.text || "?").split("\n")[0]); return r.result.value; }
const results = [];
const check = (ok, label, extra = "") => { results.push(ok); console.log(`${ok ? "PASS" : "FAIL"}  ${label}${extra ? ` (${extra})` : ""}`); };
try {
  let target;
  for (let i = 0; i < 60; i++) { try { const l = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json(); target = l.find((t) => t.type === "page"); if (target) break; } catch {} await sleep(250); }
  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { const p = pending.get(m.id); pending.delete(m.id); p.resolve(m.result); } };
  await send("Page.enable"); await send("Runtime.enable");
  await send("Page.navigate", { url: `${BASE}/files` });
  await sleep(1800);
  if (await ev("location.pathname") === "/login") {
    await ev(`(async()=>{var r=await fetch('/login',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams({email:'demo@example.com',password:'geo-demo-2026',next:'/files'}).toString()});location.href='/files?tracker='+${JSON.stringify(TRACKER)};})()`);
    await sleep(1800);
  }
  check(await ev("location.pathname") === "/files", "打开资料归档页（已登录）", await ev("location.pathname"));
  // 选中惠策对象
  await ev(`(function(){var s=document.getElementById('tracker');if(s){s.value=${JSON.stringify(TRACKER)};s.dispatchEvent(new Event('change'));}})()`);
  await sleep(1000);
  const before = await ev(`document.querySelectorAll('#gallery img').length`);
  check(before === 0, "上传前无资料", `${before} 项`);

  const doc = await send("DOM.getDocument");
  const node = await send("DOM.querySelector", { nodeId: doc.root.nodeId, selector: "#file" });
  check(!!node.nodeId, "找到 file input", `${node.nodeId}`);
  await send("DOM.setFileInputFiles", { nodeId: node.nodeId, files: [IMAGE] });
  await ev(`document.getElementById('category').value='portrait';document.getElementById('upload').click()`);
  let after = 0;
  for (let i = 0; i < 40; i++) { await sleep(250); after = await ev(`document.querySelectorAll('#gallery img').length`); if (after >= 1) break; }
  check(after >= 1, "真浏览器上传后缩略图出现", `${after} 项`);
  const state = await ev(`document.getElementById('upState').textContent`);
  check(/上传成功/.test(state), "上传状态提示", state);
  const meta = await ev(`document.getElementById('assetMeta').textContent`);
  check(/[1-9]/.test(meta), "清单计数更新", meta);

  // 删除
  await ev(`(function(){var b=document.querySelector('#gallery [data-del]');if(b){window.confirm=function(){return true};b.click()}})()`);
  let gone = -1;
  for (let i = 0; i < 40; i++) { await sleep(250); gone = await ev(`document.querySelectorAll('#gallery img').length`); if (gone === 0) break; }
  check(gone === 0, "删除后缩略图消失", `${gone} 项`);

  const passed = results.filter(Boolean).length;
  console.log(`\n${passed}/${results.length} checks passed`);
  if (passed !== results.length) process.exitCode = 1;
} catch (e) {
  console.error("E2E 异常:", e.message);
  process.exitCode = 1;
} finally {
  chrome.kill("SIGKILL");
}
