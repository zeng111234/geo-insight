"""采集器协议：向某个模型/平台提一个问题，拿回一段答案原文。

这一层存在的唯一理由是**把"怎么拿"和"拿来之后怎么处理"隔开**。
现在没有平台 API 权限，所以只有离线适配器；等拿到权限，新增一个实现即可，
写入、指标、导出都不用动。

刻意不做的事：不在这里做提及识别和引用抽取。那是"拿到原文之后"的工序，
属于分析层；混在采集器里会让每个新适配器都得重写一遍标注逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


class CollectorError(RuntimeError):
    """采集失败。code 供上层区分是"没配"还是"这次没取到"。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CollectRequest:
    question_text: str
    model_key: str
    channel: str = "api"
    sample_idx: int = 1


@dataclass
class CollectResult:
    text: str
    model_key: str
    channel: str
    collected_at: str
    model_version: str | None = None
    latency_ms: int | None = None
    token_in: int | None = None
    token_out: int | None = None
    cost: float | None = None
    raw_uri: str = ""
    raw: dict = field(default_factory=dict)


@runtime_checkable
class Collector(Protocol):
    """一个采集器要能被注册表接收，至少要有这三样属性和三个方法。"""

    key: str
    display_name: str
    kind: str  # "api" | "offline"

    def available(self) -> bool:
        """现在能不能用（有没有配路径、有没有 key）。UI 与 CLI 靠它决定是否展示。"""

    def supports(self, model_key: str) -> bool:
        """能不能采这个模型。"""

    def collect(self, request: CollectRequest) -> CollectResult:
        """取一条。取不到要抛 CollectorError，不要返回空字符串冒充成功。"""
