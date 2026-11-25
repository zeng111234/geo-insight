"""豆包（火山方舟）采集器：Chat API + 联网搜索。

协议：``POST /api/v3/chat/completions``（OpenAI Chat Completions 兼容），
用顶层 ``web_search_options: {}`` 打开联网，模型自行判断要不要搜。

## 为什么用 Chat 而不是 Responses

Responses 的 ``tools:[{"type":"web_search"}]`` 也能用，但引用结构是
``output[].content[].annotations``，比 Chat 的 ``message.annotations`` 深一层；
而 Chat 是 OpenAI 最稳的那套协议，换模型、换版本时最不容易变形。
两者引用字段一样（url / title / site_name / publish_time）。

## 模型 ID 会变

``doubao-seed-2-1-pro-260915`` 这类 ID 带日期版本，平台迭代后会下线。
所以从 ``GEO_DOUBAO_MODEL`` 读，默认值只是"文档里那个"，
跑之前用你账号里实际开通的模型覆盖它。报 ``BAD_REQUEST`` 时先怀疑这个。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from geo.collectors import keys
from geo.collectors.base import CollectRequest, CollectResult, CollectorError
from geo.collectors.failures import raise_for_network, raise_for_status
from geo.collectors.http import DEFAULT_TIMEOUT, NetworkError, Transport, post_json
from geo.collectors.rawlog import save_raw

ENDPOINT = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
DEFAULT_MODEL = "doubao-seed-2-1-pro-260915"
DEFAULT_TZ = timezone(timedelta(hours=8))


class DoubaoCollector:
    key = "doubao"
    display_name = "豆包（火山方舟 · 联网搜索）"
    kind = "api"

    MODEL_KEYS = ("doubao",)

    # url_citation 是答案级的注释，没有"哪句话用了它"的信息，
    # 所以算参考资料而不是内联引用。
    CITE_TYPE_DEFAULT = "reference"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        transport: Transport | None = None,
    ):
        self._api_key = api_key
        self._model = model or os.getenv("GEO_DOUBAO_MODEL", DEFAULT_MODEL)
        self._timeout = timeout if timeout is not None else float(
            os.getenv("GEO_COLLECT_TIMEOUT", DEFAULT_TIMEOUT)
        )
        self._transport = transport or post_json

    # --- 协议 ---
    def available(self) -> bool:
        return bool(self._api_key or keys.get_key("doubao"))

    def supports(self, model_key: str) -> bool:
        return model_key in self.MODEL_KEYS

    def collect(self, request: CollectRequest) -> CollectResult:
        api_key = self._api_key or keys.get_key("doubao")
        if not api_key:
            raise CollectorError(
                "NOT_CONFIGURED",
                "没有火山方舟 key。设 GEO_ARK_API_KEY，或写进 backend/data/keys.local.json",
            )

        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": request.question_text}],
            # 空对象即可开启联网；模型会自己判断要不要搜。
            # 注意：tools 与 web_search_options 同时传时，平台优先自定义工具、
            # 联网不生效（官方文档明确写了），所以这里绝不能同时带 tools。
            "web_search_options": {},
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }

        try:
            response = self._transport(ENDPOINT, payload, headers, self._timeout)
        except NetworkError as exc:
            raise_for_network(exc, "豆包", self._timeout)

        raise_for_status(response, "豆包")

        try:
            body = response.json()
        except ValueError as exc:
            raise CollectorError("BAD_RESPONSE", f"豆包返回的不是 JSON：{exc}") from exc

        parsed = _parse_chat(body)
        if not parsed["text"]:
            raise CollectorError(
                "EMPTY_TEXT",
                "豆包返回 200 但正文为空。原始响应已落盘，检查是否触发了内容策略",
            )

        raw_uri, save_error = save_raw("doubao", body)
        raw = {"response": body}
        if save_error:
            raw["_raw_save_error"] = save_error

        return CollectResult(
            text=parsed["text"],
            model_key=request.model_key,
            channel=request.channel,
            collected_at=datetime.now(DEFAULT_TZ).isoformat(timespec="seconds"),
            model_version=body.get("model") or self._model,
            latency_ms=response.elapsed_ms,
            token_in=_int_or_none((body.get("usage") or {}).get("prompt_tokens")),
            token_out=_int_or_none((body.get("usage") or {}).get("completion_tokens")),
            cost=None,  # 搜索另计费且价目会变，不猜
            raw_uri=raw_uri,
            raw=raw,
            citations=parsed["citations"],
            search_called=parsed["search_called"],
            search_count=parsed["search_count"],
        )


def _parse_chat(body: dict) -> dict:
    if not isinstance(body.get("choices"), list) or not body["choices"]:
        raise CollectorError(
            "BAD_RESPONSE", f"豆包响应缺少 choices，顶层字段：{sorted(body)[:8]}"
        )

    message = (body["choices"][0] or {}).get("message") or {}
    content = message.get("content")

    texts: list[str] = []
    annotations: list[dict] = []

    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        # 少数实现返回块数组：[{"type":"text","text":...}]，
        # 注释可能挂在 message 上，也可能挂在块上，两处都收。
        for block in content:
            if isinstance(block, dict):
                if block.get("text"):
                    texts.append(str(block["text"]))
                for item in block.get("annotations") or []:
                    annotations.append(item)

    for item in message.get("annotations") or []:
        if isinstance(item, dict):
            annotations.append(item)

    citations = [
        {
            "url": item["url"],
            "title": item.get("title"),
            "site_name": item.get("site_name"),
            "published_at": item.get("publish_time"),
        }
        for item in annotations
        if item.get("url") and item.get("type", "url_citation") == "url_citation"
    ]

    usage = body.get("usage") or {}
    tool_usage = usage.get("tool_usage") or {}
    searched = _int_or_none(tool_usage.get("web_search"))
    # 三态：usage 明确报了次数就用它；没报但有引用，说明搜过；
    # 两个都没有就只能说"不知道"，不能当没搜。
    if searched is None:
        search_called = True if citations else None
        search_count = None
    else:
        search_called = searched > 0
        search_count = searched

    return {
        "text": "\n".join(texts).strip(),
        "citations": citations,
        "search_called": search_called,
        "search_count": search_count,
        "annotation_count": len(annotations),
    }


def _int_or_none(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
