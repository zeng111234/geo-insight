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
import uuid
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from geo.collectors.citations import normalize_url
from geo.domain import metrics
from geo.repositories import effect_repository as effect_repo
from geo.repositories import sqlite_repository as repo
from geo.services import article_stage_service
from geo.settings import settings

SCOPES = ("tracker", "question")


class ArticleInputError(ValueError):
    """文章字段不合法。"""


class QuestionNotFoundError(ValueError):
    """文章绑定的问题不属于这个监控对象。"""


def create_article(
    conn: sqlite3.Connection,
    tracker_id: str,
    tenant_id: str,
    payload: dict,
) -> dict:
    """新建一篇草稿文章，让试点稿件能真正进入发布/归因链路。

    没有这一步，文章只存在于种子数据里：手写的稿子无法发布，也就无从归因。
    问题可以晚点再绑，但一旦指定，必须属于本对象的当前问题集——否则归因会
    拿另一套问题的样本算到这篇文章头上。
    """
    title = str(payload.get("title") or "").strip()
    body = str(payload.get("body") or "").strip()
    if not title or len(title) > 200:
        raise ArticleInputError("title must be 1-200 characters")
    if not body:
        raise ArticleInputError("body is required")

    question_id = payload.get("question_id") or None
    if question_id:
        belongs = conn.execute(
            """SELECT 1
               FROM question q
               JOIN question_set qs ON qs.id = q.question_set_id
               JOIN tracker t ON t.active_question_set_id = qs.id
               WHERE q.id = ? AND t.id = ? AND t.tenant_id = ?""",
            (question_id, tracker_id, tenant_id),
        ).fetchone()
        if not belongs:
            raise QuestionNotFoundError("question not in this tracker's active question set")

    article_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    conn.execute(
        """INSERT INTO article(id, tenant_id, tracker_id, question_id, title, body,
                               template_key, status, version, goal, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', 1, ?, ?, ?)""",
        (
            article_id,
            tenant_id,
            tracker_id,
            question_id,
            title,
            body,
            payload.get("template_key") or None,
            payload.get("goal") or None,
            now,
            now,
        ),
    )
    # E3:文章和六阶段状态同一事务落库。分开写就会留下「有文章没阶段」的
    # 中间态，前端首次打开读不到进行中的阶段。
    article_stage_service.ensure_stages(conn, article_id, now)
    conn.commit()
    return repo.get_article(conn, article_id)


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


# 试点判定规则（预注册）。看到数据之后再调阈值，任何结论都不可信；
# 要改就开一次新试点，并在 docs/decisions 留记录。
PILOT_RULE = {
    "target_lift_pp": 20.0,
    "control_max_pp": 10.0,
    "min_window_samples": 21,
}


def _verdict(
    *,
    scope: str,
    before: dict | None,
    after: dict | None,
    delta_pp: float | None,
    comparable: bool,
    control: dict | None,
    did_pp: float | None,
) -> dict:
    """按预注册规则给一句话结论，不让看数的人各自解释。

    effective    目标问题达到门槛，且对照问题没跟着动
    inconclusive 有提升但不够，或对照同期也在动（排除不了平台整体变化）
    negative     目标问题没涨
    insufficient 样本不够，不下结论
    """
    rule = dict(PILOT_RULE)
    floor = rule["min_window_samples"]
    observed = {
        "delta_pp": delta_pp,
        "control_delta_pp": (control or {}).get("delta_pp"),
        "did_pp": did_pp,
        "before_samples": (before or {}).get("valid_total", 0),
        "after_samples": (after or {}).get("valid_total", 0),
    }
    base = {"rule": rule, "observed": observed}

    if before is None or after is None:
        return {**base, "status": "not_published", "message": "未发布，无处判定", "reasons": []}
    if scope != "question":
        return {
            **base,
            "status": "not_applicable",
            "message": "全对象口径，不判定",
            "reasons": ["结果混入了其他问题，判不出这一篇带来的变化"],
        }
    if not comparable:
        return {
            **base,
            "status": "insufficient",
            "message": "样本不足，暂不下结论",
            "reasons": ["存在样本不足的窗口，按约定不输出结论"],
        }
    if observed["before_samples"] < floor or observed["after_samples"] < floor:
        return {
            **base,
            "status": "insufficient",
            "message": "样本不足，暂不下结论",
            "reasons": [
                f"发布前 {observed['before_samples']} 条 / 发布后 {observed['after_samples']} 条，"
                f"判定需要各 ≥ {floor} 条"
            ],
        }

    reasons: list[str] = []
    target = rule["target_lift_pp"]
    reached = delta_pp >= target
    reasons.append(f"目标问题 {delta_pp:+.1f}pp，{'达到' if reached else '未达'} +{target:.1f}pp 门槛")

    control_ok = False
    if control is None:
        # 没有对照就不给「有效」：目标问题涨了也可能是平台整体在变。
        reasons.append("未设置对照问题，排除不了平台整体变化")
    else:
        control_delta = observed["control_delta_pp"]
        if control_delta is None or not control.get("comparable"):
            reasons.append("对照问题样本不足，排除不了平台整体变化")
        elif abs(control_delta) >= rule["control_max_pp"]:
            reasons.append(
                f"对照问题同期 {control_delta:+.1f}pp，超过 {rule['control_max_pp']:.1f}pp，"
                "排除不了平台整体变化"
            )
        else:
            control_ok = True
            reasons.append(f"对照问题 {control_delta:+.1f}pp，变化在 {rule['control_max_pp']:.1f}pp 内")

    if delta_pp <= 0:
        status, message = "negative", "未观察到提升"
    elif reached and control_ok:
        status, message = "effective", "有效"
    else:
        status, message = "inconclusive", "不足以判定"
    return {**base, "status": status, "message": message, "reasons": reasons}


def _baseline_preview(
    conn: sqlite3.Connection,
    tracker_id: str,
    question_id: str | None,
    window_days: int,
    scope: str,
) -> dict:
    """未发布时先看基线够不够。

    判定窗口是「发布日往前 window_days 天」。这段日期一旦过去就不会再长出新样本，
    所以**发布前样本不足 = 这篇永远判不了**——必须让人在按发布之前看到这件事，
    而不是等发完了再说「样本不足」。
    """
    _, last_data = repo.data_date_range(conn, tracker_id)
    anchor = last_data or date.today().isoformat()
    start = _shift(anchor, -(window_days - 1))
    counts = effect_repo.window_counts(conn, tracker_id, start, anchor, question_id=question_id)
    needed = PILOT_RULE["min_window_samples"]
    samples = int(counts["valid_total"])
    return {
        "scope": scope,
        "from": start,
        "to": anchor,
        "samples": samples,
        "needed": needed,
        "ready": samples >= needed,
        "shortfall": max(0, needed - samples),
    }


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
            "baseline": _baseline_preview(
                conn, article["tracker_id"], target_question_id, window_days, actual_scope
            ),
            "verdict": _verdict(
                scope=actual_scope,
                before=None,
                after=None,
                delta_pp=None,
                comparable=False,
                control=None,
                did_pp=None,
            ),
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
        "verdict": _verdict(
            scope=actual_scope,
            before=before,
            after=after,
            delta_pp=delta_pp,
            comparable=comparable,
            control=control,
            did_pp=did_pp,
        ),
        "notes": notes,
    }
