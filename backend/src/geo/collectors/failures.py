"""采集失败的归类：把「一次不顺利」翻译成一个稳定的错误码。

为什么要一个专门的模块，而不是在每个 adapter 里写 if/elif：

- 两个平台（DeepSeek / 豆包）的失败形态几乎一样，200 + 429 + 5xx + 超时 + 空回答，
  分类逻辑复制两遍，迟早有一边漏一个分支。
- **「没搜索」不是失败**。这是这个模块存在的最实际理由：一个 200 的响应里
  没有引用，可能是平台没触发搜索（要改问法），也可能是搜了但结果没被引（正常）。
  两者在 ``search_called`` 上区分，不能都当错误吞掉，也不能都当成功。

错误码词汇与 ``domain/enums.py`` 的 ``ErrorCode`` 一致（那里是唯一权威定义）。
"""

from __future__ import annotations

from geo.collectors.base import CollectorError
from geo.collectors.http import HttpResponse, NetworkError

# 401/403：key 不对、过期、额度被停。改问法、重试都没用，必须换 key。
_AUTH_STATUSES = frozenset({401, 403})
# 400/422：参数被拒。最常见的原因是问题太长、模型不支持这个工具、或 body 结构写错。
_BAD_REQUEST_STATUSES = frozenset({400, 404, 405, 422})


def raise_for_status(response: HttpResponse, platform: str) -> None:
    """状态码不对就抛 CollectorError；2xx 直接返回。

    错误正文里通常带着平台自己的 code/message（如 ``invalid_api_key``），
    整段透出给调用方，比只报「失败的请求 401」有用得多。
    """
    status = response.status
    if 200 <= status < 300:
        return

    detail = _detail(response)
    if status in _AUTH_STATUSES:
        raise CollectorError(
            "AUTH_FAILED",
            f"{platform} 拒绝了这个 key（HTTP {status}）：{detail}。换 key 或检查额度，重试没用",
        )
    if status == 429:
        raise CollectorError(
            "RATE_LIMITED",
            f"{platform} 限流（HTTP 429）：{detail}。等一会再采，或降低采样频率",
        )
    if status in _BAD_REQUEST_STATUSES:
        raise CollectorError(
            "BAD_REQUEST",
            f"{platform} 不接受这个请求（HTTP {status}）：{detail}。多为问题过长或模型不支持联网工具",
        )
    if 500 <= status < 600:
        raise CollectorError(
            "UPSTREAM_ERROR",
            f"{platform} 服务端错误（HTTP {status}）：{detail}。平台侧问题，稍后重试",
        )
    raise CollectorError(
        "UPSTREAM_ERROR", f"{platform} 返回了未预期的状态码 HTTP {status}：{detail}"
    )


def raise_for_network(error: NetworkError, platform: str, timeout: float) -> None:
    """网络层失败转 CollectorError。永远不 return —— 调用方写在 except 分支里。"""
    if error.reason == "TIMEOUT":
        raise CollectorError(
            "TIMEOUT", f"{platform} 在 {timeout:.0f} 秒内没有返回。联网搜索比普通对话慢，可调大超时"
        ) from error
    raise CollectorError("CONNECTION", f"{platform} 连不上：{error}") from error


def _detail(response: HttpResponse) -> str:
    """优先取平台错误体里的 message，取不到就用正文开头。"""
    try:
        payload = response.json()
    except ValueError:
        return response.text_snippet(160) or "（空响应体）"

    error = payload.get("error")
    if isinstance(error, dict):
        parts = [str(error.get(key)) for key in ("code", "type", "message") if error.get(key)]
        if parts:
            return " / ".join(parts)
    if isinstance(error, str) and error:
        return error
    for key in ("message", "msg", "detail"):
        if payload.get(key):
            return str(payload[key])
    return response.text_snippet(160) or "（无错误描述）"
