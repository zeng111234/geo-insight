// AI 写作 API 端到端：真登录 → CSRF → POST generate → 回读草稿。
// 跑在隔离实例（默认 8139，GEO_BASE 可改），不碰 8123 / 主库。
// 用法：GEO_BASE=http://127.0.0.1:8139 node writer_api_eval.mjs

const BASE = process.env.GEO_BASE || "http://127.0.0.1:8139";
const PASSWORD = process.env.GEO_PASSWORD || "geo-demo-2026";
const EMAIL = process.env.GEO_EMAIL || "demo@example.com";
const TRACKER = process.env.GEO_TRACKER || "d3aae9ce-cb61-468a-9ab2-51d2f4d28bf1";

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
  const health = await req("/health");
  check(health.status === 200, "隔离实例健康", `HTTP ${health.status}`);

  const login = await req("/login", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ email: EMAIL, password: PASSWORD, next: "/" }).toString(),
  });
  check(login.status === 303, "登录成功重定向", `HTTP ${login.status}`);
  check(cookie.includes("geo_session="), "会话 cookie 已拿到");
  check(!!csrf, "CSRF token 已拿到");

  const gen = await req(`/v1/trackers/${TRACKER}/articles/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
    body: JSON.stringify({ reference: "客户参考：广州GEO要看可核验案例" }),
  });
  check(gen.status === 201, "generate 接口 201", `HTTP ${gen.status}`);
  const article = await gen.json();
  check(!!article.id && article.title && article.body, "返回真实草稿", article.title || "空");
  check(article.source === "ai_generated_pending_review", "标记为待人工审核", article.source);
  check(article.fact_count >= 1, "带事实条目", `count=${article.fact_count}`);
  check(!!article.raw_uri, "原始响应可回溯", article.raw_uri);

  const stages = await req(`/v1/articles/${article.id}/stages`);
  check(stages.status === 200, "草稿阶段接口可读", `HTTP ${stages.status}`);
  const stageData = await stages.json();
  const items = stageData.items || stageData.stages || stageData;
  check(Array.isArray(items) && items.length === 6, "六阶段齐全", `n=${Array.isArray(items) ? items.length : "?"}`);

  console.log(`\nAI写作API: ${results.filter(Boolean).length}/${results.length} 通过`);
} catch (e) {
  console.error("[x] 异常：", e.message);
  results.push(false);
}
process.exit(results.every(Boolean) ? 0 : 1);
