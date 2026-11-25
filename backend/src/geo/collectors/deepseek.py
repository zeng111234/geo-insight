"""DeepSeek 官方 API 采集器（联网搜索 + 引用）。

协议：Anthropic Messages 兼容层 ``POST /anthropic/v1/messages``。
选它而不是 Chat Completions，是因为**引用字段最全**：
Chat 只给 ``annotations`` 的 url/title，而 Messages 的
``web_search_tool_result`` 块还带 ``site_name`` 与 ``page_age``（发布时间）。

## 一个必须记住的坑

DeepSeek 的 citation **只有** ``url`` / ``title`` / ``cited_text``，
没有 ``site_name``、没有发布时间。这两个字段在另一个块里：

    content[].type == "web_search_tool_result"
    content[].content[] = {url, title, site_name, page_age, snippet}

所以要按 URL 把 site_name / page_age 回填到 citation 上。
不回填的话，库里每条引用的媒体名和发布时间都是空的，
「信源分布」和「发布时间完整度」两个 tab 会变成一片空白 —— 不是没数据，是丢了。

## 引用只落「正文引用的」

``web_search_tool_result`` 里的结果**不写 citation 表**。它是"搜到了这些"，
不是"答案引用了这些"。两者混在一起，「搜了但有没引用」这个信号就没了。
原始响应已整份落盘，要查搜到了什么随时能翻。

计费：服务端搜索本身不计费，只算 token。**单价不写死**——
官方价目随账号与时段变，写死一个数字等于给以后埋一个错账。只记 token。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from geo.collectors import keys
from geo.collectors.base import CollectRequest, CollectResult, CollectorError
from geo.collectors.failures import raise_for_network, raise_for_status
from geo.collectors.http import DEFAULT_TIMEOUT, HttpResponse, NetworkError, Transport, post_json
from geo.collectors.rawlog import save_raw

ENDPOINT = "https://api.deepseek.com/anthropic/v1/messages"
DEFAULT_MODEL = "deepseek-flash"
DEFAULT_TZ = timezone(timedelta(hours=8))
SEARCH_TOOL = {"type": "deepseek_web_search", "name": "deepseek_web_search"}


class DeepSeekCollector:
    key = "deepseek"
    display_name = "DeepSeek（官方 API · 联网搜索）"
    kind = "api"

    # 这个采集器认哪个 model_key。库里 model_key 是 'deepseek'，
    # 而真正调用的模型名（deepseek-flash）是另一回事，不能混。
    MODEL_KEYS = ("deepseek",)

    # DeepSeek 的引用挂在 text 块内部（web_search_result_location），
    # 语义上是"正文里这句话带的出处"，所以默认算内联引用。
    CITE_TYPE_DEFAULT = "inline"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        transport: Transport | None = None,
    ):
        self._api_key = api_key
        self._model = model or os.getenv("GEO_DEEPSEEK_MODEL", DEFAULT_MODEL)
        self._timeout = timeout if timeout is not None else float(
            os.getenv("GEO_COLLECT_TIMEOUT", DEFAULT_TIMEOUT)
        )
        self._transport = transport or post_json

    # --- 协议 ---
    def available(self) -> bool:
        return bool(self._api_key or keys.get_key("deepseek"))

    def supports(self, model_key: str) -> bool:
        return model_key in self.MODEL_KEYS

    def collect(self, request: CollectRequest) -> CollectResult:
        api_key = self._api_key or keys.get_key("deepseek")
        if not api_key:
            raise CollectorError(
                "NOT_CONFIGURED",
                "没有 DeepSeek key。设 GEO_DEEPSEEK_API_KEY，或写进 backend/data/keys.local.json",
            )

        payload = {
            "model": self._model,
            "max_tokens": 2048,
            "tools": [SEARCH_TOOL],
            "messages": [{"role": "user", "content": request.question_text}],
            # 不 stream：采集只要最终答案，流式反而要把 SSE 拼回来。
            "stream": False,
        }
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Accept": "application/json",
        }

        try:
            response = self._transport(ENDPOINT, payload, headers, self._timeout)
        except NetworkError as exc:
            raise_for_network(exc, "DeepSeek", self._timeout)

        raise_for_status(response, "DeepSeek")

        try:
            body = response.json()
        except ValueError as exc:
            raise CollectorError("BAD_RESPONSE", f"DeepSeek 返回的不是 JSON：{exc}") from exc

        parsed = _parse_message(body)
        if not parsed["text"]:
            raise CollectorError(
                "EMPTY_TEXT",
                "DeepSeek 返回 200 但正文为空。原始响应已落盘，检查是否被安全策略拦下",
            )

        raw_uri, save_error = save_raw("deepseek", body)
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
            token_in=_int_or_none(body.get("usage", {}).get("input_tokens")),
            token_out=_int_or_none(body.get("usage", {}).get("output_tokens")),
            # 单价不写死：官方价目随账号与时段变，写死就是给以后埋错账。
            cost=None,
            raw_uri=raw_uri,
            raw=raw,
            citations=parsed["citations"],
            search_called=parsed["search_called"],
            search_count=parsed["search_count"],
        )


def _parse_message(body: dict) -> dict:
    """从 Messages 响应里取出正文、正文引用、以及"有没有搜"。

    返回的 ``citations`` 已经按 URL 补上了 site_name / page_age，
    但还没判媒体类型——那是 ``collectors/citations`` 的事。
    """
    if not isinstance(body.get("content"), list):
        raise CollectorError(
            "BAD_RESPONSE", f"DeepSeek 响应缺少 content 数组，顶层字段：{sorted(body)[:8]}"
        )

    texts: list[str] = []
    citations: list[dict] = []
    results_by_url: dict[str, dict] = {}
    search_blocks = 0

    for block in body["content"]:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")

        if block_type == "server_tool_use" and block.get("name") == SEARCH_TOOL["name"]:
            search_blocks += 1

        elif block_type == "web_search_tool_result":
            # 只作旁证：补 site_name / page_age，不当作"答案引用了"。
            for item in block.get("content") or []:
                if isinstance(item, dict) and item.get("url"):
                    results_by_url[item["url"]] = {
                        "title": item.get("title"),
                        "site_name": item.get("site_name"),
                        "page_age": item.get("page_age"),
                    }

        elif block_type == "text":
            if block.get("text"):
                texts.append(str(block["text"]))
            for cite in block.get("citations") or []:
                if isinstance(cite, dict) and cite.get("url"):
                    citations.append(dict(cite))

    enriched = []
    for cite in citations:
        side = results_by_url.get(cite["url"], {})
        enriched.append(
            {
                "url": cite["url"],
                "title": cite.get("title") or side.get("title"),
                "site_name": side.get("site_name"),
                "published_at": side.get("page_age"),
            }
        )

    usage_search = _int_or_none(
        (body.get("usage") or {}).get("server_tool_use", {}).get("web_search_requests")
    )
    # 有搜索块、或 usage 报了搜索次数，都算搜过。两个信号任一成立即可：
    # 只认 usage 会在某些模型下漏判，只认块会在没有 server_tool_use 时漏判。
    search_called = bool(search_blocks) or bool(usage_search)
    search_count = usage_search if usage_search is not None else (search_blocks or None)

    return {
        "text": "\n".join(texts).strip(),
        "citations": enriched,
        "search_called": search_called,
        "search_count": search_count,
        "search_blocks": search_blocks,
        "result_count": len(results_by_url),
    }


def _int_or_none(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
