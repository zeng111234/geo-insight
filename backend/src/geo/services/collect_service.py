"""采一条答案并落库。

与导入通道共用同一套写入（``import_repository``）与同一套幂等规则（row_hash），
所以"采集进来的"和"导进来的"在库里是同一种东西，指标不区分来源，只认数据本身。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from geo.collectors import registry
from geo.collectors.base import CollectorError, CollectRequest
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

    collector = registry.get(collector_key) if collector_key else registry.first_available()
    if collector is None:
        raise CollectorError(
            "NO_COLLECTOR",
            "没有可用的采集器。离线演练请设置 GEO_COLLECTOR_CSV 指向答案库文件",
        )
    if not collector.available():
        raise CollectorError("NOT_AVAILABLE", f"采集器 {collector.key} 当前不可用")
    if not collector.supports(model_key):
        raise CollectorError("UNSUPPORTED_MODEL", f"采集器 {collector.key} 不支持模型 {model_key}")

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
        "message": "已写入",
    }
