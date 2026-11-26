"""Assembles metric responses.

Rules enforced here (spec FR-015/016/017/018):
- mention rate denominator is valid samples;
- average rank and TopN denominator is samples that actually have a list index;
- sentiment denominator is mention samples, never valid samples;
- each metric independently reports ``insufficient_samples``.
"""

import sqlite3
from datetime import date

from geo.domain import channels as channels_domain
from geo.domain import metrics
from geo.repositories import sqlite_repository as repo
from geo.settings import settings


def build_channel_matrix(
    conn: sqlite3.Connection, tracker_id: str, start: str, end: str
) -> dict:
    """模型 × 端 16 渠道矩阵。

    接入状态和样本数是两件事：未接入的渠道即使库里偶然有样本也照实报数，
    但 status 仍是 not_available —— 不能用一次性的导入数据冒充「采集线通了」。
    """
    counts = {
        (r["model_key"], r["client"]): r
        for r in repo.channel_sample_rows(conn, tracker_id, start, end)
    }
    items: list[dict] = []
    for ch in channels_domain.channel_list():
        row = counts.get((ch["model_key"], ch["client"]))
        samples = int(row["samples"] or 0) if row else 0
        mentions = int(row["mentions"] or 0) if row else 0
        if not ch["available"]:
            status = "not_available"
        elif samples == 0:
            status = "no_samples"
        elif samples < settings.min_samples:
            status = "insufficient_samples"
        else:
            status = "ok"
        items.append(
            {
                **ch,
                "samples": samples,
                "mentions": mentions,
                "mention_rate": round(mentions / samples, 4) if samples else None,
                "status": status,
            }
        )
    return {
        "items": items,
        "total": len(items),
        "available": sum(1 for x in items if x["available"]),
        "unavailable": sum(1 for x in items if not x["available"]),
        "window": {"from": start, "to": end},
    }


def build_overview(
    conn: sqlite3.Connection,
    tracker_id: str,
    start: str,
    end: str,
    model: str | None,
    channel: str | None,
    client: str | None = None,
) -> dict:
    counts = repo.overview_counts(conn, tracker_id, start, end, model, channel, client)
    positions = counts["positions"]
    valid_total = counts["valid_total"]

    return {
        "metric_version": settings.metric_version,
        "period": {"from": start, "to": end},
        "sample_total": counts["sample_total"],
        "valid_total": valid_total,
        "no_list_samples": counts["mention_total"] - len(positions),
        "cards": {
            "mention_count": counts["mention_total"],
            "mention_rate": metrics.mention_rate(counts["mention_total"], valid_total).to_dict(),
            "coverage_rate": metrics.coverage_rate(
                counts["covered_questions"], counts["active_questions"]
            ).to_dict(),
            "avg_rank": metrics.avg_rank(positions).to_dict(),
            "top1_rate": metrics.top_n_rate(positions, 1).to_dict(),
            "top3_rate": metrics.top_n_rate(positions, 3).to_dict(),
            "top10_rate": metrics.top_n_rate(positions, 10).to_dict(),
            "sentiment": metrics.sentiment_distribution(counts["sentiments"]),
        },
    }


def _week_start(day: str) -> str:
    """ISO 周的周一。按周一为周首（国内习惯），跨年也不会错位。"""
    d = date.fromisoformat(day)
    iso = d.isocalendar()
    return date.fromisocalendar(iso[0], iso[1], 1).isoformat()


