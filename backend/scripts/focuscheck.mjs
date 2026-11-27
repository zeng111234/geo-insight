// 键盘焦点可见性 E2E（WCAG 2.4.11）：Tab 导航时焦点环必须是实色 accent，
// 不能被 input:focus 的 outline:none 盖掉。用真 Tab 键触发 :focus-visible。
// 用法：先 python backend/run.py，再 node backend/scripts/focuscheck.mjs
// 换端口：GEO_BASE=http://127.0.0.1:8123 node backend/scripts/focuscheck.mjs
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
const BASE = process.env.GEO_BASE || "http://127.0.0.1:8123";
const PORT = Number(process.env.GEO_CDP_PORT || 9468);
const CHROME = ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"].filter(existsSync)[0];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--no-sandbox", `--remote-debugging-port=${PORT}`, `--user-data-dir=${process.env.TEMP}/geo-focus-cdp-${Date.now()}`, "about:blank"], { stdio: "ignore" });
async function target() { for (let i = 0; i < 60; i++) { try { const r = await fetch(`http://127.0.0.1:${PORT}/json/list`); const l = await r.json(); const p = l.find((t) => t.type === "page"); if (p) return p.webSocketDebuggerUrl; } catch {} await sleep(250); } throw Error("no cdp"); }
let id = 0; const pending = new Map(); let ws;
function send(m, p = {}) { const mid = ++id; ws.send(JSON.stringify({ id: mid, method: m, params: p })); return new Promise((res, rej) => pending.set(mid, { resolve: res, reject: rej })); }
async function ev(e) { const r = await send("Runtime.evaluate", { expression: e, returnByValue: true, awaitPromise: true }); if (r.exceptionDetails) throw new Error("浏览器执行失败: " + String((r.exceptionDetails.exception?.description) || r.exceptionDetails.text).split("\n")[0].slice(0, 160)); return r.result.value; }
const results = [];
const check = (ok, label, extra = "") => { results.push(ok); console.log(`${ok ? "PASS" : "FAIL"}  ${label}${extra ? ` (${extra})` : ""}`); };
try {
  ws = new WebSocket(await target());
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { const p = pending.get(m.id); pending.delete(m.id); p.resolve(m.result); } };
  await send("Page.enable"); await send("Runtime.enable");

  await send("Page.navigate", { url: BASE + "/login" });
  await sleep(2500);

  // 用真 Tab 键让浏览器进入 keyboard 模态，触发 :focus-visible
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 });
  await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 });
  await sleep(300);

  // 找到当前焦点元素，读它的样式与 :focus-visible 匹配状态
  const info = await ev(`(function(){
    var el=document.activeElement;
    var cs=getComputedStyle(el);
    return {tag:el.tagName, fv:el.matches(':focus-visible'),
      outlineStyle:cs.outlineStyle, outlineWidth:cs.outlineWidth,
      outlineColor:cs.outlineColor, offset:cs.outlineOffset};
  })()`);
  check(info.tag !== "BODY", "Tab 后有焦点元素", info.tag);
  check(info.fv === true, "键盘导航触发 :focus-visible", String(info.fv));
  check(info.outlineStyle === "solid", "焦点环是实线", info.outlineStyle);
  check(info.outlineWidth === "2px", "焦点环 2px", info.outlineWidth);
  check(info.outlineColor !== "rgba(0, 0, 0, 0)" && !/,\s*0\.\d+\)$/.test(info.outlineColor), "焦点环非透明（实色可见）", info.outlineColor);
  check(info.offset === "2px", "焦点环 offset 2px", info.offset);

  // input 焦点：focus-visible 不应被 outline:none 盖掉
  await ev(`(function(){var el=document.querySelector('input[name=email]');if(el){el.focus();return true}return false})()`);
  // focus() 不触发 focus-visible，再按一次 Tab 键进入键盘模态后 shift+tab 回到 input
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 });
  await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 });
  await sleep(300);
  const inputInfo = await ev(`(function(){
    var el=document.activeElement; var cs=getComputedStyle(el);
    return {tag:el.tagName, type:el.getAttribute('type'), fv:el.matches(':focus-visible'),
      outlineStyle:cs.outlineStyle, outlineWidth:cs.outlineWidth, shadow:cs.boxShadow};
  })()`);
  check(inputInfo.tag === "INPUT" && inputInfo.fv, "input 键盘焦点触发 focus-visible", JSON.stringify(inputInfo).slice(0, 80));
  check(inputInfo.outlineStyle === "solid" && inputInfo.outlineWidth === "2px",
    "input 焦点环没被 outline:none 盖掉", `${inputInfo.outlineStyle}/${inputInfo.outlineWidth}`);

  const failed = results.filter((x) => !x).length;
  console.log(failed === 0 ? "\nALL FOCUS CHECKS PASSED" : `\n${failed} FAILED`);
  process.exitCode = failed === 0 ? 0 : 1;
} catch (e) { console.error("ERROR", e.message); process.exitCode = 1; }
finally { try { ws?.close(); } catch {} chrome.kill(); }
