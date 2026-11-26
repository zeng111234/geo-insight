"""按问题算「值不值得先投一篇」的机会分。

只用已有样本能算出来的三件事，不做语义推断：

1. **gap**：本品牌在这题上没被提及的比例。越大越值得投——已经次次被提的问题不需要再写。
2. **competitor_rate**：这题里出现竞品的答案占比。AI 已经在给品牌答案，说明位置存在；
   竞品也 0 的问题，多半是问法不带品牌意图（「怎么写起诉状」），投内容也换不来提及。
3. **publishable**：引用里来自「我们已接入的发布平台」的比例。比例高说明这题的信源池
   本来就收我们能发的站，发进去才可能被引；全是裁判文书和百科的题，发公众号没用。

三项加权，权重写死在 ``OPPORTUNITY_WEIGHTS``。**这不是效果预测**，是排序依据：
它回答「先写哪一篇」，不回答「写了会不会涨」。
"""

from __future__ import annotations

import sqlite3

from geo.repositories import opportunity_repository as opp_repo
from geo.repositories import sqlite_repository as repo
from geo.settings import settings

# 分数权重。改这里要同时改 docs 里的口径说明，否则前端展示的公式会和实现不一致。
OPPORTUNITY_WEIGHTS = {"gap": 0.4, "competitor": 0.25, "publishable": 0.35}

# 已接入发布适配器的平台（backend/src/geo/publishers/）。
# 域名里出现这些词就算「发得进去」——zhuanlan.zhihu.com、mp.weixin.qq.com、
# www.sohu.com/a/xxx 都能命中；wechat 与 weixin 视为同一个。
PUBLISHABLE_PLATFORMS = {
    "zhihu": "知乎",
    "weixin": "公众号",
    "sohu": "搜狐",
    "toutiao": "头条",
    "douyin": "抖音",
    "xiaohongshu": "小红书",
}
_PLATFORM_LABELS = frozenset(PUBLISHABLE_PLATFORMS)


def platform_of(domain: str) -> str | None:
    """域名归到已接入平台；归不上返回 None。

    按标签匹配而不是 `endswith('zhihu.com')`：演示库和测试库里的域名是
    `zhihu.example.com` 这种占位，真实库里是 `zhuanlan.zhihu.com`，两种都该认出来。
    标签直接等于平台 key 才算（`mp.weixin.qq.com` 命中 weixin），不做同义词映射——
    多一条映射就多一条没人验证的分支。
    """
    host = (domain or "").strip().lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return None
    for label in host.split("."):
        if label in _PLATFORM_LABELS:
            return label
    return None


def _rate(numerator: int, denominator: int) -> float | None:
    if not denominator:
        return None
    return round(numerator / denominator, 4)


def _score(gap: float, competitor_rate: float | None, publishable_share: float | None) -> float:
    return round(
        100
        * (
            OPPORTUNITY_WEIGHTS["gap"] * gap
            + OPPORTUNITY_WEIGHTS["competitor"] * (competitor_rate or 0.0)
            + OPPORTUNITY_WEIGHTS["publishable"] * (publishable_share or 0.0)
        ),
        1,
    )


def _next_action(
    *, samples: int, own_rate: float | None, competitor_rate: float | None, publishable: int
) -> str:
    floor = settings.min_samples
    if samples == 0:
        return "该问题本期没有样本，先补采样再谈机会"
    if samples < floor:
        return f"样本 {samples} 条，低于 {floor} 条，先补样本"
    if not publishable:
        return "引用池里没有可发布平台，发公众号/知乎进去也没人引，先换渠道或换问法"
    if own_rate == 0 and (competitor_rate or 0) > 0:
        return "AI 已经在给品牌答案但没提我们——最适合先投一篇"
    if own_rate == 0:
        return "AI 对这题基本不给品牌答案，投内容前先确认问法带不带品牌意图"
    if (own_rate or 0) >= 0.5:
        return "已被稳定提及，优先级低"
    if (own_rate or 0) < 0.3:
        return "提及率偏低，优先补一篇"
    return "有提升空间，可排期"


