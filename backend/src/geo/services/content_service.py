"""内容工作室：发布后提及率对比与效果归因。

口径不新造：直接复用 001 的「提及样本 / 有效样本」，窗口按文章首次发布时间切分。
窗口内样本不足时只报状态，不给涨跌幅（与 FR-018 的约定一致）。

归因分两层，不要混：

- ``scope="tracker"``：沿用旧口径，对整个监控对象做前后对比。默认值，保持旧接口与黄金窗口断言不变。
- ``scope="question"``：只算文章绑定的那个问题。发一篇针对 Q1 的文章，Q2 的涨跌不该算到它头上。

引用归因是另一条证据：把 ``publication.published_url`` 归一化后，去匹配发布后窗口里
``citation.url``。匹配到只能说明「这一轮答案引用了这个链接」，不证明因果；要和
目标问题/对照问题的前后变化一起看。
"""

import sqlite3
from collections import Counter
from datetime import date, timedelta

from geo.collectors.citations import normalize_url
from geo.domain import metrics
from geo.repositories import effect_repository as effect_repo
from geo.repositories import sqlite_repository as repo
from geo.settings import settings

SCOPES = ("tracker", "question")


def _shift(day: str, delta: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=delta)).isoformat()


def _window(
    conn: sqlite3.Connection,
    tracker_id: str,
    start: str,
    end: str,
    question_id: str | None = None,
    model_key: str | None = None,
) -> dict:
    counts = effect_repo.window_counts(
        conn, tracker_id, start, end, question_id=question_id, model_key=model_key
    )
    rate = metrics.mention_rate(counts["mention_total"], counts["valid_total"])
    return {
        "from": start,
        "to": end,
        "valid_total": counts["valid_total"],
        "mention_total": counts["mention_total"],
        "mention_rate": rate.to_dict(),
    }


def _delta_pp(before: dict, after: dict) -> tuple[float | None, bool]:
    comparable = before["mention_rate"]["status"] == "ok" and after["mention_rate"]["status"] == "ok"
    if not comparable:
        return None, False
    return round((after["mention_rate"]["value"] - before["mention_rate"]["value"]) * 100, 1), True


def _norm(url: str) -> str:
    """归一化只用于匹配，落库 hash 仍用原始 URL（与导入通道一致）。"""
    try:
        return normalize_url(url)
    except (TypeError, ValueError):
        return ""


def _split_published(publications: list[dict]) -> tuple[list[str], dict[str, str]]:
    urls: list[str] = []
    normalized: dict[str, str] = {}
    for pub in publications:
        url = (pub.get("published_url") or "").strip()
        if not url:
            continue
        urls.append(url)
        key = _norm(url)
        if key:
            normalized.setdefault(key, url)
    return urls, normalized


def _matched(rows: list[dict], normalized: dict[str, str]) -> list[dict]:
    if not normalized:
        return []
    return [row for row in rows if _norm(row.get("url") or "") in normalized]


def _citation_block(
    before_rows: list[dict], after_rows: list[dict], normalized: dict[str, str]
) -> dict:
    before_hits = _matched(before_rows, normalized)
    after_hits = _matched(after_rows, normalized)

    by_model = Counter(row["model_key"] for row in after_hits)
    samples = [
        {
            "url": row["url"],
            "title": row.get("media_name") or row.get("domain") or "",
            "domain": row.get("domain") or "",
            "model_key": row["model_key"],
            "collected_at": row["collected_at"],
            "answer_id": row["answer_id"],
        }
        for row in after_hits[:20]
    ]

    return {
        "before": {
            "matched_count": len(before_hits),
            "matched_answers": len({row["answer_id"] for row in before_hits}),
        },
        "after": {
            "matched_count": len(after_hits),
            "matched_answers": len({row["answer_id"] for row in after_hits}),
            "by_model": dict(sorted(by_model.items())),
            "samples": samples,
        },
    }


def _by_model(
    conn: sqlite3.Connection,
    tracker_id: str,
    before_start: str,
    before_end: str,
    after_start: str,
    after_end: str,
    question_id: str | None,
    normalized: dict[str, str],
) -> list[dict]:
    models = effect_repo.models_in_window(
        conn, tracker_id, before_start, after_end, question_id=question_id
    )
    if not models:
        return []

    # 引用明细只读一次，按模型计数即可；不必每个模型再查一遍 citation。
    after_hits = _matched(
        effect_repo.citation_rows(conn, tracker_id, after_start, after_end, question_id=question_id),
        normalized,
    )
    hit_by_model = Counter(row["model_key"] for row in after_hits)
    answer_by_model: dict[str, set[str]] = {}
    for row in after_hits:
        answer_by_model.setdefault(row["model_key"], set()).add(row["answer_id"])

    result: list[dict] = []
    for model in models:
        before = _window(conn, tracker_id, before_start, before_end, question_id, model)
        after = _window(conn, tracker_id, after_start, after_end, question_id, model)
        delta, comparable = _delta_pp(before, after)
        result.append(
            {
                "model_key": model,
                "before": before,
                "after": after,
                "delta_pp": delta,
                "comparable": comparable,
                "citations_matched": hit_by_model.get(model, 0),
                "matched_answers": len(answer_by_model.get(model, set())),
            }
        )
    return result


