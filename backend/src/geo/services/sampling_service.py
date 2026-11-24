"""采样缺口：按采样计划算出「哪天、哪个问题、哪个模型，还差几条样本」。

**核销不是状态机，是实时差分。** 导入新数据后重新算一遍，缺口自然缩小或归零；
不需要维护一张待办表，也就不存在"待办表和真实数据对不上"的问题。
撤销一个导入批次后缺口会重新出现，这也是对的——数据没了就该重采。

口径：
- 应有条数 = 该问题层级对应的次数（core→core_times，long_tail→long_tail_times），每天重置；
- 实际条数 = 当天该问题该模型的有效样本数，不区分渠道（人工用网页问也算采到）；
- 缺 = max(0, 应有 − 实际)，采多了不算负缺口。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta

from geo.repositories import sqlite_repository as repo

MAX_DAYS = 366


def _model_keys(plan: dict) -> list[str]:
    try:
        keys = json.loads(plan.get("model_keys") or "[]")
    except ValueError:
        return []
    return [str(k) for k in keys if str(k).strip()]


def _days(start: str, end: str) -> list[str]:
    try:
        first = date.fromisoformat(start)
        last = date.fromisoformat(end)
    except ValueError as exc:
        raise ValueError(f"日期格式必须是 YYYY-MM-DD：{exc}") from None
    if first > last:
        raise ValueError("起始日期不能晚于结束日期")
    span = (last - first).days + 1
    if span > MAX_DAYS:
        raise ValueError(f"区间最多 {MAX_DAYS} 天，当前 {span} 天")
    return [(first + timedelta(days=i)).isoformat() for i in range(span)]


def compute_gaps(
    conn: sqlite3.Connection,
    tracker_id: str,
    start: str,
    end: str,
    model: str | None = None,
) -> dict:
    plan = repo.sampling_plan_of(conn, tracker_id)
    if not plan:
        raise ValueError("这个监控对象还没有采样计划，先去 /settings 配一个再来算缺口")

    models = _model_keys(plan)
    if not models:
        raise ValueError("采样计划里的模型列表是空的")
    if model:
        if model not in models:
            raise ValueError(f"模型 {model} 不在采样计划里（计划里是：{'、'.join(models)}）")
        models = [model]

    questions = repo.active_questions(conn, tracker_id)
    if not questions:
        raise ValueError("这个监控对象下没有启用中的问题")

    counts = repo.sample_counts(conn, tracker_id, start, end)

    rows: list[dict] = []
    total_expected = total_actual = total_missing = 0
    for day in _days(start, end):
        for question in questions:
            expected = int(
                plan["core_times"] if question["tier"] == "core" else plan["long_tail_times"]
            )
            for model_key in models:
                actual = counts.get((day, question["id"], model_key), 0)
                missing = max(0, expected - actual)
                total_expected += expected
                total_actual += min(actual, expected)
                total_missing += missing
                rows.append(
                    {
                        "date": day,
                        "question_id": question["id"],
                        "question_text": question["text"],
                        "tier": question["tier"],
                        "model": model_key,
                        "expected": expected,
                        "actual": actual,
                        "missing": missing,
                    }
                )

    return {
        "tracker_id": tracker_id,
        "period": {"from": start, "to": end},
        "models": models,
        "question_count": len(questions),
        "plan": {
            "core_times": plan["core_times"],
            "long_tail_times": plan["long_tail_times"],
            "channel": plan["channel"],
        },
        "rows": rows,
        "totals": {
            "expected": total_expected,
            "actual": total_actual,
            "missing": total_missing,
        },
    }


def gap_tickets(gaps: dict, default_time: str = "09:00:00") -> list[dict]:
    """把缺口展开成「一行一个待采样本」，给人工派活用。

    每行带上导入通道认得的列（question/answer/model/collected_at），
    人工填完 answer 就能直接导回来，不需要另做一套模板。
    """
    tickets: list[dict] = []
    for row in gaps["rows"]:
        for index in range(row["missing"]):
            tickets.append(
                {
                    "date": row["date"],
                    "question": row["question_text"],
                    "model": row["model"],
                    "tier": row["tier"],
                    "slot": f"{row['actual'] + index + 1}/{row['expected']}",
                    # 预填日期与默认时刻，人工只需填答案；时间与实际不符可以改
                    "collected_at": f"{row['date']}T{default_time}",
                    "answer": "",
                    "mentions": "",
                    "citations": "",
                }
            )
    return tickets


def collection_health(conn: sqlite3.Connection, tracker_id: str, start: str, end: str) -> dict:
    """采集健康：计划应采 vs 实际已采，按模型和按天汇总。

    和「缺口清单」是同一套口径的两面：
    - 缺口清单回答「今天还差哪几条」，逐条列出来派活；
    - 这里回答「整体产能怎么样」，用来判断采样计划是不是定得离谱。

    没有采样计划或没有问题时不抛错，返回 ``configured=False`` + 说明，
    因为「还没配」本身就是要展示的健康状态之一。
    """
    try:
        gaps = compute_gaps(conn, tracker_id, start, end)
    except ValueError as exc:
        return {
            "tracker_id": tracker_id,
            "period": {"from": start, "to": end},
            "configured": False,
            "message": str(exc),
            "totals": None,
            "by_model": [],
            "by_day": [],
            "plan": None,
        }

    by_model: dict[str, dict] = {}
    by_day: dict[str, dict] = {}
    for row in gaps["rows"]:
        for bucket, key in ((by_model, row["model"]), (by_day, row["date"])):
            cell = bucket.setdefault(
                key, {"expected": 0, "actual": 0, "missing": 0}
            )
            cell["expected"] += row["expected"]
            cell["actual"] += min(row["actual"], row["expected"])
            cell["missing"] += row["missing"]

    def with_rate(cell: dict) -> dict:
        cell["rate"] = (
            round(cell["actual"] / cell["expected"], 4) if cell["expected"] else None
        )
        return cell

    models = [{"model": k, **with_rate(v)} for k, v in sorted(by_model.items())]
    days = [{"date": k, **with_rate(v)} for k, v in sorted(by_day.items())]
    totals = {
        "expected": gaps["totals"]["expected"],
        "actual": gaps["totals"]["actual"],
        "missing": gaps["totals"]["missing"],
    }
    with_rate(totals)

    return {
        "tracker_id": tracker_id,
        "period": {"from": start, "to": end},
        "configured": True,
        "plan": {**gaps["plan"], "question_count": gaps["question_count"]},
        "totals": totals,
        "by_model": models,
        "by_day": days,
        "last_import_at": repo.latest_import_at(conn, tracker_id, start, end),
        "message": None,
    }
