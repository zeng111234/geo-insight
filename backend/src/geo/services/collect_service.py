"""采一条答案并落库。

与导入通道共用同一套写入（``import_repository``），所以"采集进来的"和"导进来的"
在库里是同一种东西，指标不区分来源，只认数据本身。

## 幂等有两层，各管一种情况

1. **槽位唯一索引**（015 迁移）：同一天同一(问题, 模型, 渠道)的第 N 次采样，
   只能有一条。重试、并发、进程重启都撞在这条约束上，写不进去。
2. **row_hash**：同一秒内完全相同的采集（文本 + 时间戳都一样）直接跳过。
   它拦的是"一秒内的重复调用"，槽位索引拦的是"同一天同一槽位的重复写入"。

两层都要：只靠 row_hash，跨秒的重试会变成两条样本；只靠槽位，
同一秒并发两条会有一条撞约束报错而不是安静跳过。

## 为什么先占槽位再调用

调用是真花钱的（token + 搜索）。超额检查必须在调用**之前**，
否则"今天已经采满 3 次"的拒绝要等到第 4 次调用的账单出来才生效。
所以顺序是：选采集器 → 占槽位 → 调平台 → 写库。
占槽位失败（NO_SLOT）时一分钱没花，这是这条顺序唯一的目的。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from geo.collectors import citations as citation_builder
from geo.collectors import registry
from geo.collectors.base import CollectRequest, Collector, CollectorError
from geo.repositories import import_repository as repo
from geo.repositories import sqlite_repository as sqlite_repo
from geo.services import mention_service, sampling_service
from geo.services.import_service import normalize_question, sha
from geo.settings import settings

DEFAULT_TZ = timezone(timedelta(hours=8))

# 这些错误说明"平台现在整体不舒服"，继续打只会更糟。
# 配置类错误（NOT_FOUND / UNKNOWN_QUESTION / BAD_RESPONSE）不在里面：
# 那只影响出问题的那一条，其它问题照常采。
RETRYABLE_CODES = frozenset({"RATE_LIMITED", "TIMEOUT", "UPSTREAM_ERROR", "CONNECTION"})
# 连续撞上这么多次就结束本轮。3 次是"足以确认平台在限流"的最小数量，
# 又不至于因为偶发一次超时就不采了。
MAX_RETRYABLE_FAILURES = 3


def run_once(
    conn: sqlite3.Connection,
    *,
    tracker_id: str,
    question_text: str,
    model_key: str,
    tenant_id: str | None = None,
    channel: str = "api",
    collector_key: str | None = None,
    allow_over: bool = False,
    detect_mentions: bool = True,
) -> dict:
    """按问题文本与模型采一条，成功后返回落库结果。

    失败一律抛 ``CollectorError``：没配采集器、问题不存在、今天采满、取不到。
    调用方按 code 决定是提示还是跳过。

    ``allow_over=True`` 越过当天计划量（人工补采用）。它只放开额度，
    仍然分配新槽位——"补采"不该覆盖已有的那一条。
    """
    tenant = tenant_id or settings.default_tenant_id
    tracker = repo.get_tracker(conn, tracker_id, tenant)
    if not tracker:
        raise CollectorError("UNKNOWN_TRACKER", f"监控对象不存在或不属于该租户：{tracker_id}")

    question_id = repo.load_questions(conn, tracker_id).get(normalize_question(question_text))
    if not question_id:
        # 采集是"对已知问题取样"，不在这里造问题；要加问题请走配置或导入通道
        raise CollectorError(
            "UNKNOWN_QUESTION", f"问题不在当前监控对象下：{question_text[:40]}"
        )

    # 顺序有讲究：先确认有采集器（没配 key 要报 NO_COLLECTOR，不能报成"采满了"），
    # 再占槽位（超额在花钱之前拒绝），最后才调平台。
    collector = _pick_collector(collector_key, model_key)

    started = datetime.now(DEFAULT_TZ)
    day = started.date().isoformat()
    slot = _allocate_slot(
        conn, tracker_id, question_id, model_key, channel, day, allow_over=allow_over
    )

    result = collector.collect(
        CollectRequest(question_text=question_text, model_key=model_key, channel=channel)
    )

    # row_hash 的口径与导入通道**刻意不同**：这里用「日期 + 槽位」而不是
    # 「到达秒」，因为采集的幂等单位是"这一天这一条"，不是"这一秒"。
    #
    # 两者都会撞上表上的 uq_answer_row_hash（UNIQUE(row_hash)）：
    # - 用 collected_at：平台常常逐字返回相同答案，同一天采 3 次若落在同一秒，
    #   第 2、3 次的 row_hash 与第 1 次相同，被唯一索引拦成"重复"——
    #   但它们是 3 条独立观测，样本数永远够不到计划量。
    # - 用 日期 + 槽位：同槽位重试 hash 相同（拦得住重复写入），
    #   不同槽位 hash 必然不同（3 次采样都能落地）。
    #
    # 导入通道仍用 collected_at，因为那边没有槽位概念，`同一批文件导入两次`
    # 才是要防的重复。
    row_hash = sha(
        "|".join(
            [
                tracker_id,
                question_id,
                result.model_key,
                result.channel,
                day,
                str(slot),
                sha(result.text),
            ]
        )
    )
    # 这里**不**用 row_hash 判重，尽管导入通道靠它去重。
    # 原因：平台对同一个问题经常返回逐字相同的答案。一天采 3 次，
    # 若第 2、3 次恰好落在同一秒，row_hash 会把它们判成"重复"——
    # 但它们本来就是 3 条独立观测，判重会让样本数永远够不到计划量。
    # 采集的幂等靠槽位（同一天同一槽位只能有一条），导入的幂等靠内容哈希，
    # 两者语义不同，不能互相借用。row_hash 仍然写进库，供追溯用。

    try:
        answer_id = repo.insert_answer(
            conn,
            {
                "tenant_id": tenant,
                "tracker_id": tracker_id,
                "question_id": question_id,
                "model_key": result.model_key,
                "model_version": result.model_version,
                "channel": result.channel,
                "raw_uri": result.raw_uri or f"collect://{collector.key}",
                "text": result.text,
                "text_hash": sha(result.text),
                "collected_at": result.collected_at,
                "latency_ms": result.latency_ms,
                "token_in": result.token_in,
                "token_out": result.token_out,
                "cost": result.cost,
                "batch_id": None,
                "row_hash": row_hash,
                "search_called": result.search_called,
                "search_count": result.search_count,
                # 槽位：这一条是今天第几次。跨零点时按"开始采集"那天算，
                # 因为 sampled_at 与 collected_at 可能落在不同日期，
                # 用开始时刻才有唯一解。
                "sample_day": day,
                "sample_idx": slot,
            },
        )

        # 引用与答案同一事务：答案写进去但引用丢了，等于证据缺一半，
        # 而且从外部看不出缺——后面所有"引用条数"都会比真实值少。
        drafts = citation_builder.build_drafts(
            result.citations,
            platform_default=getattr(collector, "CITE_TYPE_DEFAULT", "reference"),
        )
        for draft in drafts:
            repo.insert_citation(
                conn,
                answer_id,
                {
                    "url": draft.url,
                    # 用原始 URL 的 sha，与导入通道一致；归一化只在归因匹配时算。
                    "url_hash": sha(draft.url),
                    "domain": citation_builder.domain_of(draft.url),
                    "media_name": draft.site_name,
                    "media_type": draft.media_type,
                    # owner_type 一律 media：判断"这个域名属于哪个品牌"要看 brand 表的
                    # 域名配置，采集层看不到那个上下文。留给分析层。
                    "owner_type": "media",
                    "position": draft.position,
                    "cite_type": draft.cite_type,
                    "published_at": draft.published_at,
                },
            )

        # 采完立刻识别，不留在下一步：D3 之后调度器每小时跑一轮，
        # "采到了但还没识别"的窗口期内看板会显示提及率没动，很容易被误读成没效果。
        mention = {"matched": False, "reason": "SKIPPED"}
        if detect_mentions:
            mention = mention_service.detect_for_answer(
                conn, tenant_id=tenant, tracker_id=tracker_id, answer_id=answer_id
            )

        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        message = str(exc)
        # 槽位撞了 = 同一槽位已经有数据（并发或重试）。这是"重复"，不是故障。
        if "UNIQUE constraint failed: answer." in message:
            return {
                "inserted": False,
                "reason": "DUPLICATE",
                "collector": collector.key,
                "model_key": model_key,
                "collected_at": result.collected_at,
                "slot": slot,
                "message": f"今天的第 {slot} 条已经有数据（并发或重试），未重复写入",
            }
        raise

    return {
        "inserted": True,
        "answer_id": answer_id,
        "collector": collector.key,
        "collector_kind": collector.kind,
        "model_key": result.model_key,
        "collected_at": result.collected_at,
        "text_length": len(result.text),
        "citations_written": len(drafts),
        "search_called": result.search_called,
        "search_count": result.search_count,
        "raw_uri": result.raw_uri,
        "sample_day": day,
        "sample_idx": slot,
        "mention_matched": bool(mention.get("matched")),
        "message": _describe(result, len(drafts)) + _describe_mention(mention),
    }


def fill_gaps(
    conn: sqlite3.Connection,
    *,
    tracker_id: str,
    model_key: str,
    tenant_id: str | None = None,
    force: bool = False,
    detect_mentions: bool = True,
    collector_key: str | None = None,
) -> dict:
    """按「今天还差哪几条」采一轮，采完再算一次缺口。

    这是 D3 的调度单元：外层循环（CLI 的 ``--loop``）只负责"跑一轮、睡一会"，
    一轮里采多少、采哪个问题、采几次，全部由缺口清单决定。

    **重复跑第二遍不会多采**：第一轮采满后缺口归零，第二轮 ``todo`` 为空，
    一次调用都不发。这就是「同日重跑不产生重复样本」的实现方式——
    不是靠比内容，是靠"没有缺口就不采"。

    失败一条不影响其它条：某个问题被限流时，剩下的问题照常采完，
    错误收在 ``errors`` 里。整轮因为一个 429 全停，等于把一个平台的问题
    传染给所有平台。
    """
    today = datetime.now(DEFAULT_TZ).date().isoformat()
    before = sampling_service.compute_gaps(conn, tracker_id, today, today, model=model_key)
    todo = [row for row in before["rows"] if row["missing"] > 0]

    inserted = duplicates = failed = 0
    errors: list[dict] = []
    retryable_failures = 0
    aborted = False

    for row in todo:
        for _ in range(row["missing"]):
            try:
                outcome = run_once(
                    conn,
                    tracker_id=tracker_id,
                    question_text=row["question_text"],
                    model_key=model_key,
                    tenant_id=tenant_id,
                    allow_over=force,
                    detect_mentions=detect_mentions,
                    collector_key=collector_key,
                )
            except CollectorError as exc:
                failed += 1
                errors.append(
                    {
                        "question": row["question_text"],
                        "code": exc.code,
                        "message": str(exc),
                    }
                )
                # 认证失败与超额是"这一轮别继续了"的信号：
                # 前者每条都会失败（白等 N 次），后者说明计划本身配错了。
                if exc.code in ("AUTH_FAILED", "NO_SLOT", "NO_COLLECTOR"):
                    aborted = True
                    break
                # 限流/超时/5xx 是"过一会儿就好"，但它们会同时影响后面所有调用。
                # 连续撞上几个就收手，否则一轮几十次调用全打在同一个限流上，
                # 既浪费额度，也可能把平台的封禁窗口拖长。
                # 配置类错误（NOT_FOUND / UNKNOWN_QUESTION）不计入：那只影响那一条。
                if exc.code in RETRYABLE_CODES:
                    retryable_failures += 1
                    if retryable_failures >= MAX_RETRYABLE_FAILURES:
                        aborted = True
                        break
                continue
            inserted += 1 if outcome.get("inserted") else 0
            duplicates += 0 if outcome.get("inserted") else 1
        if aborted:
            break

    after = sampling_service.compute_gaps(conn, tracker_id, today, today, model=model_key)
    return {
        "date": today,
        "model_key": model_key,
        "planned_missing": before["totals"]["missing"],
        "remaining_missing": after["totals"]["missing"],
        "inserted": inserted,
        "duplicates": duplicates,
        "failed": failed,
        "aborted": aborted,
        "errors": errors,
    }


def _allocate_slot(
    conn: sqlite3.Connection,
    tracker_id: str,
    question_id: str,
    model_key: str,
    channel: str,
    day: str,
    *,
    allow_over: bool,
) -> int:
    """分配今天这个问题的第几个槽位；超过计划量就拒绝。

    取 max + 1 而不是 count + 1：如果中间有行被删掉（人工清理、测试收尾），
    count 会算出一个已经被用过的序号，撞唯一索引；max 只会往前。
    """
    used = repo.max_slot(conn, tracker_id, question_id, model_key, channel, day)
    slot = used + 1

    expected = _expected_times(conn, tracker_id, question_id)
    if expected is not None and slot > expected and not allow_over:
        raise CollectorError(
            "NO_SLOT",
            f"{day} 这个问题计划 {expected} 次，已经采满。"
            f"要额外补采加 --force（会超出计划，指标里多算一条样本）",
        )
    return slot


def _expected_times(conn: sqlite3.Connection, tracker_id: str, question_id: str) -> int | None:
    """按采样计划与问题层级算当天该采几次；没有计划时不限制（返回 None）。"""
    plan = sqlite_repo.sampling_plan_of(conn, tracker_id)
    if not plan:
        return None
    row = conn.execute("SELECT tier FROM question WHERE id = ?", (question_id,)).fetchone()
    tier = row["tier"] if row else "long_tail"
    return int(plan["core_times"] if tier == "core" else plan["long_tail_times"])


def _pick_collector(collector_key: str | None, model_key: str) -> Collector:
    """选采集器。显式指定时按指定来，否则按「可用且支持该模型」挑。

    自动挑的时候不能只取第一个可用的：离线库排在前面，它可用但可能不认识 doubao，
    直接报 UNSUPPORTED_MODEL 会让人以为"没配 key 也能采"，实际是挑错了人。
    """
    if collector_key:
        collector = registry.get(collector_key)  # 不存在时抛 UNKNOWN_COLLECTOR
        if not collector.available():
            raise CollectorError("NOT_AVAILABLE", f"采集器 {collector.key} 当前不可用")
        if not collector.supports(model_key):
            raise CollectorError(
                "UNSUPPORTED_MODEL", f"采集器 {collector.key} 不支持模型 {model_key}"
            )
        return collector

    found = registry.for_model(model_key)
    if found is not None:
        return found

    available = registry.available()
    if not available:
        raise CollectorError(
            "NO_COLLECTOR",
            f"没有可用采集器，采不了 {model_key}。真实采集需要配置 key"
            f"（GEO_DEEPSEEK_API_KEY / GEO_ARK_API_KEY 或 backend/data/keys.local.json）；"
            f"离线演练请设 GEO_COLLECTOR_CSV 指向答案库文件",
        )
    names = "、".join(f"{c.key}（{c.kind}）" for c in available)
    supported = "、".join(registry.supported_models()) or "（无）"
    raise CollectorError(
        "UNSUPPORTED_MODEL",
        f"没有采集器能采 {model_key}。当前可用：{names}；它们支持：{supported}",
    )


def _describe(result, citation_count: int) -> str:
    """一句话说清这次采到了什么，重点在「搜没搜」与「引没引」。"""
    if result.search_called is None:
        search = "平台未报告"
    elif result.search_called:
        times = f"×{result.search_count}" if result.search_count else ""
        search = f"已联网搜索{times}"
    else:
        search = "未触发搜索"

    if citation_count:
        cite = f"{citation_count} 条引用"
    elif result.search_called:
        # 这一句是 D1 里最重要的信号：搜了但没被引用，不是失败，是"内容没被采纳"。
        cite = "0 条引用（搜到了但没被引用）"
    else:
        cite = "0 条引用"
    return f"{search}，{cite}"


def _describe_mention(mention: dict) -> str:
    """提及识别的结果。采到了但没提到，是要能一眼看见的。"""
    if mention.get("matched"):
        return f"，提及品牌（{mention.get('matched_alias')} / {mention.get('sentiment')}）"
    reason = mention.get("reason")
    if reason == "NO_BRAND_PROFILE":
        return "，未识别（这个对象没配品牌）"
    if reason == "INVALID_ANSWER":
        return "，未识别（答案标记为无效）"
    if reason == "SKIPPED":
        return ""
    return "，未提及品牌"
