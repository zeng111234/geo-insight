"""H4-5 最小可观测：结构化请求日志 + 进程内错误率/慢请求窗口。

决策 012 只保留「最小日志与健康页」（原 2.0 人日的完整可观测栈不做）。
所以这里刻意做小：

- 一条请求一行 JSON：method / path / status / duration_ms / request_id。
  不落盘、不联网、不依赖第三方——1–2 人内部工具，日志跟着控制台走就够。
- 进程内环形窗口：最近 N 个请求的错误率、慢请求数、P95，由 /health 读出。
  只看内存、重启即清零，不假装成持久化监控。
- 跳过 /health 与 /static：它们是看板自己的探针和静态资源，记进来只会污染分母。

口径（与项目既有纪律一致）：
- 错误只算 5xx。4xx 是客户端传错参数，不算服务出错。
- 0 请求时 error_rate 返回 None，不是 0 —— "0/0 不是 0%"，跟采集健康同一套口径。
- path 只记 URL 路径、不记 query string，避免把 token 之类的参数写进日志。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections import deque
from dataclasses import dataclass

LOG = logging.getLogger("geo.requests")
if not LOG.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    LOG.addHandler(_handler)
LOG.setLevel(logging.INFO)
LOG.propagate = False  # 不让 uvicorn/root 再打一遍同一条

WINDOW = max(1, int(os.getenv("GEO_LOG_WINDOW", "200")))
SLOW_MS = max(1, int(os.getenv("GEO_SLOW_MS", "1000")))


@dataclass(frozen=True)
class RequestRecord:
    method: str
    path: str
    status: int
    duration_ms: int
    request_id: str


class RequestWindow:
    """最近的请求窗口。线程安全——uvicorn 多 worker 时每进程各一份，互不影响。"""

    def __init__(self, size: int = WINDOW, slow_ms: int = SLOW_MS):
        self._rows: deque[RequestRecord] = deque(maxlen=max(1, size))
        self._lock = threading.Lock()
        self.slow_ms = max(1, slow_ms)

    def observe(
        self, method: str, path: str, status: int, duration_ms: int, request_id: str
    ) -> None:
        # 跳过自观测与静态资源，避免健康探针每看一次就把分母 +1
        if path == "/health" or path.startswith("/static/"):
            return
        row = RequestRecord(method, path, status, max(0, duration_ms), request_id)
        with self._lock:
            self._rows.append(row)

        payload = {
            "method": method,
            "path": path,
            "status": status,
            "duration_ms": row.duration_ms,
            "request_id": request_id,
        }
        if status >= 500:
            level = logging.ERROR
            payload["error"] = "server"
        elif row.duration_ms >= self.slow_ms:
            level = logging.WARNING
            payload["slow"] = True
        else:
            level = logging.INFO
        LOG.log(level, json.dumps(payload, ensure_ascii=False))

    def stats(self) -> dict:
        with self._lock:
            rows = list(self._rows)
        total = len(rows)
        if not total:
            # 0/0 不是 0%：没有样本就不给结论，与采集健康同一纪律
            return {
                "requests": 0,
                "errors": 0,
                "error_rate": None,
                "slow": 0,
                "slow_threshold_ms": self.slow_ms,
                "p95_ms": None,
            }
        errors = sum(1 for r in rows if r.status >= 500)
        slow = sum(1 for r in rows if r.duration_ms >= self.slow_ms)
        durations = sorted(r.duration_ms for r in rows)
        return {
            "requests": total,
            "errors": errors,
            "error_rate": round(errors / total, 4),
            "slow": slow,
            "slow_threshold_ms": self.slow_ms,
            "p95_ms": durations[int(0.95 * (len(durations) - 1))],
        }
