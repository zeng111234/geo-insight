// 事实审核核实 API 端到端：真登录 → CSRF → PATCH 事实 verdict → 回读文章详情确认落库。
// 跑在隔离实例（临时库副本），不碰 8123 / 主库写入。
// 用法：GEO_BASE=http://127.0.0.1:8145 GEO_ARTICLE=<id> GEO_FACT=<id> node factreviewcheck.mjs
const BASE = process.env.GEO_BASE || "http://127.0.0.1:8145";
const PASSWORD = process.env.GEO_PASSWORD || "geo-demo-2026";
const EMAIL = process.env.GEO_EMAIL || "demo@example.com";
const ARTICLE = process.env.GEO_ARTICLE || "";
const FACT = process.env.GEO_FACT || "";

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
  const setCookies = r.headers.getSetCookie ? r.headers.getSetCookie() : [];
  for (const sc of setCookies) {
    const pair = sc.split(";")[0];
    if (pair.startsWith("geo_session=")) {
      cookie = [cookie.split("; ").filter((c) => c && !c.startsWith("geo_session=")), pair].flat().filter(Boolean).join("; ");
    }
    if (pair.startsWith("geo_csrf=")) csrf = decodeURIComponent(pair.slice("geo_csrf=".length));
  }
  return r;
}

try {
  check(!!ARTICLE && !!FACT, "提供了待测文章与事实 ID", `${ARTICLE.slice(0, 8)} / ${FACT.slice(0, 8)}`);
  const health = await req("/health");
  check(health.status === 200, "隔离实例健康", `HTTP ${health.status}`);

  const login = await req("/login", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ email: EMAIL, password: PASSWORD, next: "/" }).toString(),
  });
  check(login.status === 303, "登录成功", `HTTP ${login.status}`);
  check(!!csrf, "CSRF token 已拿到");

  // 核实前：事实应为 pending
  const before = await req(`/v1/articles/${ARTICLE}`);
  check(before.status === 200, "文章详情可读", `HTTP ${before.status}`);
  const beforeDoc = await before.json();
  const beforeFact = (beforeDoc.facts || []).find((f) => f.id === FACT);
  check(!!beforeFact && beforeFact.verdict === "pending", "核实前事实为 pending", beforeFact?.verdict || "缺失");

  // 真核实：PATCH verdict + 来源
  const patch = await req(`/v1/articles/${ARTICLE}/facts/${FACT}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
    body: JSON.stringify({ verdict: "confirmed", source_ref: "https://gov.cn/ai-geo-verified" }),
  });
  check(patch.status === 200, "核实接口 200", `HTTP ${patch.status}`);
  const patched = await patch.json();
  check(patched.verdict === "confirmed", "返回 confirmed", patched.verdict);
  check(patched.source_ref === "https://gov.cn/ai-geo-verified", "返回核实来源", patched.source_ref);

  // 回读：确认真的落库（不是只在响应里改了）
  const after = await req(`/v1/articles/${ARTICLE}`);
  const afterDoc = await after.json();
  const afterFact = (afterDoc.facts || []).find((f) => f.id === FACT);
  check(!!afterFact && afterFact.verdict === "confirmed", "回读事实已 confirmed", afterFact?.verdict || "缺失");
  check(afterFact?.source_ref === "https://gov.cn/ai-geo-verified", "回读来源已落库", afterFact?.source_ref || "空");

  // 非法 verdict 应被拒
  const bad = await req(`/v1/articles/${ARTICLE}/facts/${FACT}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
    body: JSON.stringify({ verdict: "maybe" }),
  });
  check(bad.status === 400, "非法 verdict 被 400 拒绝", `HTTP ${bad.status}`);

  const passed = results.filter(Boolean).length;
  console.log(`\n${passed}/${results.length} checks passed`);
  if (passed !== results.length) process.exitCode = 1;
} catch (e) {
  console.error("E2E 异常:", e.message);
  process.exitCode = 1;
}
