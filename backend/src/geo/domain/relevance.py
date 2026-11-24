"""核心词：解析、校验、以及「答案是否跑题」的判断。

为什么要核心词：光看「本品牌是否被提及」分不清两件事——
答案在聊这个行业但没提我们（正常，说明没排上），还是答非所问（采集或提问有问题）。
配上核心词就能把后者标出来，避免把跑题的样本当成本品牌的负面表现来解读。

判断只看正文是否出现任一个核心词，不做语义分析：
- 命中 = 不跑题；一个都没命中 = 疑似跑题。
- 没配核心词时返回 None，表示"未判定"，不能当成 False 或 True。
"""

from __future__ import annotations

import json

MAX_KEYWORDS = 20
MAX_KEYWORD_LEN = 20


def parse_keywords(raw: object) -> list[str]:
    """把各种来源的核心词统一成去重后的列表。

    DB 里存 JSON 字符串，请求里可能是数组或逗号分隔的字符串（中英文逗号都认）。
    解析失败一律当空列表，不抛异常——配置脏了不该让整个页面打不开。
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                raw = json.loads(text)
            except ValueError:
                return []
        else:
            raw = text.replace("，", ",").split(",")
    if not isinstance(raw, (list, tuple)):
        return []

    words: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        word = item.strip()
        if word and word not in words:
            words.append(word)
    return words


def validate_keywords(words: list[str]) -> str | None:
    """返回错误文案；None 表示合法。"""
    if len(words) > MAX_KEYWORDS:
        return f"核心词最多 {MAX_KEYWORDS} 个，当前 {len(words)} 个"
    for word in words:
        if len(word) > MAX_KEYWORD_LEN:
            return f"核心词「{word[:8]}…」超过 {MAX_KEYWORD_LEN} 个字"
    return None


def is_off_topic(text: str | None, keywords: list[str]) -> bool | None:
    """正文一个核心词都没有 -> True；没配核心词 -> None（未判定）。"""
    if not keywords:
        return None
    body = (text or "").lower()
    return not any(word.lower() in body for word in keywords)
