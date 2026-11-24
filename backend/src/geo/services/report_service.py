"""Report building (FR-027 ~ FR-031).

The payload is computed once and frozen: reports never re-read live data, so a
number seen today is the same number seen in 30 days. Comparability is decided
before any delta is produced — when the two windows are not on the same basis,
``delta_pp`` stays ``None`` instead of being shown with a caveat.
"""

import hashlib
import json
import sqlite3
from datetime import date, timedelta

from geo.repositories import config_repository as config
from geo.repositories import report_repository as reports
from geo.repositories import sqlite_repository as repo
from geo.services import metric_service
from geo.settings import settings

MODEL_LABELS = {"doubao": "豆包", "deepseek": "DeepSeek", "kimi": "Kimi", "yuanbao": "腾讯元宝"}


def _label(model_key: str) -> str:
    return MODEL_LABELS.get(model_key, model_key)


def _hash(payload: dict) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _period_days(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days + 1


def previous_window(start: str, end: str) -> tuple[str, str]:
    days = _period_days(start, end)
    prev_end = date.fromisoformat(start) - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days - 1)
    return prev_start.isoformat(), prev_end.isoformat()


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _conclusions(payload: dict) -> list[dict]:
    cards = payload["cards"]
    sample = payload["sample"]
    items: list[dict] = []

    rate = cards["mention_rate"]
    if rate["status"] != "ok":
        items.append(
            {"level": "warn", "text": f"本期有效样本 {sample['valid_total']} 条，低于 3 条，不给提及率结论。"}
        )
    else:
        items.append(
            {
                "level": "info",
                "text": f"提及率 {_pct(rate['value'])}（提及 {sample['mention_total']} / 有效 {rate['sample_base']}）。",
            }
        )

    rank = cards["avg_rank"]
    if rank["status"] == "ok":
        items.append(
            {
                "level": "info",
                "text": f"平均排名第 {rank['value']:.1f} 位，基于 {rank['sample_base']} 条带推荐列表的提及样本。",
            }
        )
    else:
        items.append(
            {"level": "warn", "text": f"带推荐列表的提及样本 {rank['sample_base']} 条，不足 3 条，平均排名不成立。"}
        )

    no_list = payload["sample"]["no_list_samples"]
    mentioned = payload["sample"]["mention_total"]
    if mentioned:
        share = no_list / mentioned
        if share > settings.no_list_alert_threshold:
            items.append(
                {
                    "level": "warn",
                    "text": f"提及样本中 {_pct(share)} 来自无推荐列表的答案，平均排名只代表其余样本。",
                }
            )

    citations = payload["citations"]
    unknown = citations.get("unknown_published_share")
    if unknown is not None and unknown > 0.5:
        items.append(
            {"level": "warn", "text": f"信源发布时间未知占 {_pct(unknown)}，时间分布不作结论。"}
        )
    if citations.get("top_media"):
        first = citations["top_media"][0]
        items.append(
            {
                "level": "info",
                "text": f"被引最多的来源是 {first['media_name']}（{first['count']} 次，占全部引用的 {_pct(first['share'])}）。",
            }
        )

    comparison = payload["comparison"]
    if comparison["comparable"] and comparison["delta_pp"] is not None:
        direction = "上升" if comparison["delta_pp"] > 0 else ("下降" if comparison["delta_pp"] < 0 else "持平")
        items.append(
            {
                "level": "info",
                "text": f"与上一周期相比，提及率{direction} {abs(comparison['delta_pp']):.1f} 个百分点。",
            }
        )
    elif comparison["incomparable_reasons"]:
        items.append(
            {
                "level": "warn",
                "text": "与上一周期不可比：" + "；".join(comparison["incomparable_reasons"]) + "。不输出涨跌幅。",
            }
        )
    return items


