// 一次性：把「辽宁勋证律师事务所」加入系统监控（走真实 HTTP API，带登录+CSRF）。
// 复用系统既有流程：建对象(自动建采样计划+默认问题集) → 给默认集改名/关键词 → 导入核心/长尾问题
// → 设为生效集 → 设为监控中(active) → 回读验证。
// 两个坑（首跑踩过，本脚本已规避）：
//   1) create_tracker 会自动建一个默认问题集；不要再新建集——导入的问题落到"当前生效集"，
//      新建空集切成生效后问题留在旧集、生效集读出 0 条。
//   2) 新建对象默认 status=draft（页面显示"草稿"）；"加入监控"要显式 PATCH status=active。
// 用法：node backend/scripts/onboard_xunzheng.mjs [BASE]
import { existsSync } from "node:fs";

const BASE = process.argv[2] || "http://127.0.0.1:8123";
const EMAIL = "demo@example.com";
const PASSWORD = "geo-demo-2026";
let cookie = "", csrf = "";

async function req(path, options = {}) {
  const headers = Object.assign({}, options.headers || {});
  if (cookie) headers.Cookie = cookie;
  if (csrf && options.method && options.method !== "GET") headers["X-CSRF-Token"] = csrf;
  if (options.body && typeof options.body !== "string") {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.body);
  }
  const res = await fetch(BASE + path, { ...options, headers, redirect: "manual" });
  const sc = res.headers.getSetCookie ? res.headers.getSetCookie() : [];
  for (const c of sc) {
    if (/^geo_session=/.test(c)) cookie = "geo_session=" + c.match(/geo_session=([^;]+)/)[1];
    if (/^geo_csrf=/.test(c)) csrf = decodeURIComponent(c.match(/geo_csrf=([^;]+)/)[1]);
  }
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { raw: text }; }
  return { status: res.status, data, location: res.headers.get("location") };
}

const ok = (cond, label, extra = "") => {
  console.log(`${cond ? "PASS" : "FAIL"}  ${label}${extra ? ` (${extra})` : ""}`);
  if (!cond) process.exitCode = 1;
};

try {
  // 1) 登录（表单提交，拿 session cookie + csrf）
  const before = await req("/v1/meta/methodology");
  if (before.status === 401) {
    const form = "email=" + encodeURIComponent(EMAIL) + "&password=" + encodeURIComponent(PASSWORD);
    const login = await req("/login", {
      method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: form,
    });
    ok(login.status === 303 || login.status === 302, "登录成功", String(login.status));
    const after = await req("/v1/meta/methodology");
    ok(after.status === 200, "会话可用", String(after.status));
  } else {
    ok(before.status === 200, "免鉴权环境，会话可用", String(before.status));
  }

  // 2) 查重：已存在同名对象就停，不重复建
  const list = await req("/v1/trackers");
  const dup = (list.data.items || []).find((t) => t.name && t.name.indexOf("勋证") >= 0);
  if (dup) {
    console.log("SKIP  已存在勋证对象：" + dup.id + " " + dup.name);
    console.log("（如需重建请先删除该对象，本脚本不覆盖）");
    process.exit(0);
  }

  // 3) 建对象（采样计划随对象自动建，默认 deepseek 3/1）
  const created = await req("/v1/trackers", {
    method: "POST",
    body: {
      name: "辽宁勋证律师事务所",
      brand_name: "辽宁勋证律师事务所",
      industry: "律师事务所",
      region: "沈阳",
      brand_aliases: ["勋证律所", "辽宁勋证", "勋证律师事务所"],
      competitor_names: [],
    },
  });
  ok(created.status === 201, "建对象成功", `${created.status} ${created.data?.message || ""}`);
  const tid = created.data && created.data.id;
  ok(!!tid, "拿到对象 id", String(tid));
  if (!tid) throw Error("没拿到对象 id");

  // 4) 取系统自动建的默认问题集（config_service.create 随对象一起建）。
  //    坑：不要再新建集——导入 problems 会落到"当前生效集"，新建空集切成生效后
  //    问题会留在旧集里、生效集读出来 0 条（onboard 首跑踩过，已修正）。
  const setList = await req(`/v1/trackers/${tid}/question-sets`);
  const setId = (setList.data.items || [])[0]?.id;
  ok(!!setId, "取到自动建的问题集", String(setId));
  const renamed = await req(`/v1/question-sets/${setId}`, {
    method: "PATCH",
    body: { name: "勋证律所 · GEO场景问题", keywords: ["律师事务所", "沈阳律师", "勋证律所"] },
  });
  ok(renamed.status === 200, "问题集改名+关键词", String(renamed.status));

  // 5) 导入问题：核心=品牌/地域专业度提问，长尾=用户真实搜索句。
  //    全部来自用户示例「沈阳专业的律师事务所有哪些 之类的问题」的合理扩展。
  const items = [
    { text: "沈阳专业的律师事务所有哪些", tier: "core" },
    { text: "沈阳律师事务所哪家更专业", tier: "core" },
    { text: "沈阳口碑好的律师事务所推荐", tier: "core" },
    { text: "辽宁勋证律师事务所怎么样", tier: "core" },
    { text: "沈阳刑事辩护律师哪家律所好", tier: "long_tail" },
    { text: "沈阳离婚纠纷律师事务所怎么选", tier: "long_tail" },
    { text: "沈阳劳动仲裁律师事务所有哪些", tier: "long_tail" },
    { text: "沈阳交通事故律师哪家律所专业", tier: "long_tail" },
    { text: "沈阳公司法律师事务所推荐", tier: "long_tail" },
    { text: "沈阳律师咨询去哪家律所比较靠谱", tier: "long_tail" },
  ];
  const imp = await req(`/v1/trackers/${tid}/questions`, { method: "POST", body: { items } });
  ok(imp.status === 201, "导入问题成功", String(imp.status));
  ok(imp.data?.created_count === items.length, "问题全部落库", `${imp.data?.created_count}/${items.length}`);

  // 6) 设为生效集 + 设为监控中(active)
  const activate = await req(`/v1/trackers/${tid}`, {
    method: "PATCH", body: { active_question_set_id: setId },
  });
  ok(activate.status === 200 || activate.status === 201, "设为生效问题集", String(activate.status));
  const active = await req(`/v1/trackers/${tid}`, { method: "PATCH", body: { status: "active" } });
  ok(active.status === 200, "设为监控中", String(active.status));

  // 7) 回读验证：对象/问题集/问题/采样计划
  const detail = await req(`/v1/trackers/${tid}`);
  ok(detail.status === 200 && detail.data?.name === "辽宁勋证律师事务所", "回读对象名", detail.data?.name);
  ok(detail.data?.active_question_set_id === setId, "生效集已切换", detail.data?.active_question_set_id);
  const qs = await req(`/v1/trackers/${tid}/questions`);
  ok(qs.data?.summary?.total === items.length, "回读问题数", JSON.stringify(qs.data?.summary));
  const plan = await req(`/v1/trackers/${tid}/sampling-plan`);
  ok(plan.status === 200 && (plan.data?.models || []).length > 0, "采样计划含模型", JSON.stringify(plan.data?.models));

  console.log("\n对象已建好：" + BASE + "/settings?tracker=" + tid);
  console.log("对象 ID：" + tid);
  console.log("问题集 ID：" + setId);
  console.log("下一步可对该对象跑一轮采集：python backend/scripts/collect.py --tracker " + tid + " --rounds 1");
} catch (e) {
  console.error("ERROR", e.message);
  process.exitCode = 1;
}
