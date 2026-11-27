"""AI 助手对话（roadmap E4 收口）：基于系统真实数据回答运营问题。

与 assistant_service（写作）的区别：
- 写作把素材拼成"写一篇短文"指令、强制 JSON 输出、落 article；
- 这里把真实指标快照拼成"回答问题"指令、返回纯文本、不落文章。
两者共用同一套 LLM 客户端（DeepSeek Anthropic 兼容端点）与原始响应存档。

诚实边界（贯穿全项目）：
- 回答只依据 snapshot 里的真实数据；数据不足就直说，不编造结论。
- 快照来自 metric_service / assistant_service.collect_material，不另写 SQL、不臆造。
- 每次提问由调用方扣积分并记流水；LLM 失败由调用方冲正，不白扣钱。
"""

from __future__ import annotations

import json
import os

from geo.collectors import keys
from geo.collectors.base import CollectorError
from geo.collectors.failures import raise_for_network, raise_for_status
from geo.collectors.http import DEFAULT_TIMEOUT, HttpResponse, Transport, post_json
from geo.collectors.rawlog import save_raw
from geo.services import assistant_service, metric_service

ENDPOINT = "https://api.deepseek.com/anthropic/v1/messages"
DEFAULT_MODEL = "deepseek-chat"
MAX_TOKENS = 800
SNAPSHOT_DAYS = 30


class ChatError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def available() -> bool:
    return assistant_service.available()


def _period(days: int = SNAPSHOT_DAYS) -> tuple[str, str]:
    """用当前日期回看 N 天，格式与 metric_service 的 period 一致（YYYY-MM-DD）。"""
    from datetime import date, timedelta

    today = date.today()
    return (str(today - timedelta(days=days - 1)), str(today))


def snapshot(conn, tracker_id: str, tenant_id: str) -> dict:
    """收集真实数据快照。只调现有 service，不臆造、不重复写 SQL。"""
    tracker = assistant_service.collect_material(conn, tracker_id, tenant_id)
    start, end = _period()
    overview = metric_service.build_overview(conn, tracker_id, start, end, None, None, None)
    cards = overview.get("cards", {})
    # 只挑能回答问题的关键指标，避免把整包塞进 prompt 烧 token。
    return {
        "brand": tracker.get("brand_name") or tracker.get("tracker_name") or "该品牌",
        "period": {"from": start, "to": end},
        "sample_total": overview.get("sample_total"),
        "valid_total": overview.get("valid_total"),
        "mention_count": cards.get("mention_count"),
        "mention_rate": (cards.get("mention_rate") or {}).get("value"),
        "avg_rank": (cards.get("avg_rank") or {}).get("value"),
        "coverage_rate": (cards.get("coverage_rate") or {}).get("value"),
        "top3_rate": (cards.get("top3_rate") or {}).get("value"),
        "keywords": tracker.get("keywords") or [],
        "client_profile": tracker.get("client_profile") or {},
    }


def build_prompt(snap: dict, question: str) -> str:
    lines = [
        "你是 GEO 运营助手，基于下面给出的【系统真实数据快照】回答运营人员的问题。",
        "硬性要求：",
        "1. 只用快照里的数据说话；快照没有的数据就说「快照里没有这项数据」，不要编。",
        "2. 不要给承诺性结论（如「保证提升」）；可以指出数据本身说明的现象和局限。",
        "3. 数字照抄快照原值，不要换算成百分比以外的形式，也不要四舍五入改变含义。",
        "4. 回答用简洁中文，直接回应问题，不写无关的套话。",
        "\n系统真实数据快照（来自本系统库，非估算）：",
        json.dumps(snap, ensure_ascii=False, indent=2),
        f"\n运营人员的问题：{question.strip()[:1000]}",
    ]
    return "\n".join(lines)


def ask(
    snap: dict,
    question: str,
    *,
    api_key: str | None = None,
    model: str | None = None,
    timeout: float | None = None,
    transport: Transport | None = None,
) -> dict:
    """真调一次 LLM 回答问题。失败抛 ChatError，由调用方冲正积分。"""
    key = api_key or keys.get_key("deepseek")
    if not key:
        raise ChatError("NOT_CONFIGURED", "没有 DeepSeek key，AI 助手不可用")
    payload = {
        "model": model or os.getenv("GEO_DEEPSEEK_WRITER_MODEL", DEFAULT_MODEL),
        "max_tokens": MAX_TOKENS,
        "stream": False,
        "messages": [{"role": "user", "content": build_prompt(snap, question)}],
    }
    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "Accept": "application/json",
    }
    use_transport = transport or post_json
    try:
        response = use_transport(
            ENDPOINT, payload, headers,
            timeout if timeout is not None else float(os.getenv("GEO_COLLECT_TIMEOUT", DEFAULT_TIMEOUT)),
        )
    except Exception as exc:
        try:
            raise_for_network(exc, "AI 助手", 60)
        except CollectorError as net_exc:
            raise ChatError(net_exc.code, str(net_exc)) from exc
        raise
    try:
        raise_for_status(response, "AI 助手")
    except CollectorError as exc:
        raise ChatError(exc.code, str(exc)) from exc

    try:
        raw_body = json.loads(response.body.decode("utf-8", "replace"))
    except ValueError as exc:
        raise ChatError("BAD_RESPONSE", f"助手响应不是 JSON：{exc}") from exc
    answer = assistant_service._parse_message(raw_body)
    if not answer or not answer.strip():
        raise ChatError("EMPTY_RESPONSE", "助手返回了空回答")
    raw_uri, save_error = save_raw("deepseek-assistant", raw_body)
    usage = raw_body.get("usage") or {}
    return {
        "answer": answer.strip(),
        "model_version": raw_body.get("model") or payload["model"],
        "token_in": usage.get("input_tokens"),
        "token_out": usage.get("output_tokens"),
        "raw_uri": raw_uri,
        "raw_save_error": save_error,
        "latency_ms": response.elapsed_ms if isinstance(response, HttpResponse) else None,
    }
