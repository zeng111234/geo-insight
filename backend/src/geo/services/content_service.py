"""内容工作室：发布后提及率对比。

口径不新造：直接复用 001 的「提及样本 / 有效样本」，窗口按文章首次发布时间切分。
窗口内样本不足时只报状态，不给涨跌幅（与 FR-018 的约定一致）。
"""

import sqlite3
from datetime import date, timedelta

from geo.domain import metrics
from geo.repositories import sqlite_repository as repo
from geo.settings import settings


def _shift(day: str, delta: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=delta)).isoformat()


def _window(conn: sqlite3.Connection, tracker_id: str, start: str, end: str) -> dict:
    counts = repo.window_counts(conn, tracker_id, start, end)
    rate = metrics.mention_rate(counts["mention_total"], counts["valid_total"])
    return {
        "from": start,
        "to": end,
        "valid_total": counts["valid_total"],
        "mention_total": counts["mention_total"],
        "mention_rate": rate.to_dict(),
    }


def publish_impact(conn: sqlite3.Connection, article_id: str, window_days: int = 3) -> dict:
    article = repo.get_article(conn, article_id)
    if article is None:
        return {"status": "not_found"}

    base = {
        "article_id": article_id,
        "tracker_id": article["tracker_id"],
        "window_days": window_days,
        "metric_version": settings.metric_version,
    }

    published = [p for p in article["publications"] if p["status"] == "published" and p["published_at"]]
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
            "notes": [],
        }

    first_publish = min(p["published_at"] for p in published)
    publish_day = first_publish[:10]
    before_end = _shift(publish_day, -1)
    before_start = _shift(before_end, -(window_days - 1))

    before = _window(conn, article["tracker_id"], before_start, before_end)
    after = _window(conn, article["tracker_id"], publish_day, _shift(publish_day, window_days - 1))

    comparable = before["mention_rate"]["status"] == "ok" and after["mention_rate"]["status"] == "ok"
    delta_pp = None
    if comparable:
        delta_pp = round((after["mention_rate"]["value"] - before["mention_rate"]["value"]) * 100, 1)

    notes: list[str] = []
    _, last_data = repo.data_date_range(conn, article["tracker_id"])
    if last_data and after["to"] > last_data:
        notes.append(f"发布后窗口尚未满 {window_days} 天，数据截至 {last_data}")
    if not comparable:
        notes.append("存在样本不足的窗口，按约定不输出涨跌幅")

    return {
        **base,
        "status": "ok",
        "published_at": first_publish,
        "channels": sorted({p["platform_name"] for p in published}),
        "before": before,
        "after": after,
        "delta_pp": delta_pp,
        "comparable": comparable,
        "notes": notes,
    }
