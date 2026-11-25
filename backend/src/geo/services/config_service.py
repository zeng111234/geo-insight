"""Validation and composition for the configuration surface.

Handlers stay thin (Constitution V): parse request, call this module, format
response. Everything about "3 samples per core question per model per day"
lives here, not in the HTTP layer.
"""

import json
import sqlite3

from geo.domain import relevance
from geo.repositories import config_repository as config
from geo.repositories import sqlite_repository as repo
from geo.settings import settings


def _models(plan: dict | None) -> list[str]:
    if not plan or not plan.get("model_keys"):
        return []
    try:
        values = json.loads(plan["model_keys"])
    except (TypeError, ValueError):
        return []
    return [str(v) for v in values]


def create(conn: sqlite3.Connection, tenant_id: str, payload: dict, now: str) -> dict:
    name = (payload.get("name") or "").strip()
    brand_name = (payload.get("brand_name") or "").strip()
    problems = []
    if not name:
        problems.append("name 必填")
    if not brand_name:
        problems.append("brand_name 必填")

    aliases = payload.get("brand_aliases") or payload.get("aliases") or []
    competitors = payload.get("competitor_names") or payload.get("competitors") or []
    for key, values in (("brand_aliases", aliases), ("competitor_names", competitors)):
        if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
            problems.append(f"{key} 必须是字符串数组")
    if len(aliases) > 20:
        problems.append("brand_aliases 最多 20 个")
    if len(competitors) > 20:
        problems.append("competitor_names 最多 20 个")

    # 官网地址可选：存进 tracker.settings，不新增列。只做最基本的结构校验——
    # 这里不是 URL 解析器，挡住明显不是链接的串即可，别拦合法的中文域名。
    website = (payload.get("website") or "").strip()
    if website:
        if not isinstance(website, str):
            problems.append("website 必须是字符串")
        elif len(website) > 500:
            problems.append("website 最多 500 字符")
        elif not (website.startswith("http://") or website.startswith("https://")):
            problems.append("website 必须以 http:// 或 https:// 开头")
    if problems:
        raise ValueError("；".join(problems))

    settings_json = json.dumps({"website": website}, ensure_ascii=False) if website else "{}"
    return config.create_tracker(
        conn,
        tenant_id=tenant_id,
        name=name,
        brand_name=brand_name,
        industry=(payload.get("industry") or "").strip() or None,
        region=(payload.get("region") or "").strip() or None,
        aliases=aliases,
        competitors=competitors,
        owner_id=payload.get("owner_id"),
        now=now,
        settings_json=settings_json,
    )


def sampling_overview(
    conn: sqlite3.Connection, tracker_id: str, start: str | None = None, end: str | None = None
) -> dict:
    plan = config.latest_plan(conn, tracker_id)
    questions = config.list_questions(conn, tracker_id)["summary"]
    models = _models(plan)
    core_times = plan["core_times"] if plan else 3
    long_tail_times = plan["long_tail_times"] if plan else 1

    core_daily = questions["core"] * core_times * max(len(models), 0)
    long_tail_daily = questions["long_tail"] * long_tail_times * max(len(models), 0)

    failure = config.failure_stats(conn, tracker_id, start, end)
    threshold = settings.sampling_failure_threshold
    if failure["attempted"] == 0:
        failure_status = "insufficient_data"
    elif failure["rate"] is not None and failure["rate"] > threshold:
        failure_status = "over_threshold"
    else:
        failure_status = "ok"

    notes = []
    if not plan:
        notes.append("该监控对象还没有采样计划，按默认 3/1 预估")
    if not models:
        notes.append("采样计划未配置模型，按 0 计")
    if questions["total"] == 0:
        notes.append("尚未导入问题，采样计划为 0")

    return {
        "tracker_id": tracker_id,
        "channel": plan["channel"] if plan else None,
        "models": models,
        "core_times": core_times,
        "long_tail_times": long_tail_times,
        "timezone": plan["timezone"] if plan else "Asia/Shanghai",
        "effective_from": plan["effective_from"] if plan else None,
        "questions": questions,
        "daily_planned": core_daily + long_tail_daily,
        "breakdown": {
            "core_questions": questions["core"],
            "core_daily": core_daily,
            "long_tail_questions": questions["long_tail"],
            "long_tail_daily": long_tail_daily,
            "model_count": len(models),
        },
        "failure": {**failure, "threshold": threshold, "status": failure_status},
        "notes": notes,
    }


def tracker_detail(conn: sqlite3.Connection, tracker_id: str, tenant_id: str) -> dict | None:
    tracker = repo.get_tracker(conn, tracker_id, tenant_id)
    if not tracker:
        return None
    # H2-6:核心词属于生效问题集(出参统一成数组,前端直接用)
    from geo.services import tracker_service

    tracker["keywords"] = tracker_service.active_keywords(conn, tracker_id)
    # settings 列存的是 JSON 字符串；前端要读 website，这里解析成结构化字段。
    try:
        extra = json.loads(tracker.get("settings") or "{}")
    except (TypeError, ValueError):
        extra = {}
    tracker["website"] = extra.get("website", "") if isinstance(extra, dict) else ""
    tracker["question_summary"] = repo.question_summary(conn, tracker_id)
    profile = config.get_brand_profile(conn, tracker_id, tenant_id) or {}
    tracker["brand"] = {
        "id": profile.get("id"),
        "name": profile.get("name"),
        "industry": profile.get("industry"),
        "region": profile.get("region"),
        "aliases": profile.get("aliases", []),
    }
    competitors = config.list_competitors(conn, tracker_id)
    tracker["competitors"] = competitors
    tracker["sampling"] = sampling_overview(conn, tracker_id)
    first_day, last_day = repo.data_date_range(conn, tracker_id)
    # 契约里 data_range 是对象；仓储层返回的是 tuple，直接塞进去会序列化成数组，
    # 前端读 data_range.first_day 就静默变成「无」。
    tracker["data_range"] = {"first_day": first_day, "last_day": last_day}
    return tracker