def build_trend(
    conn: sqlite3.Connection,
    tracker_id: str,
    start: str,
    end: str,
    model: str | None,
    channel: str | None,
    granularity: str = "day",
    client: str | None = None,
) -> dict:
    """按日或按周的趋势。

    周聚合故意在 Python 里做，不另写一条 SQL：
    这样「周值 = 该周各日之和」是结构上成立的，不靠两条查询碰巧一致。
    平均位次用「位次和 ÷ 位次数」重算，而不是把日平均值再平均——
    各天样本数不同时，后者会算错。
    """
    if granularity not in {"day", "week"}:
        raise ValueError("granularity 只能是 day 或 week")

    rows = repo.trend_rows(conn, tracker_id, start, end, model, channel, client)
    raw = [
        {
            "date": r["day"],
            "days": 1,
            "valid_total": int(r["valid_total"] or 0),
            "mention_total": int(r["mention_total"] or 0),
            "ranked_total": int(r["ranked_total"] or 0),
            "position_sum": float(r["position_sum"] or 0),
        }
        for r in rows
    ]

    if granularity == "week":
        buckets: dict[str, dict] = {}
        for item in raw:
            key = _week_start(item["date"])
            cell = buckets.setdefault(
                key,
                {
                    "date": key,
                    "days": 0,
                    "valid_total": 0,
                    "mention_total": 0,
                    "ranked_total": 0,
                    "position_sum": 0.0,
                },
            )
            for field in ("valid_total", "mention_total", "ranked_total", "position_sum"):
                cell[field] += item[field]
            cell["days"] += 1
        raw = [buckets[key] for key in sorted(buckets)]

    points = []
    for item in raw:
        rate = metrics.mention_rate(item["mention_total"], item["valid_total"])
        rank = metrics.avg_rank_summary(item["ranked_total"], item["position_sum"])
        points.append(
            {
                "date": item["date"],
                "days": item["days"],
                "valid_total": item["valid_total"],
                "mention_total": item["mention_total"],
                "mention_rate": rate.value,
                "avg_rank": rank.value,
                "ranked_total": item["ranked_total"],
            }
        )

    return {
        "metric_version": settings.metric_version,
        "granularity": granularity,
        "model_changes": [],
        "points": points,
    }


def build_citation_summary(
    conn: sqlite3.Connection,
    tracker_id: str,
    start: str,
    end: str,
    model: str | None,
    channel: str | None,
    client: str | None = None,
    cite_type: str | None = None,
) -> dict:
    return repo.citation_summary(conn, tracker_id, start, end, model, channel, client, cite_type)


# ---------------------------------------------------------------------------
# 下钻：一个指标由哪些样本构成（FR-023）
# ---------------------------------------------------------------------------

SAMPLE_METRICS = {
    "mention_rate": ("提及率", "分母 = 有效样本；每条有效答案都进入分母"),
    "mention_count": ("提及数", "分母 = 有效样本；每条有效答案都进入分母"),
    "avg_rank": ("平均排名", "分母 = 位次非空的提及样本；无推荐列表的答案不参与"),
    "top1_rate": ("Top1 占比", "分母 = 位次非空的提及样本"),
    "top3_rate": ("Top3 占比", "分母 = 位次非空的提及样本"),
    "top10_rate": ("Top10 占比", "分母 = 位次非空的提及样本"),
    "sentiment": ("情感分布", "分母 = 提及样本；未提及品牌的答案不参与"),
    "coverage_rate": ("覆盖率", "分母 = 当前启用的活跃问题；按问题计，不按样本计"),
}


