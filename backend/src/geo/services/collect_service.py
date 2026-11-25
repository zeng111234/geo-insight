"""采一条答案并落库。

与导入通道共用同一套写入（``import_repository``）与同一套幂等规则（row_hash），
所以"采集进来的"和"导进来的"在库里是同一种东西，指标不区分来源，只认数据本身。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from geo.collectors import citations as citation_builder
from geo.collectors import registry
from geo.collectors.base import CollectRequest, Collector, CollectorError
from geo.repositories import import_repository as repo
from geo.services.import_service import normalize_question, sha
from geo.settings import settings

DEFAULT_TZ = timezone(timedelta(hours=8))


def run_once(
    conn: sqlite3.Connection,
    *,
    tracker_id: str,
    question_text: str,
    model_key: str,
    tenant_id: str | None = None,
    channel: str = "api",
    collector_key: str | None = None,
) -> dict:
    """按问题文本与模型采一条，成功后返回落库结果。

    失败一律抛 ``CollectorError``：没配采集器、问题不存在、取不到、重复。
    调用方按 code 决定是提示还是跳过。
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

    collector = _pick_collector(collector_key, model_key)

    result = collector.collect(
        CollectRequest(question_text=question_text, model_key=model_key, channel=channel)
    )

    row_hash = sha(
        "|".join(
            [
                tracker_id,
                question_id,
                result.model_key,
                result.channel,
                result.collected_at,
                sha(result.text),
            ]
        )
    )
    if repo.row_hash_exists(conn, row_hash):
        conn.rollback()
        return {
            "inserted": False,
            "reason": "DUPLICATE",
            "collector": collector.key,
            "model_key": model_key,
            "collected_at": result.collected_at,
            "message": "同一秒内已经采过同样的内容，未重复写入",
        }

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

    conn.commit()
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
        "message": _describe(result, len(drafts)),
    }


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
