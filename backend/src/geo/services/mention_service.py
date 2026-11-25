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


def detect_for_answer(
    conn: sqlite3.Connection,
    *,
    tenant_id: str,
    tracker_id: str,
    answer_id: str,
) -> dict:
    """只对一条答案做识别——采集之后的增量路径。

    为什么不全量重算：采集循环每小时跑一次，全量扫全部答案会让每次采集
    多花几百毫秒到几秒，而且随库增长越来越慢；1500 条时还能忍，15000 条时
    调度器会被自己的重算拖到跑不完一轮。

    口径必须与 ``recompute`` 完全一致：同一个 ``mention_parser``、同一套别名
    （品牌名 + 启用中的别名）、同样只处理 ``is_valid=1``。
    两套路径分叉是这类"为了快复制一份逻辑"最常见的失败方式，
    所以 tests/integration/test_collect_slot.py 里有一条交叉断言：
    同一批答案，增量结果必须与全量重算的提及数逐个相等。

    不写审计：这是采集的附带动作，一次采集一条，写审计会把审计表刷成流水账；
    人工触发的全量重算才需要留痕。
    """
    row = conn.execute(
        "SELECT text, is_valid FROM answer WHERE id = ? AND tracker_id = ? AND tenant_id = ?",
        (answer_id, tracker_id, tenant_id),
    ).fetchone()
    if row is None:
        raise ValueError(f"答案不存在或不属于该租户：{answer_id}")

    profile = config_repo.get_brand_profile(conn, tracker_id, tenant_id)
    if profile is None:
        return {"answer_id": answer_id, "matched": False, "reason": "NO_BRAND_PROFILE"}

    if not row["is_valid"]:
        # 无效答案不进指标分母，也不进识别——与 recompute 的 answer_texts 口径一致
        return {"answer_id": answer_id, "matched": False, "reason": "INVALID_ANSWER"}

    aliases = [profile["name"]] + [alias["alias"] for alias in profile["aliases"]]
    hit = mention_parser.detect(row["text"], aliases)

    # 先清旧结果再写：mention 表有 UNIQUE(answer_id, brand_id)，
    # 不清的话重试第二次插入直接撞约束，把一次正常重试变成异常。
    conn.execute(
        "DELETE FROM mention WHERE answer_id = ? AND brand_id = ? AND subject_type = 'own'",
        (answer_id, profile["id"]),
    )
    if hit is None:
        return {"answer_id": answer_id, "matched": False}

    mention_repo.insert_mention(conn, answer_id, profile["id"], hit)
    return {
        "answer_id": answer_id,
        "matched": True,
        "matched_alias": hit.alias,
        "sentiment": hit.sentiment,
    }
