"""提及识别 v1：从答案原文里找品牌（含别名）的提及。

为什么是规则匹配而不是模型判定：这一层的职责是「口径可复现」——同一份别名
配置和同一段原文，今天跑和下个月跑必须得到同一条 mention，指标才可对账；
模型判读挪到有人工标注基准之后再上（roadmap 风险登记第二条的预案就是
「人工标注作为唯一事实源，识别只做预填」）。规则四条：

- **精确**：别名逐字出现在原文里（``match_type='exact'``）。
- **归一化**：大小写折叠 + NFKC 全半角折叠后才出现（``match_type='normalized'``，
  如 ``Liaozheng`` 命中 ``LiaoZheng``、全角「ＬＩＡＯ」命中半角）。库是标准库
  ``unicodedata``，不引第三方依赖（决策 002 的零依赖原则）。
- **常见错写**：靠 ``brand_alias`` 里 ``alias_type='misspell'`` 的人工清单逐字匹配，
  不做「猜你想打什么」的模糊匹配——错写清单是用户自己维护的，错了改清单即可。
- **纯拉丁字母/数字的别名加词边界**（``LEGO`` 不命中 ``LEGOLAND``）；含 CJK 的
  别名用子串（中文没有词边界可依赖）。

两条口径决策，都在 003 spec 的 Edge 里定过：

- **否定句照算提及**（「不建议选辽征」是提及但负面，不是未提及）。漏检比误判
  更伤指标——提及率分母是全部有效样本，一条漏检直接把分子做小。情感只给粗值：
  命中点附近有否定线索 → ``neg``，否则 ``neu``；正面情感不做规则猜测。
- **列表位次**：命中行是有序列表（``1. xxx`` / ``1、xxx``）时解析出行号作为
  ``position``，无列表命中不给位次——平均位次的分母只认带位次样本。

同一答案同一品牌只产一条 mention（表上有 UNIQUE 约束）；多个别名都命中时，
取「精确优先、起点靠前、别名更长」的那个做主命中，位次取任意命中里最先
出现在列表行的那个。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# 否定线索：出现在命中点附近就把情感判 neg。宁可漏判正面，不可把「提及」漏掉。
NEGATION_CUES = (
    "不推荐",
    "不建议",
    "不选择",
    "不选",
    "别选",
    "不如",
    "避免",
    "避开",
    "慎选",
    "谨慎",
    "不值得",
    "不靠谱",
    "口碑差",
    "差评",
    "投诉",
    "排除",
)
# 有序列表行：行首序号 + 分隔符。中文顿号、全角句点、右括号都认。
LIST_LINE_RE = re.compile(r"^\s*(\d+)\s*[.、．)）]")
# 纯 ASCII 字母数字（需要词边界）；含 CJK 的走子串。
_ASCII_RE = re.compile(r"^[A-Za-z0-9 _\-&.']+$")
# 否定线索向命中点前方看多远（字符数）：一句话以内。
_NEGATION_WINDOW = 30


@dataclass(frozen=True)
class MentionHit:
    """一次识别出的提及。坐标相对「命中的那份文本」（精确=原文，归一化=折叠后）。"""

    alias: str
    match_type: str  # exact | normalized
    start: int
    end: int
    position: int | None
    sentiment: str  # neg | neu
    sentiment_confidence: float
    snippet: str


def normalize_text(text: str | None) -> str:
    """大小写折叠 + 全半角折叠。识别口径与展示无关，识别一律在这份文本上做。"""
    return unicodedata.normalize("NFKC", text or "").casefold()


def _find_all(haystack: str, needle: str) -> list[int]:
    """子串全部出现位置。needle 为空不查，避免无限循环。"""
    if not needle:
        return []
    starts: list[int] = []
    start = haystack.find(needle)
    while start != -1:
        starts.append(start)
        start = haystack.find(needle, start + 1)
    return starts


def _find_bounded(haystack: str, needle: str) -> list[int]:
    """词边界匹配（纯拉丁别名用）。前后都不是字母数字才算一次命中。"""
    if not needle:
        return []
    pattern = re.compile(
        r"(?<![A-Za-z0-9])" + re.escape(needle) + r"(?![A-Za-z0-9])"
    )
    return [m.start() for m in pattern.finditer(haystack)]


def _line_bounds(text: str, start: int) -> tuple[int, int]:
    """命中点所在行的 [起, 止)。"""
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", start)
    if line_end == -1:
        line_end = len(text)
    return line_start, line_end


def _list_position(text: str, start: int) -> int | None:
    """命中行是有序列表行时返回行号，否则 None。"""
    line_start, _ = _line_bounds(text, start)
    line = text[line_start : line_start + 24]
    match = LIST_LINE_RE.match(line)
    return int(match.group(1)) if match else None


def _sentiment(text: str, start: int, end: int) -> tuple[str, float]:
    """否定线索扫描：命中点前一段 + 命中行内。有线索 neg/0.6，没有 neu/0.5。"""
    line_start, line_end = _line_bounds(text, start)
    context = text[max(0, start - _NEGATION_WINDOW) : line_end]
    # 命中点之后同一个词里带的否定（「辽征不行」）也看一小段。
    context += text[end : end + 12]
    for cue in NEGATION_CUES:
        if cue in context:
            return "neg", 0.6
    return "neu", 0.5


def _snippet(text: str, start: int, end: int) -> str:
    """命中点前后各 40 字符的上下文，给人复核用的，不做截断标记。"""
    return text[max(0, start - 40) : min(len(text), end + 40)].replace("\n", " ").strip()


def _alias_hits(raw: str, folded: str, alias: str) -> list[MentionHit]:
    """单个别名在一段文本里的全部命中。精确优先：原文逐字有就不做归一化。"""
    hits: list[MentionHit] = []

    def _add(text: str, match_type: str, starts: list[int]) -> None:
        for start in starts:
            end = start + len(alias)
            sentiment, confidence = _sentiment(text, start, end)
            hits.append(
                MentionHit(
                    alias=alias,
                    match_type=match_type,
                    start=start,
                    end=end,
                    position=_list_position(text, start),
                    sentiment=sentiment,
                    sentiment_confidence=confidence,
                    snippet=_snippet(text, start, end),
                )
            )

    if _ASCII_RE.match(alias):
        # 拉丁别名的精确命中也要词边界，否则「Lego」会命中「Legoland」的原文。
        pattern = re.compile(r"(?<![A-Za-z0-9])" + re.escape(alias) + r"(?![A-Za-z0-9])")
        exact = [m.start() for m in pattern.finditer(raw)]
        if exact:
            _add(raw, "exact", exact)
            return hits
        starts = _find_bounded(folded, alias.casefold())
        if starts:
            _add(folded, "normalized", starts)
        return hits

    exact = _find_all(raw, alias)
    if exact:
        _add(raw, "exact", exact)
        return hits
    starts = _find_all(folded, alias.casefold())
    if starts:
        _add(folded, "normalized", starts)
    return hits


def detect(text: str | None, aliases: list[str]) -> MentionHit | None:
    """一段原文 + 别名清单 → 至多一条主命中（None = 未提及）。

    主命中取「精确优先，起点靠前，别名更长」；列表位次取所有命中里最先
    出现在列表行上的那个（散文命中 + 列表命中并存时，位次属于列表）。
    """
    if not text or not aliases:
        return None
    raw = text
    folded = normalize_text(text)

    hits: list[MentionHit] = []
    seen: set[str] = set()
    for alias in aliases:
        alias = (alias or "").strip()
        if not alias or alias in seen:
            continue
        seen.add(alias)
        hits.extend(_alias_hits(raw, folded, alias))
    if not hits:
        return None

    # 精确优先、起点靠前、同起点更长的别名赢（全名比它内部的短别名更该被记录）。
    exact = [h for h in hits if h.match_type == "exact"]
    pool = exact or hits
    primary = min(pool, key=lambda h: (h.start, -(h.end - h.start)))

    position = primary.position
    if position is None:
        for hit in sorted(hits, key=lambda h: h.start):
            if hit.position is not None:
                position = hit.position
                break

    return MentionHit(
        alias=primary.alias,
        match_type=primary.match_type,
        start=primary.start,
        end=primary.end,
        position=position,
        sentiment=primary.sentiment,
        sentiment_confidence=primary.sentiment_confidence,
        snippet=primary.snippet,
    )


def evaluate(samples: list[dict], aliases: list[str]) -> dict:
    """在人工标注样本上算精确率/召回率，并给出漏检与误报清单。

    样本格式：``{"text": str, "mentioned": bool}``。精确率/召回率都按
    「样本级是否提及」算（一条答案对自品牌只有一条 mention，样本级与
    记录级等价）。漏检（misses）是召回的缺口、误报（false_alarms）是
    精确的缺口，两边分开列，改别名清单时知道该看哪份。
    """
    true_positive = 0
    false_positive = 0
    misses: list[dict] = []
    false_alarms: list[dict] = []
    for index, sample in enumerate(samples, start=1):
        text = sample.get("text", "")
        labeled = bool(sample.get("mentioned"))
        predicted = detect(text, aliases) is not None
        if labeled and predicted:
            true_positive += 1
        elif labeled and not predicted:
            misses.append({"index": index, "text": text, "note": sample.get("note", "")})
        elif not labeled and predicted:
            false_positive += 1
            false_alarms.append({"index": index, "text": text, "note": sample.get("note", "")})

    labeled_positive = true_positive + len(misses)
    predicted_positive = true_positive + false_positive
    precision = true_positive / predicted_positive if predicted_positive else 0.0
    recall = true_positive / labeled_positive if labeled_positive else 0.0
    return {
        "samples": len(samples),
        "true_positive": true_positive,
        "precision": precision,
        "recall": recall,
        "misses": misses,
        "false_alarms": false_alarms,
    }
