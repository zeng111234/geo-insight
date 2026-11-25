"""采集用的最小 HTTP 客户端：只做「POST 一个 JSON，拿回状态码 + 正文」。

为什么不用 request / httpx：

- ``requirements.txt`` 目前只有 starlette + uvicorn，为两个 API 加一个 HTTP 库
  得跟着维护版本冲突。标准库 urllib 够用，且没有依赖就多一台机器也能跑。
- 采集只需要 POST JSON + 超时，不需要连接池、重试策略、cookie 罐。

**刻意不抛 HTTPError**：4xx/5xx 是「拿到了响应但内容不对」，和「压根没连上」
是两类失败，上层要分别归类（前者看状态码，后者只能算超时/网络）。
把 HTTPError 统一转成 status 返回，分类逻辑就只在一处。

``NetworkError.reason`` 只有两个值：``TIMEOUT`` 与 ``CONNECTION``。
再细分（DNS / TLS / 拒绝连接）对使用者没有区别——都是「这次没取到，等会再试」。
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

# 默认超时。联网搜索要等服务端抓网页，比普通对话慢；60 秒是实测留出的余量，
# 生产里如果频繁超时，先看是不是问题太长导致模型搜了太多轮。
DEFAULT_TIMEOUT = 60.0


class NetworkError(RuntimeError):
    """没拿到 HTTP 响应。reason 只区分「超时」与「连不上/连断了」。"""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason  # "TIMEOUT" | "CONNECTION"


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes
    elapsed_ms: int
    headers: dict[str, str] = field(default_factory=dict)

    def json(self) -> dict:
        """解析正文。不是 JSON 就抛 ValueError —— 由调用方归成 BAD_RESPONSE。"""
        try:
            loaded = json.loads(self.body.decode("utf-8", "replace"))
        except ValueError as exc:
            raise ValueError(f"响应不是 JSON：{self.body[:200]!r}") from exc
        if not isinstance(loaded, dict):
            raise ValueError(f"响应顶层不是对象：{type(loaded).__name__}")
        return loaded

    def text_snippet(self, limit: int = 300) -> str:
        return self.body.decode("utf-8", "replace")[:limit]


# 可替换的传输函数签名：(url, payload, headers, timeout) -> HttpResponse
Transport = Callable[[str, dict, dict[str, str], float], HttpResponse]


def post_json(
    url: str,
    payload: dict,
    headers: dict[str, str],
    timeout: float = DEFAULT_TIMEOUT,
) -> HttpResponse:
    """POST 一个 JSON 对象，返回状态码与正文。连不上就抛 NetworkError。"""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/json")
    for key, value in headers.items():
        request.add_header(key, value)

    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            elapsed = int((time.perf_counter() - started) * 1000)
            return HttpResponse(
                status=response.status,
                body=body,
                elapsed_ms=elapsed,
                headers={k.lower(): v for k, v in response.headers.items()},
            )
    except urllib.error.HTTPError as exc:
        # 4xx/5xx：服务端回了响应，只是状态码不对。正文里通常带着错误原因，
        # 留着给上层写进 raw，排查时不用再手敲一次。
        body = exc.read() if hasattr(exc, "read") else b""
        elapsed = int((time.perf_counter() - started) * 1000)
        return HttpResponse(status=exc.code, body=body, elapsed_ms=elapsed)
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, (TimeoutError, socket.timeout)):
            raise NetworkError("TIMEOUT", f"{timeout:.0f} 秒内没有响应") from exc
        raise NetworkError("CONNECTION", f"连不上 {url}：{reason}") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise NetworkError("TIMEOUT", f"{timeout:.0f} 秒内没有响应") from exc
    except OSError as exc:
        raise NetworkError("CONNECTION", f"网络异常：{exc}") from exc