def build_samples(
    conn: sqlite3.Connection,
    tracker_id: str,
    start: str,
    end: str,
    metric: str,
    model: str | None,
    channel: str | None,
    client: str | None = None,
) -> dict | None:
    if metric not in SAMPLE_METRICS:
        return None
    label, rule = SAMPLE_METRICS[metric]

    if metric == "coverage_rate":
        rows = repo.coverage_rows(conn, tracker_id, start, end, model, channel, client)
        items = [
            {
                "question_id": r["question_id"],
                "question_text": r["question_text"],
                "tier": r["tier"],
                "valid_total": r["valid_total"],
                "mention_total": r["mention_total"],
                "in_denominator": True,
                "mentioned": r["mention_total"] > 0,
            }
            for r in rows
        ]
        covered = sum(1 for i in items if i["mentioned"])
        value = metrics.coverage_rate(covered, len(items))
    else:
        rows = repo.metric_sample_rows(conn, tracker_id, start, end, model, channel, client)
        items = []
        for r in rows:
            mentioned = r["mention_id"] is not None
            if metric in {"mention_rate", "mention_count"}:
                in_denominator = True
            elif metric == "sentiment":
                in_denominator = mentioned
            else:  # avg_rank / topN
                in_denominator = r["position"] is not None
            items.append(
                {
                    "answer_id": r["answer_id"],
                    "question_id": r["question_id"],
                    "question_text": r["question_text"],
                    "tier": r["tier"],
                    "model_key": r["model_key"],
                    "channel": r["channel"],
                    "day": r["day"],
                    "collected_at": r["collected_at"],
                    "mentioned": mentioned,
                    "position": r["position"],
                    "sentiment": r["sentiment"],
                    "snippet": r["snippet"],
                    "in_denominator": in_denominator,
                }
            )
        valid_total = len(items)
        mention_total = sum(1 for i in items if i["mentioned"])
        positions = [i["position"] for i in items if i["position"] is not None]
        if metric in {"mention_rate", "mention_count"}:
            value = metrics.mention_rate(mention_total, valid_total)
        elif metric == "avg_rank":
            value = metrics.avg_rank(positions)
        elif metric == "sentiment":
            value = metrics.sentiment_distribution(
                [i["sentiment"] for i in items if i["sentiment"]]
            )
            value = {"value": value["pos"], "status": value["status"], "sample_base": value["base"],
                     "distribution": {"pos": value["pos"], "neu": value["neu"], "neg": value["neg"]}}
        else:
            n = int(metric.removeprefix("top").removesuffix("_rate"))
            value = metrics.top_n_rate(positions, n)

    if hasattr(value, "to_dict"):
        value = value.to_dict()

    if metric == "coverage_rate":
        total_for_base = len(items)
    elif metric == "sentiment":
        total_for_base = value["sample_base"]
    else:
        total_for_base = len([i for i in items if i["in_denominator"]])

    return {
        "metric": metric,
        "label": label,
        "rule": rule,
        "metric_version": settings.metric_version,
        "period": {"from": start, "to": end},
        "sample_base": total_for_base,
        "status": value["status"],
        "value": value["value"],
        "distribution": value.get("distribution") if isinstance(value, dict) else None,
        "items": items,
    }


def build_competitor_ranking(
    conn: sqlite3.Connection, tracker_id: str, start: str, end: str,
    client: str | None = None,
) -> dict:
    """竞品排名：出现次数、占比、平均位次、情感分布。

    占比的分母是有效样本数，不是提及数 —— 问的是「多少条答案里提到了它」。
    """
    counts = repo.window_counts(conn, tracker_id, start, end, client)
    total = counts["valid_total"]
    items = repo.competitor_ranking_rows(conn, tracker_id, start, end, client)
    for it in items:
        it["share"] = (it["mentions"] / total) if total else None
        if it["avg_position"] is not None:
            it["avg_position"] = round(it["avg_position"], 2)
        for key in ("pos", "neu", "neg"):
            it[key] = int(it[key] or 0)
    return {
        "metric_version": settings.metric_version,
        "period": {"from": start, "to": end},
        "valid_total": total,
        "items": items,
    }


def build_competitor_cooccurrence(
    conn: sqlite3.Connection, tracker_id: str, start: str, end: str,
    client: str | None = None,
) -> dict:
    """竞品共现：同一答案里同时出现的次数，含本方品牌。

    读法：matrix[i][j] = 品牌 i 与品牌 j 在同一答案里同时出现的答案数。
    对角线上是各自的出现次数，可以拿来当参照基数。
    """
    rows = repo.competitor_cooccurrence_rows(conn, tracker_id, start, end, client)
    labels: list[str] = []
    for r in rows:
        for key in ("left_name", "right_name"):
            if r[key] and r[key] not in labels:
                labels.append(r[key])
    labels.sort()
    index = {name: i for i, name in enumerate(labels)}
    matrix = [[0] * len(labels) for _ in labels]
    for r in rows:
        i, j = index[r["left_name"]], index[r["right_name"]]
        matrix[i][j] = int(r["times"])
        matrix[j][i] = int(r["times"])
    return {
        "metric_version": settings.metric_version,
        "period": {"from": start, "to": end},
        "labels": labels,
        "matrix": matrix,
    }


