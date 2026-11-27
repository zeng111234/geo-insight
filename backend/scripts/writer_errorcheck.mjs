// AI 写作错误路径 HTTP 验证：越界问题必须在花钱前返回 404，不返回 500、不静默生成。
// 用隔离实例（8139 + 临时库），不碰 8123 / 主库 / 隔壁 UI 测试。
// 用法：GEO_BASE=http://127.0.0.1:8139 node writer_errorcheck.mjs

const BASE = process.env.GEO_BASE || "http://127.0.0.1:8139";
const PASSWORD = process.env.GEO_PASSWORD || "geo-demo-2026";
const EMAIL = process.env.GEO_EMAIL || "demo@example.com";
const TRACKER = process.env.GEO_TRACKER || "d3aae9ce-cb61-468a-9ab2-51d2f4d28bf1";
// 主库里真实存在的、但不属于惠策对象的演示问题 id（辽征试点下的题）。
const FOREIGN_QUESTION = process.env.GEO_FOREIGN_Q || "";

const results = [];
const check = (ok, label, extra = "") => {
  results.push(ok);
  console.log(`${ok ? "PASS" : "FAIL"}  ${label}${extra ? ` (${extra})` : ""}`);
};

let cookie = "";
let csrf = "";
async function req(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (cookie) headers.Cookie = cookie;
  const r = await fetch(BASE + path, { ...opts, headers, redirect: "manual" });
  for (const sc of (r.headers.getSetCookie ? r.headers.getSetCookie() : [])) {
    const pair = sc.split(";")[0];
    if (pair.startsWith("geo_session=")) {
      cookie = [cookie.split("; ").filter((c) => c && !c.startsWith("geo_session=")), pair].flat().filter(Boolean).join("; ");
    }
    if (pair.startsWith("geo_csrf=")) csrf = decodeURIComponent(pair.slice("geo_csrf=".length));
  }
  return r;
}

try {
  const health = await req("/health");
  check(health.status === 200, "隔离实例健康", `HTTP ${health.status}`);

  const login = await req("/login", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ email: EMAIL, password: PASSWORD, next: "/" }).toString(),
  });
  check(login.status === 303 && !!csrf, "登录+CSRF 就绪", `HTTP ${login.status}`);

  // 越界问题：必须 404，且响应体带 NOT_FOUND / 不属于。
  // 若问题 id 未提供，先探测一个不属于本对象的题；探测不到就跳过并如实说明。
  let foreign = FOREIGN_QUESTION;
  if (!foreign) {
    const qs = await (await req(`/v1/trackers/${TRACKER}/questions`)).json();
    const own = new Set((qs.items || []).map((q) => q.id));
    const all = await (await req("/v1/trackers")).json();
    for (const t of (all.items || [])) {
      if (t.id === TRACKER) continue;
      const other = await (await req(`/v1/trackers/${t.id}/questions`)).json();
      const found = (other.items || []).find((q) => !own.has(q.id));
      if (found) { foreign = found.id; break; }
    }
  }
  check(!!foreign, "找到不属于惠策的问题用于越界测试", foreign || "无");
  if (foreign) {
    const before = await (await req(`/v1/trackers/${TRACKER}/articles`)).json();
    const n0 = before.items?.length ?? -1;
    const r = await req(`/v1/trackers/${TRACKER}/articles/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: JSON.stringify({ question_id: foreign, reference: "不该被生成" }),
    });
    const body = await r.json().catch(() => ({}));
    check(r.status === 404, "越界问题返回 404（不是 500）", `HTTP ${r.status}`);
    check(/NOT_FOUND|不属于/.test(String(body.code || "") + String(body.message || "")),
      "错误码可读、不是静默假生成", `${body.code || "?"} ${body.message || ""}`.slice(0, 80));
    const after = await (await req(`/v1/trackers/${TRACKER}/articles`)).json();
    check((after.items?.length ?? -1) === n0, "没留下草稿（花钱前就被拒）", `${n0}→${after.items?.length}`);
  }

  console.log(`\nAI写作错误路径: ${results.filter(Boolean).length}/${results.length} 通过`);
} catch (e) {
  console.error("[x] 异常：", e.message);
  results.push(false);
}
process.exit(results.every(Boolean) ? 0 : 1);
