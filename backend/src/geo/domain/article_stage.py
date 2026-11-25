"""内容工作室六阶段的唯一口径（决策 009 / roadmap E3）。

参考系统流水线：信源研究 → 事实与证据 → 正文编辑 → 质量审核 → 渠道稿 → 发布。

为什么阶段状态落库而不是前端步骤条：
- 刷新后要停在原阶段，换电脑、换浏览器也要接得上；
- 「这步做没做完」是生产状态，不是浏览状态；
- 发布阶段复用已真实的渠道链路（决策 011），阶段是它的前置门槛。

状态三值：pending / active / done。同一篇文章同一时刻只有一个 active，
数据库用部分唯一索引兜底，不靠代码自觉。

阶段集合是产品定义，domain 是唯一权威；数据库不存 CHECK 约束，
将来加阶段不用改表。
"""

from __future__ import annotations

RESEARCH = "research"
EVIDENCE = "evidence"
DRAFT = "draft"
REVIEW = "review"
CHANNEL = "channel"
PUBLISH = "publish"

# 顺序即流水线顺序，界面步骤条与推进逻辑都读这一份
STAGES: tuple[tuple[str, str], ...] = (
    (RESEARCH, "信源研究"),
    (EVIDENCE, "事实与证据"),
    (DRAFT, "正文编辑"),
    (REVIEW, "质量审核"),
    (CHANNEL, "渠道稿"),
    (PUBLISH, "发布"),
)
STAGE_KEYS: frozenset[str] = frozenset(key for key, _ in STAGES)
STAGE_LABELS: dict[str, str] = dict(STAGES)

PENDING, ACTIVE, DONE = "pending", "active", "done"
VALID_STATUSES = frozenset({PENDING, ACTIVE, DONE})


def is_known_stage(value: object) -> bool:
    return value in STAGE_KEYS


def next_stage(key: str) -> str | None:
    """下一阶段；最后一阶段返回 None（发布完成没有下一步）。"""
    for index, (candidate, _) in enumerate(STAGES):
        if candidate == key:
            return STAGES[index + 1][0] if index + 1 < len(STAGES) else None
    return None


def initial_status(article_status: str, stage_key: str) -> str:
    """历史文章的初始阶段状态。

    已发布的文章六阶段全是既成事实，标 done——不该让老文章看起来「还没开始」。
    其余文章第一阶段 active，后面 pending。这不是猜：没发布就不可能走过发布阶段。
    """
    if article_status == "published":
        return DONE
    return ACTIVE if stage_key == STAGES[0][0] else PENDING