def build(
    conn: sqlite3.Connection,
    tracker_id: str,
    tenant_id: str,
    start: str,
    end: str,
    now: str,
) -> dict | None:
    tracker = repo.get_tracker(conn, tracker_id, tenant_id)
    if not tracker:
        return None

    overview = metric_service.build_overview(conn, tracker_id, start, end, None, None)
    trend = metric_service.build_trend(conn, tracker_id, start, end, None, None)
    citations = metric_service.build_citation_summary(conn, tracker_id, start, end, None, None)

    models = repo.models_in_period(conn, tracker_id, start, end)
    model_set_hash = hashlib.sha256(",".join(sorted(models)).encode()).hexdigest()[:16]
    competitors = config.list_competitors(conn, tracker_id)

    prev_start, prev_end = previous_window(start, end)
    prev_overview = metric_service.build_overview(conn, tracker_id, prev_start, prev_end, None, None)
    prev_models = repo.models_in_period(conn, tracker_id, prev_start, prev_end)

    reasons: list[str] = []
    if prev_overview["valid_total"] == 0:
        reasons.append(f"上一周期（{prev_start} ~ {prev_end}）无有效样本")
    if models and prev_models and set(models) != set(prev_models):
        reasons.append(
            "模型集合不一致：本期 " + "、".join(_label(m) for m in models)
            + "，上期 " + "、".join(_label(m) for m in prev_models)
        )
    if overview["valid_total"] < settings.min_samples:
        reasons.append(f"本期有效样本 {overview['valid_total']} 条，低于 {settings.min_samples} 条")
    if 0 < prev_overview["valid_total"] < settings.min_samples:
        reasons.append(f"上一周期有效样本 {prev_overview['valid_total']} 条，低于 {settings.min_samples} 条")

    earlier = reports.previous_report(conn, tracker_id, tenant_id, start)
    if earlier:
        if earlier["metric_version"] != settings.metric_version:
            reasons.append(
                f"口径版本不一致：本期 {settings.metric_version}，上期报告 {earlier['metric_version']}"
            )
        if (
            earlier["competitor_set_version"] is not None
            and competitors.get("version") is not None
            and earlier["competitor_set_version"] != competitors["version"]
        ):
            reasons.append(
                f"竞品集合版本不一致：本期 v{competitors['version']}，上期报告 v{earlier['competitor_set_version']}"
            )

    cur_rate = overview["cards"]["mention_rate"]
    prev_rate = prev_overview["cards"]["mention_rate"]
    comparable = not reasons
    delta_pp = None
    if comparable and cur_rate["status"] == "ok" and prev_rate["status"] == "ok":
        delta_pp = round((cur_rate["value"] - prev_rate["value"]) * 100, 1)

    payload = {
        "metric_version": settings.metric_version,
        "generated_at": now,
        "tracker": {
            "id": tracker["id"],
            "name": tracker["name"],
            "brand_name": tracker["brand_name"],
            "industry": tracker["industry"],
            "region": tracker["region"],
        },
        "period": {"from": start, "to": end, "days": _period_days(start, end)},
        "sample": {
            "valid_total": overview["valid_total"],
            "mention_total": overview["cards"]["mention_count"],
            "no_list_samples": overview["no_list_samples"],
        },
        "cards": overview["cards"],
        "trend": trend["points"],
        "citations": citations,
        "models": models,
        "model_set_hash": model_set_hash,
        "competitor_set_version": competitors.get("version"),
        "competitor_names": [c["name"] for c in competitors.get("items", [])],
        "comparison": {
            "previous_period": {"from": prev_start, "to": prev_end},
            "comparable": comparable,
            "incomparable_reasons": reasons,
            "before": {
                "valid_total": prev_overview["valid_total"],
                "mention_rate": prev_rate,
            },
            "after": {"valid_total": overview["valid_total"], "mention_rate": cur_rate},
            "delta_pp": delta_pp,
        },
    }
    payload["conclusions"] = _conclusions(payload)
    return payload


def generate(
    conn: sqlite3.Connection,
    tracker_id: str,
    tenant_id: str,
    start: str,
    end: str,
    now: str,
) -> dict | None:
    payload = build(conn, tracker_id, tenant_id, start, end, now)
    if payload is None:
        return None
    return reports.save_report(
        conn,
        tenant_id=tenant_id,
        tracker_id=tracker_id,
        period_start=start,
        period_end=end,
        metric_version=payload["metric_version"],
        model_set_hash=payload["model_set_hash"],
        competitor_set_version=payload["competitor_set_version"],
        payload=payload,
        payload_hash=_hash(payload),
        created_by=None,
        now=now,
    )
