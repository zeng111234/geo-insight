"""信源落地页（GEO 服务 SOP 第三步「AI 转化通道」）的读取与组装。

SOP 原文：通过信源落地页，展现客户电话、官网等信息，实现大模型推荐流量转化。
这是给大模型/真人抓取的公开页，信息必须来自真实登记，绝不写死或臆造。

数据源是 tracker.settings 的 service / assets 两个子对象（键名与服务台账录入端
settings.html 一致）：
  service  = {package, price, start_date, owner, phone, website}
  assets.table1 = {intro, cases[], honors, expertise, photos}
  assets.table2 = {accounts:[{platform, name}]}
  assets.table3 = {geo_words}
老对象的官网可能存在顶层 settings.website，做兼容回退。

读不到的字段一律给空字符串 + 明确「待补充」标记，不拿演示数据冒充。
"""

from __future__ import annotations

import json
import sqlite3

PLACEHOLDER = "待补充"


def _load_settings(row: sqlite3.Row) -> dict:
    raw = row["settings"] if "settings" in row.keys() else None
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _text(value) -> str:
    """转成干净字符串；None / 非字符串不臆造内容。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value)


def build_site_profile(conn: sqlite3.Connection, tracker_id: str, tenant_id: str) -> dict | None:
    """组装公开落地页数据；对象不存在或不属于租户返回 None。"""
    row = conn.execute(
        """SELECT t.id, t.name, t.industry, t.region, t.status, t.settings,
                  b.name AS brand_name
           FROM tracker t LEFT JOIN brand b ON b.id = t.brand_id
           WHERE t.id = ? AND t.tenant_id = ?""",
        (tracker_id, tenant_id),
    ).fetchone()
    if not row:
        return None

    settings = _load_settings(row)
    service = settings.get("service") if isinstance(settings.get("service"), dict) else {}
    assets = settings.get("assets") if isinstance(settings.get("assets"), dict) else {}
    t1 = assets.get("table1") if isinstance(assets.get("table1"), dict) else {}
    t2 = assets.get("table2") if isinstance(assets.get("table2"), dict) else {}
    t3 = assets.get("table3") if isinstance(assets.get("table3"), dict) else {}

    # 官网：优先台账里的 service.website，回退到老字段 settings.website
    website = _text(service.get("website")) or _text(settings.get("website"))
    intro = _text(t1.get("intro"))
    expertise = _text(t1.get("expertise"))
    honors = _text(t1.get("honors"))
    cases = [c for c in (_text(x) for x in (t1.get("cases") or [])) if c]
    accounts = []
    for acc in (t2.get("accounts") or []):
        if not isinstance(acc, dict):
            continue
        platform, name = _text(acc.get("platform")), _text(acc.get("name"))
        if platform or name:
            accounts.append({"platform": platform, "name": name})
    geo_words = _text(t3.get("geo_words"))

    fields = {
        "brand": _text(row["brand_name"]) or _text(row["name"]),
        "website": website,
        "phone": _text(service.get("phone")),
        "package": _text(service.get("package")),
        "price": _text(service.get("price")),
        "start_date": _text(service.get("start_date")),
        "owner": _text(service.get("owner")),
        "intro": intro,
        "expertise": expertise,
        "honors": honors,
        "cases": cases,
        "accounts": accounts,
        "geo_words": geo_words,
    }
    # 哪些字段是空的——前端据此显示「待补充」，而不是留一片空白让人误以为没有。
    missing = [k for k, v in fields.items() if v == "" or v == []]
    return {
        "tracker_id": tracker_id,
        "status": row["status"],
        "industry": _text(row["industry"]),
        "region": _text(row["region"]),
        **fields,
        "missing": missing,
        "placeholder": PLACEHOLDER,
        "data_note": "本页信息由服务方登记，未登记的项显示「待补充」。",
    }
