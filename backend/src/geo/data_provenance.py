"""数据来源判定：把一条回答"是真采集还是演示数据"说清楚。

为什么需要这个模块：库里同时存在两类数据——
- 1510 条种子/演示数据（raw_uri = s3://geo-raw/demo/...），是造出来把链路跑通、把界面填满的；
- 6 条真实平台采集（raw_uri = collect://raw/...），是真调 DeepSeek API 拿回来的。

它们混在同一个看板里，界面上看起来一模一样，用户分不清哪些能当真、哪些只是演示。
本模块是**唯一权威判定点**：前端标签、接口字段、汇总横幅都从这里取值，不在各处散落
字符串判断。判定只读 raw_uri / import_batch_id，不改任何数据。

来源种类（稳定枚举，前端可据此显示）：
- real：真实平台 API 采集（collect://raw/...）
- offline：离线答案库/演练（collect://csv_bank/...、collect://rehearse...、collect://<other>）
- demo：种子/演示数据（s3://geo-raw/demo/...）
- import：外部导入（import_batch_id 非空，且不是上面几类）
- unknown：无法判定（不猜，明确标出来）

**诚实原则**：拿不准就归 unknown，不假装成 real。宁可显示"来源未知"，
也不能把演示数据标成真实采集——那正是本模块要消灭的问题。
"""

from __future__ import annotations

# 真实平台采集的 raw_uri 前缀。只有带权威后缀的才算真的，不能把 collect:// 一律当真。
_REAL_PREFIX = "collect://raw/"

# 演示/种子数据前缀。
_DEMO_PREFIX = "s3://geo-raw/demo/"

# 离线/演练采集前缀（不是真平台 API）。
_OFFLINE_PREFIX = "collect://csv_bank/"
_REHEARSE_PREFIX = "collect://rehearse"

REAL = "real"
OFFLINE = "offline"
DEMO = "demo"
IMPORT = "import"
UNKNOWN = "unknown"

# 前端展示文案与样式类，集中一处避免各页各写一套。
LABELS = {
    REAL: "真实采集",
    OFFLINE: "离线样例",
    DEMO: "演示数据",
    IMPORT: "导入数据",
    UNKNOWN: "来源未知",
}

STYLES = {
    REAL: "source-real",
    OFFLINE: "source-offline",
    DEMO: "source-demo",
    IMPORT: "source-import",
    UNKNOWN: "source-unknown",
}


def source_of(answer: dict) -> str:
    """判定一条回答的来源种类。answer 需含 raw_uri，可含 import_batch_id。

    判据只看 raw_uri 前缀（与写入端约定一致），拿不准归 unknown。
    """
    raw_uri = (answer.get("raw_uri") or "").strip()
    if raw_uri.startswith(_REAL_PREFIX):
        return REAL
    if raw_uri.startswith(_DEMO_PREFIX):
        return DEMO
    if raw_uri.startswith(_OFFLINE_PREFIX) or raw_uri.startswith(_REHEARSE_PREFIX):
        return OFFLINE
    # collect:// 其余形式（如 collect://deepseek 兜底）——不是 raw/ 真采集，归离线。
    if raw_uri.startswith("collect://"):
        return OFFLINE
    if answer.get("import_batch_id"):
        return IMPORT
    return UNKNOWN


def is_real(answer: dict) -> bool:
    """是否真实平台采集。只有 source_of == real 才返回 True。"""
    return source_of(answer) == REAL


def label(kind: str) -> str:
    return LABELS.get(kind, LABELS[UNKNOWN])


def style(kind: str) -> str:
    return STYLES.get(kind, STYLES[UNKNOWN])


def summarize(answers: list[dict]) -> dict:
    """汇总一批回答的数据构成，供页面顶部横幅用。

    返回 {total, by_kind: {kind: count}, real_count, has_mixed}。
    has_mixed=True 表示这批数据里既有真实采集又有非真实数据——
    正是"真真假假"需要提醒用户的情况。
    """
    by_kind: dict[str, int] = {}
    for answer in answers:
        kind = source_of(answer)
        by_kind[kind] = by_kind.get(kind, 0) + 1
    real_count = by_kind.get(REAL, 0)
    return {
        "total": len(answers),
        "by_kind": by_kind,
        "real_count": real_count,
        "has_mixed": real_count > 0 and any(k != REAL for k in by_kind),
    }