def build_sentiment_by_model(
    conn: sqlite3.Connection, tracker_id: str, start: str, end: str,
    client: str | None = None,
) -> dict:
    """按模型拆分情感分布。

    每个模型独立判样本是否足够：不足就标 ``insufficient_samples``，
    不给三个看起来能比较、其实没法比较的比例。
    """
    rows = repo.sentiment_by_model_rows(conn, tracker_id, start, end, client)
    items: list[dict] = []
    pooled: list[str] = []
    for r in rows:
        sentiments = (
            ["pos"] * int(r["pos"] or 0)
            + ["neu"] * int(r["neu"] or 0)
            + ["neg"] * int(r["neg"] or 0)
        )
        pooled.extend(sentiments)
        items.append(
            {
                "model_key": r["model_key"],
                "mentions": int(r["mentions"] or 0),
                "ranked": int(r["ranked"] or 0),
                "avg_position": (
                    round(r["avg_position"], 2) if r["avg_position"] is not None else None
                ),
                **metrics.sentiment_distribution(sentiments),
            }
        )
    return {
        "metric_version": settings.metric_version,
        "period": {"from": start, "to": end},
        "items": items,
        "total": metrics.sentiment_distribution(pooled),
        "min_samples": settings.min_samples,
        "rule": (
            "分母 = 各模型下本方品牌的提及样本；"
            f"少于 {settings.min_samples} 条的模型只标样本不足，不给比例"
        ),
    }


def build_question_performance(
    conn: sqlite3.Connection, tracker_id: str, start: str, end: str,
    client: str | None = None,
) -> dict:
    """分问题表现：每个问题的样本数、提及率、平均位次。

    样本不足 min_samples 的问题不给提及率 —— 1 条样本的 100% 和 30 条样本的 40%
    放在一张表里比，只会误导。样本为 0 的问题单独标 "no_samples"。
    """
    rows = repo.question_performance_rows(conn, tracker_id, start, end, client)
    items: list[dict] = []
    total_samples = total_mentions = covered = 0
    for r in rows:
        samples = int(r["samples"] or 0)
        mentions = int(r["mentions"] or 0)
        total_samples += samples
        total_mentions += mentions
        if samples:
            covered += 1
        if samples == 0:
            status = "no_samples"
        elif samples < settings.min_samples:
            status = "insufficient_samples"
        else:
            status = "ok"
        items.append(
            {
                "question_id": r["question_id"],
                "text": r["question_text"],
                "tier": r["tier"],
                "samples": samples,
                "mentions": mentions,
                "mention_rate": (
                    round(mentions / samples, 4) if status == "ok" else None
                ),
                "status": status,
                "ranked": int(r["ranked"] or 0),
                "avg_rank": (
                    round(r["avg_position"], 2)
                    if r["avg_position"] is not None
                    else None
                ),
            }
        )
    return {
        "metric_version": settings.metric_version,
        "period": {"from": start, "to": end},
        "items": items,
        "totals": {
            "questions": len(items),
            "covered": covered,
            "samples": total_samples,
            "mentions": total_mentions,
            "mention_rate": (
                round(total_mentions / total_samples, 4) if total_samples else None
            ),
            "min_samples": settings.min_samples,
        },
        "rule": "提及率 = 提及数 ÷ 该问题的有效样本；样本少于 min_samples 不给比例，0 条标未采集",
    }
