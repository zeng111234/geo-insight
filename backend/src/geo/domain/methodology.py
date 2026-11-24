"""口径说明（FR-019 / SC-008）.

Written once here so the page, the API and the report all quote the same
wording. Changing a formula means changing this file and bumping
``metric_version`` — never editing one screen's copy.
"""

from geo.settings import settings

DEFINITIONS = [
    {
        "key": "mention_rate",
        "label": "提及率",
        "formula": "提及样本数 / 有效样本数",
        "denominator": "有效样本（失败、超时、空答案不计入）",
        "note": "一条答案里品牌出现多次只算 1 个提及样本。",
    },
    {
        "key": "coverage_rate",
        "label": "覆盖率",
        "formula": "有提及的问题数 / 当前启用的问题数",
        "denominator": "问题，不是样本",
        "note": "与提及率分母不同，两者必须分别命名，不得混用。",
    },
    {
        "key": "avg_rank",
        "label": "平均排名",
        "formula": "带位次的提及样本，其列表序号的平均值",
        "denominator": "位次非空的提及样本",
        "note": "位次取答案推荐清单或编号列表中的序号（从 1 开始）；只在正文出现、没有列表的答案记为「无列表样本」，不参与平均排名。",
    },
    {
        "key": "top_n",
        "label": "Top1 / Top3 / Top10",
        "formula": "位次 ≤ N 的样本 / 位次非空的样本",
        "denominator": "位次非空的提及样本",
        "note": "无列表样本不计入分母，否则 TopN 会被系统性压低。",
    },
    {
        "key": "sentiment",
        "label": "情感分布",
        "formula": "正/中/负片段数 / 提及样本数",
        "denominator": "提及样本",
        "note": "以品牌相关片段为单位判断；同答案多片段取多数，平票按「负面 > 中性 > 正面」。未提及品牌的答案不进入分母。",
    },
    {
        "key": "citation",
        "label": "引用",
        "formula": "引用次数按 URL 出现次数累计；内容明细按 URL 去重",
        "denominator": "出现次数（计数）/ 唯一 URL（明细）",
        "note": "同一 URL 在一个答案里出现两次，计数为 2，明细只列 1 行。",
    },
]

RULES = [
    f"有效样本低于 {settings.min_samples} 条时，只显示「样本不足」，不显示 0%、100%、平均排名或涨跌幅。",
    "每个指标按自身分母独立判断是否样本不足：提及率看有效样本，平均排名与 TopN 看位次非空样本，情感看提及样本。",
    "采样失败（超时、限流、拒答、空答案）不计入有效样本，也不进入任何指标的分母，单独统计失败率。",
    "信源发布时间无法识别时归入「未知」，不推测补齐；「未知」占比超过 50% 时提示数据质量。",
    "报告是生成时刻的不可变快照；模型集合、竞品集合版本或口径版本不一致时标注不可比，不输出涨跌幅。",
    "口径变更必须同时满足：metric_version 递增、经确认、通过黄金样本回归。",
]


def methodology() -> dict:
    return {
        "metric_version": settings.metric_version,
        "min_samples": settings.min_samples,
        "no_list_alert_threshold": settings.no_list_alert_threshold,
        "sampling_failure_threshold": settings.sampling_failure_threshold,
        "definitions": DEFINITIONS,
        "rules": RULES,
    }
