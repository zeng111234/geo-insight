# -*- coding: utf-8 -*-
"""引用的通用过滤口径(H2-7,003 FR-004)。

**只有这一份实现**(spec Edge 硬约束:H2-7 的引用类型筛选与 H2.5 E1b 的
「全部/已监控」筛选必须同一份逻辑)。两个 HTTP 端点(citations/summary、
citations/detail)都从这里取合法值;E1b 将来加 owner 维度时也在这里扩,
不允许谁自己再写一套枚举校验。
"""

from __future__ import annotations

CITATION_TYPES = ("inline", "reference", "video")

CITATION_TYPE_TEXT = {
    "inline": "正文内链",
    "reference": "参考资料",
    "video": "视频引用",
}


def citation_type_filter(raw: str | None) -> str | None:
    """解析 citeType 查询参数:None/空串 = 不过滤;非法值抛 ValueError。"""
    value = (raw or "").strip() or None
    if value is None:
        return None
    if value not in CITATION_TYPES:
        raise ValueError(f"citeType must be one of {CITATION_TYPES}")
    return value
