"""提及重跑：清空自品牌提及，按当前别名配置从原文重新识别。

什么时候用：改了品牌别名（``/settings`` 增删 ``brand_alias``）之后，旧识别
结果已经过时——003 spec 的验收口径是「旧结果被清空，新结果按新别名计算」。
种子/导入写进来的 mention 也是识别的输入前提（原文），不是要保护的圣旨；
重跑后口径全部由「当前别名 × 原文」决定，这才可复现。

边界（与 domain/mention_parser 的口径一致）：

- 只重算自品牌（tracker.brand_id）；竞品提及不删不判，等竞品识别立项。
- 只扫 ``is_valid=1`` 的答案——指标分母认什么，识别就认什么。
- 情感是粗值（否定线索 → neg，否则 neu）。人工校正是后续入口，不在这层做。

审计走 ``mention.recompute``，before/after 各记一个提及数快照；事务由
调用方 commit（审计不提交是 audit_service 的约定，删改与记账必须同事务）。
"""

from __future__ import annotations

import sqlite3

from geo.domain import mention_parser
from geo.repositories import config_repository as config_repo
from geo.repositories import mention_repository as mention_repo
from geo.services import audit_service


def recompute(
    conn: sqlite3.Connection,
    *,
    tenant_id: str,
    tracker_id: str,
    actor_id: str | None = None,
    now: str | None = None,
) -> dict | None:
    """重算一个监控对象的自品牌提及。监控对象不存在返回 None（404 由接口层判）。"""
    profile = config_repo.get_brand_profile(conn, tracker_id, tenant_id)
    if profile is None:
        return None

    # 别名池 = 品牌名本身 + 全部启用中的别名。品牌名通常也在别名表里挂着
    # 'full'，重复会被 parser 内部去重，这里不用防。
    aliases = [profile["name"]] + [row["alias"] for row in profile["aliases"]]

    before = mention_repo.own_mention_count(conn, tracker_id, profile["id"])
    deleted = mention_repo.delete_own_mentions(conn, tracker_id, profile["id"])

    answers = mention_repo.answer_texts(conn, tracker_id, tenant_id)
    matched = 0
    for answer in answers:
        hit = mention_parser.detect(answer["text"], aliases)
        if hit is not None:
            mention_repo.insert_mention(conn, answer["id"], profile["id"], hit)
            matched += 1

    summary = {
        "tracker_id": tracker_id,
        "brand_id": profile["id"],
        "aliases": len(aliases),
        "answers_scanned": len(answers),
        "mentions_before": before,
        "mentions_deleted": deleted,
        "mentions_matched": matched,
    }
    audit_service.record(
        conn,
        tenant_id=tenant_id,
        actor_id=actor_id,
        action="mention.recompute",
        object_type="tracker",
        object_id=tracker_id,
        before={"own_mentions": before},
        after={
            "own_mentions": matched,
            "answers_scanned": len(answers),
            "aliases": len(aliases),
        },
        now=now,
    )
    return summary