def build_opportunities(
    conn: sqlite3.Connection,
    tracker_id: str,
    start: str,
    end: str,
    *,
    limit: int | None = None,
    client: str | None = None,
) -> dict:
    rows = repo.question_performance_rows(conn, tracker_id, start, end, client)
    competitors = opp_repo.competitor_rows(conn, tracker_id, start, end, client)
    domain_rows = opp_repo.citation_domain_rows(conn, tracker_id, start, end, client)

    # 引用按问题聚合：总数、可发布平台条数、各平台明细
    citations: dict[str, dict] = {}
    for row in domain_rows:
        slot = citations.setdefault(row["question_id"], {"total": 0, "by_platform": {}})
        slot["total"] += row["n"]
        key = platform_of(row["domain"])
        if key:
            slot["by_platform"][key] = slot["by_platform"].get(key, 0) + row["n"]

    items: list[dict] = []
    for row in rows:
        question_id = row["question_id"]
        samples = int(row["samples"] or 0)
        mentions = int(row["mentions"] or 0)
        own_rate = _rate(mentions, samples)

        competitor = competitors.get(question_id, {"answers": 0, "mentions": 0})
        competitor_rate = _rate(competitor["answers"], samples)

        cite = citations.get(question_id, {"total": 0, "by_platform": {}})
        publishable = sum(cite["by_platform"].values())
        publishable_share = _rate(publishable, cite["total"])

        gap = None if own_rate is None else round(1 - own_rate, 4)
        score = None if gap is None else _score(gap, competitor_rate, publishable_share)

        if samples == 0:
            status = "no_samples"
        elif samples < settings.min_samples:
            status = "insufficient_samples"
        else:
            status = "ok"

        reasons = [
            f"本品牌 {mentions}/{samples} 条提及"
            + ("" if own_rate is None else f"（{own_rate * 100:.1f}%）"),
            f"竞品出现在 {competitor['answers']}/{samples} 条答案里"
            + ("" if competitor_rate is None else f"（{competitor_rate * 100:.1f}%）"),
        ]
        if cite["total"]:
            detail = "、".join(
                f"{PUBLISHABLE_PLATFORMS[key]} {count}"
                for key, count in sorted(cite["by_platform"].items(), key=lambda kv: -kv[1])
            )
            reasons.append(
                f"引用里 {publishable}/{cite['total']} 条来自可发布平台"
                + (f"（{publishable_share * 100:.1f}%）：{detail}" if detail else "（0%）")
            )
        else:
            reasons.append("该问题本期没有引用数据，判断不了发进去有没有人引")

        items.append(
            {
                "question_id": question_id,
                "text": row["question_text"],
                "tier": row["tier"],
                "status": status,
                "samples": samples,
                "mentions": mentions,
                "own_rate": own_rate,
                "competitor_answers": competitor["answers"],
                "competitor_rate": competitor_rate,
                "citations": cite["total"],
                "publishable_citations": publishable,
                "publishable_share": publishable_share,
                "gap": gap,
                "score": score,
                "next_action": _next_action(
                    samples=samples,
                    own_rate=own_rate,
                    competitor_rate=competitor_rate,
                    publishable=publishable,
                ),
                "reasons": reasons,
            }
        )

    # 有样本的先排；分数相同按竞品在场率——位置存在的证据比什么都强
    order = {"ok": 0, "insufficient_samples": 1, "no_samples": 2}
    items.sort(
        key=lambda item: (
            order.get(item["status"], 3),
            -(item["score"] if item["score"] is not None else -1),
            -(item["competitor_rate"] or 0),
        )
    )
    if limit is not None:
        items = items[:limit]

    return {
        "metric_version": settings.metric_version,
        "period": {"from": start, "to": end},
        "weights": dict(OPPORTUNITY_WEIGHTS),
        "min_samples": settings.min_samples,
        "publishable_platforms": [
            {"key": key, "name": name} for key, name in PUBLISHABLE_PLATFORMS.items()
        ],
        "items": items,
        "notes": [
            "机会分只用于排序「先写哪一篇」，不是效果预测，也不构成因果结论",
            "可发布平台按域名判定，来自已接入的渠道适配器；域名判不出来的一律不算",
        ],
    }
