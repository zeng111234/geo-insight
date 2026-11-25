"""采集器注册表。

注册是显式的（见 collectors/__init__.py），不做目录扫描：
自动发现会让"为什么这个采集器没生效"变成猜谜。
"""

from __future__ import annotations

from geo.collectors.base import Collector, CollectorError

_REGISTRY: dict[str, Collector] = {}


def register(collector: Collector) -> Collector:
    if not collector.key:
        raise ValueError("采集器必须有 key")
    _REGISTRY[collector.key] = collector
    return collector


def get(key: str) -> Collector:
    collector = _REGISTRY.get(key)
    if collector is None:
        known = "、".join(sorted(_REGISTRY)) or "（空）"
        raise CollectorError("UNKNOWN_COLLECTOR", f"没有这个采集器：{key}；已注册：{known}")
    return collector


def all_collectors() -> list[Collector]:
    return list(_REGISTRY.values())


def available() -> list[Collector]:
    return [c for c in _REGISTRY.values() if c.available()]


def first_available() -> Collector | None:
    items = available()
    return items[0] if items else None


def for_model(model_key: str) -> Collector | None:
    """挑一个「既可用、又支持这个模型」的采集器。

    不能直接用 ``first_available()``：注册顺序里离线库在第一位，
    它「可用」但不一定认识 doubao；而换了顺序又会变成「有 key 却用了离线库」。
    两个问题都不该由注册顺序决定，所以这里按能力筛，并且 **api 优先于 offline**。

    api 优先的理由：离线库是演练用的，配置了 key 就说明用户想采真数据；
    真采失败应该报错让人看见，而不是悄悄退回一份旧答案。
    """
    candidates = [
        collector
        for collector in _REGISTRY.values()
        if collector.available() and collector.supports(model_key)
    ]
    if not candidates:
        return None
    online = [c for c in candidates if c.kind == "api"]
    return (online or candidates)[0]


def supported_models() -> list[str]:
    """所有「现在能用」的采集器能采的模型，供 CLI 提示。

    从采集器反推而不是另建一张表：多一张表就多一处会过期的真相。
    """
    models: list[str] = []
    for collector in available():
        for model in getattr(collector, "MODEL_KEYS", ()):
            if model not in models:
                models.append(model)
    return models
