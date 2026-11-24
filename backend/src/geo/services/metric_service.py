"""Assembles metric responses.

Rules enforced here (spec FR-015/016/017/018):
- mention rate denominator is valid samples;
- average rank and TopN denominator is samples that actually have a list index;
- sentiment denominator is mention samples, never valid samples;
- each metric independently reports ``insufficient_samples``.
"""

import sqlite3

from geo.domain import metrics
from geo.repositories import sqlite_repository as repo
from geo.settings import settings


def build_overview(
    conn: sqlite3.Connection,
    tracker_id: str,
    start: str,
    end: str,
    model: str | None,
    channel: str | None,
) -> dict:
    counts = repo.overview_counts(conn, tracker_id, start, end, model, channel)
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


def build_trend(
    conn: sqlite3.Connection,
    tracker_id: str,
    start: str,
    end: str,
    model: str | None,
    channel: str | None,
) -> dict:
    rows = repo.trend_rows(conn, tracker_id, start, end, model, channel)
    points = []
    for r in rows:
        valid = r["valid_total"]
        mentioned = r["mention_total"]
        m = metrics.mention_rate(mentioned, valid)
        points.append(
            {
                "date": r["day"],
                "mention_rate": m.value,
                "avg_rank": None,
                "valid_total": valid,
                "mention_total": mentioned,
            }
        )
    return {
        "metric_version": settings.metric_version,
        "granularity": "day",
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
) -> dict:
    return repo.citation_summary(conn, tracker_id, start, end, model, channel)


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
) -> dict | None:
    if metric not in SAMPLE_METRICS:
        return None
    label, rule = SAMPLE_METRICS[metric]

    if metric == "coverage_rate":
        rows = repo.coverage_rows(conn, tracker_id, start, end, model, channel)
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
        rows = repo.metric_sample_rows(conn, tracker_id, start, end, model, channel)
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
    conn: sqlite3.Connection, tracker_id: str, start: str, end: str
) -> dict:
    """竞品排名：出现次数、占比、平均位次、情感分布。

    占比的分母是有效样本数，不是提及数 —— 问的是「多少条答案里提到了它」。
    """
    counts = repo.window_counts(conn, tracker_id, start, end)
    total = counts["valid_total"]
    items = repo.competitor_ranking_rows(conn, tracker_id, start, end)
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
    conn: sqlite3.Connection, tracker_id: str, start: str, end: str
) -> dict:
    """竞品共现：同一答案里同时出现的次数，含本方品牌。

    读法：matrix[i][j] = 品牌 i 与品牌 j 在同一答案里同时出现的答案数。
    对角线上是各自的出现次数，可以拿来当参照基数。
    """
    rows = repo.competitor_cooccurrence_rows(conn, tracker_id, start, end)
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
