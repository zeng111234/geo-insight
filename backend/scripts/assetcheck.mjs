// 客户资料归档 API E2E：真登录 → CSRF → 上传真PNG → 列表/清单/下载/删除。
// 跑在隔离实例（临时库），不碰 8123 / 主库。
// 用法：GEO_BASE=http://127.0.0.1:8149 GEO_TRACKER=<id> node assetcheck.mjs
const BASE = process.env.GEO_BASE || "http://127.0.0.1:8149";
const PASSWORD = process.env.GEO_PASSWORD || "geo-demo-2026";
const EMAIL = process.env.GEO_EMAIL || "demo@example.com";
const TRACKER = process.env.GEO_TRACKER || "d3aae9ce-cb61-468a-9ab2-51d2f4d28bf1";
const PNG = Buffer.from("89504e470d0a1a0a" + "00".repeat(32), "hex");

const results = [];
const check = (ok, label, extra = "") => {
  results.push(ok);
  console.log(`${ok ? "PASS" : "FAIL"}  ${label}${extra ? ` (${extra})` : ""}`);
};
let cookie = "", csrf = "";
async function req(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (cookie) headers.Cookie = cookie;
  const r = await fetch(BASE + path, { ...opts, headers, redirect: "manual" });
  const sc = r.headers.getSetCookie ? r.headers.getSetCookie() : [];
  for (const c of sc) {
    const pair = c.split(";")[0];
    if (pair.startsWith("geo_session=")) cookie = [cookie.split("; ").filter(x => x && !x.startsWith("geo_session=")), pair].flat().filter(Boolean).join("; ");
    if (pair.startsWith("geo_csrf=")) csrf = decodeURIComponent(pair.slice("geo_csrf=".length));
  }
  return r;
}

try {
  check((await req("/health")).status === 200, "隔离实例健康");
  const login = await req("/login", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ email: EMAIL, password: PASSWORD, next: "/" }).toString(),
  });
  check(login.status === 303 && !!csrf, "登录+CSRF", `HTTP ${login.status}`);

  const before = await (await req(`/v1/trackers/${TRACKER}/assets`)).json();
  check(Array.isArray(before.items), "列表可读", `items=${before.items?.length}`);

  const fd = new FormData();
  fd.append("category", "portrait");
  fd.append("file", new Blob([PNG], { type: "image/png" }), "形象照.png");
  const up = await req(`/v1/trackers/${TRACKER}/assets`, {
    method: "POST", headers: { "X-CSRF-Token": csrf }, body: fd,
  });
  check(up.status === 201, "上传 201", `HTTP ${up.status}`);
  const asset = await up.json();
  check(asset.category === "portrait" && asset.filename && !asset.filename.includes("/"), "库里只存纯文件名+正确分类", asset.filename);
  check(asset.original_name === "形象照.png", "保留原始文件名");

  const after = await (await req(`/v1/trackers/${TRACKER}/assets`)).json();
  check(after.items.length === before.items.length + 1, "列表 +1", `${before.items?.length}→${after.items?.length}`);
  const portrait = after.checklist.items.find(x => x.category === "portrait");
  check(portrait.count >= 1 && portrait.target === 10, "清单统计达标口径", `count=${portrait.count}/target=${portrait.target}`);

  const dl = await req(`/v1/trackers/${TRACKER}/assets/${asset.id}`);
  check(dl.status === 200, "下载 200", `HTTP ${dl.status}`);
  const dlBuf = Buffer.from(await dl.arrayBuffer());
  check(dlBuf.equals(PNG), "下载内容与上传一致", `${dlBuf.length}B`);

  const del = await req(`/v1/trackers/${TRACKER}/assets/${asset.id}`, {
    method: "DELETE", headers: { "X-CSRF-Token": csrf },
  });
  check(del.status === 204, "删除 204", `HTTP ${del.status}`);
  const gone = await req(`/v1/trackers/${TRACKER}/assets/${asset.id}`);
  check(gone.status === 404, "删除后查不到", `HTTP ${gone.status}`);

  const passed = results.filter(Boolean).length;
  console.log(`\n${passed}/${results.length} checks passed`);
  if (passed !== results.length) process.exitCode = 1;
} catch (e) {
  console.error("E2E 异常:", e.message);
  process.exitCode = 1;
}
