// 分页组件行为检查：在桩 DOM 里加载真实的 frontend/static/app.js，
// 模拟点击「上一页 / 下一页 / 页码跳转」，断言真的换行、边界禁用、换查询回到第 1 页。
// 用法：node backend/scripts/pagercheck.mjs
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = dirname(dirname(dirname(fileURLToPath(import.meta.url)))); // 项目根：backend/scripts/../../
const src = readFileSync(join(root, "frontend", "static", "app.js"), "utf8");

// --- 最小 DOM 桩：够 app.js 跑起来即可 ---
const clickHandlers = [];
const keyHandlers = [];
const elements = {};
const mockInput = { value: "1", max: "3", getAttribute: () => "test" };
globalThis.location = { protocol: "http:" };
globalThis.window = globalThis;
globalThis.document = {
  addEventListener: (type, fn) => {
    if (type === "click") clickHandlers.push(fn);
    if (type === "keydown") keyHandlers.push(fn);
  },
  getElementById: (id) => elements[id] || (elements[id] = { id, innerHTML: "", value: "", dataset: {} }),
  querySelector: (sel) => (sel && sel.includes("data-pager-go") ? mockInput : null),
  createElement: () => ({ style: {}, setAttribute() {}, appendChild() {} }),
};
eval(src);
const GEO = globalThis.GEO;

const fails = [];
const need = (ok, name, extra) => {
  if (!ok) fails.push(name);
  console.log((ok ? "PASS  " : "FAIL  ") + name + (extra !== undefined && !ok ? " -> " + JSON.stringify(extra) : ""));
};

need(clickHandlers.length === 1 && keyHandlers.length === 1,
  "app.js 注册了点击与键盘各 1 个 document 级委托", { click: clickHandlers.length, key: keyHandlers.length });
need(Boolean(GEO && GEO.pagerSlice && GEO.pagerNav && GEO.pagerInit && GEO.pagerReset), "GEO 暴露 4 个分页函数");

const items = Array.from({ length: 30 }, (_, i) => "row-" + (i + 1));
let draws = 0;
const draw = () => { draws++; return GEO.pagerSlice("test", items, draw); };

// 点击模拟：分页按钮带 data-pager / data-dir，closest 只在真正匹配的选择器上返回节点
const clickDir = (dir, disabled = false) => {
  const btn = {
    disabled,
    getAttribute: (k) => (k === "data-pager" ? "test" : k === "data-dir" ? String(dir) : null),
  };
  clickHandlers[0]({ target: { closest: (sel) => (sel === "[data-pager]" ? btn : null) } });
};

let pg = draw();
need(pg.slice.length === 10 && pg.slice[0] === "row-1" && pg.slice[9] === "row-10",
  "第 1 页是前 10 行", pg.slice);
need(/第 1 \/ 3 页 · 共 30 条/.test(pg.nav), "导航显示 第 1 / 3 页 · 共 30 条", pg.nav);
need(/data-dir="-1" disabled/.test(pg.nav), "第 1 页时「上一页」禁用", pg.nav);

clickDir(1);
pg = draw();
need(pg.page === 2 && pg.slice[0] === "row-11" && pg.slice.length === 10,
  "点「下一页」翻到第 2 页并换行", { page: pg.page, first: pg.slice[0] });
need(draws >= 2, "点击触发了重绘", draws);

clickDir(1);
clickDir(1);
clickDir(1); // 越界：应停在第 3 页
pg = draw();
need(pg.page === 3 && pg.slice[0] === "row-21" && pg.slice.length === 10,
  "连续下一页停在第 3 页（末页）", { page: pg.page, first: pg.slice[0], n: pg.slice.length });
need(/第 3 \/ 3 页/.test(pg.nav) && /data-dir="1" disabled/.test(pg.nav),
  "末页时「下一页」禁用", pg.nav);

clickDir(-1);
pg = draw();
need(pg.page === 2 && pg.slice[0] === "row-11", "点「上一页」回到第 2 页", pg);

GEO.pagerReset("test");
pg = draw();
need(pg.page === 1 && pg.slice[0] === "row-1", "换查询条件后 pagerReset 回到第 1 页", pg.page);