def publish_impact(
    conn: sqlite3.Connection,
    article_id: str,
    window_days: int = 3,
    *,
    scope: str = "tracker",
    control_question_id: str | None = None,
) -> dict:
    article = repo.get_article(conn, article_id)
    if article is None:
        return {"status": "not_found"}

    requested_scope = scope if scope in SCOPES else "tracker"
    target_question_id = article.get("question_id") if requested_scope == "question" else None
    actual_scope = "question" if target_question_id else "tracker"
    question = None
    if target_question_id:
        text = effect_repo.question_text(conn, target_question_id)
        question = {"id": target_question_id, "text": text}

    base = {
        "article_id": article_id,
        "tracker_id": article["tracker_id"],
        "window_days": window_days,
        "metric_version": settings.metric_version,
        "scope": actual_scope,
        "question": question,
    }

    published = [
        p for p in article["publications"] if p["status"] == "published" and p["published_at"]
    ]
    if not published:
        return {
            **base,
            "status": "not_published",
            "message": "未发布，无对比数据",
            "published_at": None,
            "before": None,
            "after": None,
            "delta_pp": None,
            "comparable": False,
            "by_model": [],
            "citation_match": {
                "published_urls": [],
                "normalized_urls": [],
                "before": {"matched_count": 0, "matched_answers": 0},
                "after": {"matched_count": 0, "matched_answers": 0, "by_model": {}, "samples": []},
            },
            "control": None,
            "did_pp": None,
            "notes": [],
        }

    first_publish = min(p["published_at"] for p in published)
    publish_day = first_publish[:10]
    before_end = _shift(publish_day, -1)
    before_start = _shift(before_end, -(window_days - 1))
    after_end = _shift(publish_day, window_days - 1)

    before = _window(conn, article["tracker_id"], before_start, before_end, target_question_id)
    after = _window(conn, article["tracker_id"], publish_day, after_end, target_question_id)
    delta_pp, comparable = _delta_pp(before, after)

    published_urls, normalized = _split_published(published)
    before_rows = effect_repo.citation_rows(
        conn, article["tracker_id"], before_start, before_end, question_id=target_question_id
    )
    after_rows = effect_repo.citation_rows(
        conn, article["tracker_id"], publish_day, after_end, question_id=target_question_id
    )
    citation_match = {
        "published_urls": published_urls,
        "normalized_urls": sorted(normalized),
        **_citation_block(before_rows, after_rows, normalized),
    }

    by_model = _by_model(
        conn,
        article["tracker_id"],
        before_start,
        before_end,
        publish_day,
        after_end,
        target_question_id,
        normalized,
    )

    control = None
    did_pp = None
    if control_question_id and control_question_id != target_question_id:
        control_before = _window(
            conn, article["tracker_id"], before_start, before_end, control_question_id
        )
        control_after = _window(
            conn, article["tracker_id"], publish_day, after_end, control_question_id
        )
        control_delta, control_comparable = _delta_pp(control_before, control_after)
        control = {
            "question_id": control_question_id,
            "question_text": effect_repo.question_text(conn, control_question_id),
            "before": control_before,
            "after": control_after,
            "delta_pp": control_delta,
            "comparable": control_comparable,
        }
        if comparable and control_comparable:
            did_pp = round(delta_pp - control_delta, 1)

    notes: list[str] = []
    if requested_scope == "question" and not target_question_id:
        notes.append("该文章未绑定问题，按全对象口径统计")
    if actual_scope == "question":
        notes.append("口径：只算文章绑定的目标问题，其他问题的变化不计入")
    _, last_data = repo.data_date_range(conn, article["tracker_id"])
    if last_data and after_end > last_data:
        notes.append(f"发布后窗口尚未满 {window_days} 天，数据截至 {last_data}")
    if not comparable:
        notes.append("存在样本不足的窗口，按约定不输出涨跌幅")
    if not published_urls:
        notes.append("没有已发布回链，无法做 URL 归因")
    elif citation_match["after"]["matched_count"] == 0:
        notes.append("发布后窗口内未匹配到已发布链接：可能未被 AI 引用，也可能引用的是别的页面")

    return {
        **base,
        "status": "ok",
        "published_at": first_publish,
        "channels": sorted({p["platform_name"] for p in published}),
        "before": before,
        "after": after,
        "delta_pp": delta_pp,
        "comparable": comparable,
        "by_model": by_model,
        "citation_match": citation_match,
        "control": control,
        "did_pp": did_pp,
        "notes": notes,
    }
