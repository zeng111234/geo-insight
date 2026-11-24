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