// 变异检查：redraw 回调链里夹带 pagerReset 会让翻页永远停在第 1 页（真实踩坑）。
// 这条断言确认「bug 模式确实翻不动」——如果哪天它能翻页了，说明 pager 组件语义变了。
function drawWithReset() {
  GEO.pagerReset("reseter");
  return GEO.pagerSlice("reseter", items, drawWithReset);
}
let rp = drawWithReset();
const btnNext = { disabled: false, getAttribute: (k) => (k === "data-pager" ? "reseter" : k === "data-dir" ? "1" : null) };
clickHandlers[0]({ target: { closest: (sel) => (sel === "[data-pager]" ? btnNext : null) } });
rp = drawWithReset();
need(rp.page === 1, "变异: redraw 链里夹 reset 时翻页停在第 1 页（bug 模式被锁定）", rp.page);

// 正确写法：redraw 只做切片渲染，reset 只在换查询时调用
function safeDraw() { return GEO.pagerSlice("safe", items, safeDraw); }
let sp = safeDraw();
const btnNext2 = { disabled: false, getAttribute: (k) => (k === "data-pager" ? "safe" : k === "data-dir" ? "1" : null) };
clickHandlers[0]({ target: { closest: (sel) => (sel === "[data-pager]" ? btnNext2 : null) } });
sp = safeDraw();
need(sp.page === 2 && sp.slice[0] === "row-11", "正确写法: redraw 只切片不 reset，翻到第 2 页", sp);

// 客户端分页必须 10 行/页（需求原话「10 个一页」）
const p2 = GEO.pagerSlice("size-test", items, () => {});
need(p2.slice.length === 10, "页大小 = 10 行/页", p2.slice.length);

// 单页数据不显示翻页控件之外还要能正常渲染；空数据不报错
const one = GEO.pagerSlice("small", ["a", "b"], () => {});
need(one.slice.length === 2 && /共 2 条/.test(one.nav), "少量数据仍给出行数信息", one.nav);
const none = GEO.pagerSlice("empty", [], () => {});
need(none.slice.length === 0 && none.nav === "", "空数据不出导航、不报错", none);

// 页码直达：pages > 3 时出现跳转输入框，点击/回车都能跳，越界钳制
const big = Array.from({ length: 100 }, (_, i) => "b" + i);
const bigDraw = () => GEO.pagerSlice("jumper", big, bigDraw);
let bp = bigDraw();
need(/data-pager-go="jumper"/.test(bp.nav) && /data-pager-jump="jumper"/.test(bp.nav),
  "页数 > 3 时出现页码直达输入框与跳转按钮", bp.nav);
const jumpTo = (val) => {
  mockInput.value = String(val);
  mockInput.max = "10";
  const jumpBtn = {
    disabled: false,
    getAttribute: (k) => (k === "data-pager-jump" ? "jumper" : null),
  };
  clickHandlers[0]({ target: { closest: (sel) => (sel === "[data-pager-jump]" ? jumpBtn : null) } });
  return bigDraw();
};
bp = jumpTo(7);
need(bp.page === 7 && bp.slice[0] === "b60", "输入页码 7 点跳转 → 第 7 页", { page: bp.page, first: bp.slice[0] });
bp = jumpTo(999);
need(bp.page === 10, "跳转越界钳制到末页（10）", bp.page);
bp = jumpTo(0);
need(bp.page === 1, "跳转 < 1 钳制到第 1 页", bp.page);
bp = jumpTo(5);
const enterRes = (() => {
  mockInput.value = "5";
  mockInput.max = "10";
  let prevented = false;
  keyHandlers[0]({ key: "Enter", target: mockInput, preventDefault: () => { prevented = true; } });
  return { prevented, page: bigDraw().page };
})();
need(enterRes.prevented && enterRes.page === 5, "输入框按 Enter 跳到第 5 页", enterRes);
const small = GEO.pagerSlice("tiny", items.slice(0, 25), () => {});
need(!/data-pager-go/.test(small.nav), "页数 ≤ 3 时不显示跳转框（不占空间）", small.nav);

console.log(fails.length ? `\n${fails.length} FAILED` : "\nALL PAGER BEHAVIOR CHECKS PASSED");
process.exit(fails.length ? 1 : 0);
