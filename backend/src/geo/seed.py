"""Seed the SQLite MVP with deterministic demo data.

Layout of the dataset:

黄金窗口 2026-11-01..05（口径演示，数值被断言锁死，不要改动）：
- 2026-11-01..03: 9 valid samples (3 days x 3 models x 1 sample), 2 mentions, both ranked
- 2026-11-04:     3 valid samples, 1 mention without a list index
- 2026-11-05:     2 valid samples (insufficient_samples threshold is 3)

富数据窗口 2026-11-06..30（页面密度与内容故事）：
- 12 个问题（4 core x 3 次/日 + 8 long_tail x 1 次/日）x 3 模型 = 60 槽位/日
- 2026-11-06、2026-11-22 采集中断：60 个 run_item 全部 failed，不产生任何答案
- 其余 23 天每天 4 个槽位随机失败（slot_no % 15 == 7），56 条答案/日，共 1288 条
- 提及率随内容投放逐步上行（约 28% → 35% → 42%），发布后效果在报告里可比

Expected checks:
- from=2026-11-01&to=2026-11-03 -> valid_total=9, mention_rate=0.2222
- from=2026-11-01&to=2026-11-04 -> no_list_samples=1, avg_rank base=2
- from=2026-11-05&to=2026-11-05 -> mention_rate.status=insufficient_samples
- run_item 共 1516 条，其中 214 条 failed -> 采样失败率约 14.1%（超 10% 阈值，演示 FR-010 告警；
  失败的 run_item 不产生答案，不进任何指标分母）
- 5 份演示报告（11-01~03 不可比、11-04~05 可比 +23.3pp、以及 3 份富周期周报）
"""

import argparse
import hashlib
from contextlib import closing
import json
import uuid
from datetime import datetime, timedelta

from geo import db
from geo.settings import settings

TENANT = settings.default_tenant_id
BRAND = "aaaaaaaa-0000-0000-0000-000000000001"
TRACKER = "11111111-1111-1111-1111-111111111111"
QUESTION_SET = "22222222-2222-2222-2222-222222222222"
Q1 = "33333333-3333-3333-3333-333333333331"
Q2 = "33333333-3333-3333-3333-333333333332"
Q3 = "33333333-3333-3333-3333-333333333333"
Q4 = "33333333-3333-3333-3333-333333333334"
Q5 = "33333333-3333-3333-3333-333333333335"
Q6 = "33333333-3333-3333-3333-333333333336"
Q7 = "33333333-3333-3333-3333-333333333337"
Q8 = "33333333-3333-3333-3333-333333333338"
Q9 = "33333333-3333-3333-3333-333333333339"
Q10 = "33333333-3333-3333-3333-33333333333a"
Q11 = "33333333-3333-3333-3333-33333333333b"
Q12 = "33333333-3333-3333-3333-33333333333c"

# 12 个问题：4 core + 8 long_tail。注意不要出现「沈阳交通事故律师怎么收费」，
# smoke 的问题导入去重断言依赖该文本在种子里不存在。
QUESTIONS = [
    (Q1, "沈阳哪一家律所最专业", "core"),
    (Q2, "沈阳刑事辩护律师大概需要多少费用", "long_tail"),
    (Q3, "沈阳律师事务所排名前十", "core"),
    (Q4, "沈阳刑事辩护律师哪个好", "core"),
    (Q5, "沈阳律师咨询哪家靠谱", "core"),
    (Q6, "取保候审一般要花多少钱", "long_tail"),
    (Q7, "沈阳离婚律师收费标准", "long_tail"),
    (Q8, "沈阳劳动仲裁律师推荐", "long_tail"),
    (Q9, "沈阳房产纠纷律师怎么找", "long_tail"),
    (Q10, "律师会见一次多少钱", "long_tail"),
    (Q11, "沈阳工伤赔偿律师咨询", "long_tail"),
    (Q12, "醉驾不起诉的可能性大吗", "long_tail"),
]
CORE_QUESTIONS = [q for q in QUESTIONS if q[2] == "core"]
LONG_TAIL_QUESTIONS = [q for q in QUESTIONS if q[2] == "long_tail"]
COMPETITOR_SET = "44444444-4444-4444-4444-444444444444"
BRAND_NAME = "辽宁辽征律师事务所"
ALIASES = [(BRAND_NAME, "full"), ("辽征", "short"), ("辽征律所", "short")]
MODELS = ["doubao", "deepseek", "kimi"]

# 002 内容工作室演示数据
ART_PUBLISHED = "55555555-5555-5555-5555-555555555551"
ART_DRAFT = "55555555-5555-5555-5555-555555555552"
ART_REVIEW = "55555555-5555-5555-5555-555555555553"
ART_PUB2 = "55555555-5555-5555-5555-555555555554"
ART_PUB3 = "55555555-5555-5555-5555-555555555555"
ART_PUBFAIL = "55555555-5555-5555-5555-555555555556"
ART_QUEUED = "55555555-5555-5555-5555-555555555557"
ART_DRAFT2 = "55555555-5555-5555-5555-555555555558"
PUBLISHED_AT = "2026-11-04T10:00:00+08:00"

# 2026-11-24 决策 011(第二批):全部 7 渠道都有发布器了,初始态统一为
# 「未连接」(disconnected,登录态未知待检测)或「待配置密钥」(百家号)。
# connected 只能来自真实检测/登录(POST /v1/channels/{id}/check 或 connect)。
CHANNELS = [
    ("66666666-6666-6666-6666-666666666661", "wechat", "微信公众号", None, "disconnected", None),
    ("66666666-6666-6666-6666-666666666662", "zhihu", "知乎", None, "disconnected", None),
    ("66666666-6666-6666-6666-666666666663", "baijiahao", "百家号", None, "needs_config", None),
    ("66666666-6666-6666-6666-666666666664", "xiaohongshu", "小红书", None, "disconnected", None),
    ("66666666-6666-6666-6666-666666666665", "toutiao", "今日头条", None, "disconnected", None),
    ("66666666-6666-6666-6666-666666666666", "sohu", "搜狐号", None, "disconnected", None),
    ("66666666-6666-6666-6666-666666666667", "douyin", "抖音", None, "disconnected", None),
]


def uid() -> str:
    return str(uuid.uuid4())


def h(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def answer_text(with_list: bool, with_brand: bool, rank: int | None) -> str:
    if with_list:
        lines = ["沈阳较受关注的律所包括："]
        for i in range(1, 6):
            if with_brand and rank == i:
                lines.append(f"{i}. {BRAND_NAME}")
            else:
                lines.append(f"{i}. 某某律师事务所（示例 {i}）")
        lines.append("建议结合案件类型与律师面谈后再决定。")
        return "\n".join(lines)
    if with_brand:
        return f"沈阳有多家律所可以处理此类案件，{BRAND_NAME} 是其中之一，建议先电话咨询并比较收费方式。"
    return "沈阳有多家律所可以处理此类案件，建议先电话咨询并比较收费方式。"


def insert_answer(
    conn,
    day: str,
    model: str,
    sample_idx: int,
    question_id: str,
    with_brand: bool,
    position: int | None,
    sentiment: str,
    with_list: bool,
    run_id: str,
) -> str:
    collected = f"{day}T09:00:00+08:00"
    run_item_id = uid()
    conn.execute(
        """INSERT INTO run_item(id, run_id, question_id, model_key, channel, sample_idx, status, attempted_at)
           VALUES (?, ?, ?, ?, 'api', ?, 'succeeded', ?)""",
        (run_item_id, run_id, question_id, model, sample_idx, collected),
    )
    text = answer_text(with_list, with_brand, position)
    answer_id = uid()
    conn.execute(
        """INSERT INTO answer(id, run_item_id, tenant_id, tracker_id, question_id, model_key, model_version,
                              channel, raw_uri, text, text_hash, collected_at, latency_ms, is_valid)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'api', ?, ?, ?, ?, ?, 1)""",
        (
            answer_id,
            run_item_id,
            TENANT,
            TRACKER,
            question_id,
            model,
            f"{model}-2026-10",
            f"s3://geo-raw/demo/{answer_id}.json",
            text,
            h(text),
            collected,
            1200,
        ),
    )
    if with_list:
        for i in range(1, 6):
            conn.execute(
                """INSERT INTO answer_block(id, answer_id, seq, block_type, text, list_index)
                   VALUES (?, ?, ?, 'list_item', ?, ?)""",
                (uid(), answer_id, i + 1, f"第 {i} 家律所", i),
            )
    if with_brand:
        conn.execute(
            """INSERT INTO mention(id, answer_id, brand_id, subject_type, matched_alias, match_type,
                                   position, position_type, sentiment, sentiment_confidence, snippet)
               VALUES (?, ?, ?, 'own', ?, 'exact', ?, ?, ?, 0.9, ?)""",
            (
                uid(),
                answer_id,
                BRAND,
                BRAND_NAME,
                position,
                "list_index" if position is not None else None,
                sentiment,
                f"{BRAND_NAME} 出现在答案中",
            ),
        )
    for idx, (domain, media, mtype, owner, unknown) in enumerate(
        [
            ("example-news.com", "示例新闻", "news", "media", 0),
            ("video.example.com", "示例视频", "video", "media", 1),
        ],
        start=1,
    ):
        conn.execute(
            """INSERT INTO citation(id, answer_id, url, url_hash, domain, media_name, media_type,
                                    owner_type, position, cite_type, published_at, published_at_unknown)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                uid(),
                answer_id,
                f"https://{domain}/a/{answer_id[:8]}-{idx}",
                h(f"{domain}-{answer_id}-{idx}"),
                domain,
                media,
                mtype,
                owner,
                idx,
                "video" if mtype == "video" else "inline",
                None if unknown else f"{day}T08:00:00+08:00",
                unknown,
            ),
        )
    return answer_id


def seed() -> dict:
    db.init_db()
    # sqlite3 的 with 只提交事务不关连接，这里显式关闭，避免测试与 --reset 泄漏句柄。
    with closing(db.connect()) as conn:
        # H2-6 之后 tracker ↔ question_set 互相引用(双向外键),顺序删除无解;
        # 先把绑定解开再清表
        conn.execute("UPDATE tracker SET active_question_set_id = NULL")
        for table in [
            "report_snapshot",
            "report",
            "audit_log",
            "publication",
            "article_fact",
            "article",
            "channel_account",
            "citation",
            "mention",
            "answer_block",
            "answer",
            # 必须在 answer 之后：answer.import_batch_id 指向它
            "import_batch",
            "run_item",
            "run",
            "sampling_plan",
            "question",
            "question_set",
            "tracker",
            "competitor",
            "competitor_set",
            "brand_alias",
            "brand",
            "app_user",
            "tenant",
        ]:
            conn.execute(f"DELETE FROM {table}")

        now = "2026-11-01T08:00:00+08:00"
        conn.execute(
            "INSERT INTO tenant(id, name, slug, plan, status, created_at) VALUES (?, ?, ?, 'internal', 'active', ?)",
            (TENANT, "Demo 品牌部", "demo", now),
        )
        conn.execute(
            """INSERT INTO app_user(id, tenant_id, email, display_name, role, status, created_at)
               VALUES (?, ?, ?, ?, 'admin', 'active', ?)""",
            (uid(), TENANT, "demo@example.com", "演示用户", now),
        )
        conn.execute(
            "INSERT INTO brand(id, tenant_id, name, industry, region, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (BRAND, TENANT, BRAND_NAME, "律师事务所", "沈阳", now),
        )
        for alias, kind in ALIASES:
            conn.execute(
                "INSERT INTO brand_alias(id, brand_id, alias, alias_type, is_active) VALUES (?, ?, ?, ?, 1)",
                (uid(), BRAND, alias, kind),
            )
        conn.execute(
            "INSERT INTO competitor_set(id, tenant_id, version, created_at) VALUES (?, ?, 1, ?)",
            (COMPETITOR_SET, TENANT, now),
        )
        for name in [
            "北京盈科（沈阳）律师事务所",
            "辽宁同方律师事务所",
            "北京大成（沈阳）律师事务所",
            "上海段和段（沈阳）律师事务所",
            "辽宁成功律师事务所",
        ]:
            conn.execute(
                "INSERT INTO competitor(id, competitor_set_id, name, aliases) VALUES (?, ?, ?, '[]')",
                (uid(), COMPETITOR_SET, name),
            )
        conn.execute(
            """INSERT INTO tracker(id, tenant_id, brand_id, competitor_set_id, name, industry, region,
                                   status, settings, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'active', '{}', ?, ?)""",
            (TRACKER, TENANT, BRAND, COMPETITOR_SET, "辽宁辽征 · 律所", "律师事务所", "沈阳", now, now),
        )
        conn.execute(
            "UPDATE tracker SET keywords = ? WHERE id = ?",
            (
                json.dumps(
                    ["律所", "律师", "刑事辩护", "离婚", "劳动仲裁"], ensure_ascii=False
                ),
                TRACKER,
            ),
        )
        conn.execute(
            "INSERT INTO question_set(id, tenant_id, tracker_id, name, keywords, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (QUESTION_SET, TENANT, TRACKER, "律所核心问题",
             json.dumps(["律所", "律师", "刑事辩护", "离婚", "劳动仲裁"], ensure_ascii=False), now),
        )
        # H2-6:生效集指向主集(黄金窗口口径不变);再挂一个空答案的扩展集供切换演示
        conn.execute("UPDATE tracker SET active_question_set_id = ? WHERE id = ?", (QUESTION_SET, TRACKER))
        QUESTION_SET2 = "22222222-2222-2222-2222-222222222223"
        conn.execute(
            "INSERT INTO question_set(id, tenant_id, tracker_id, name, keywords, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (QUESTION_SET2, TENANT, TRACKER, "长尾扩展集",
             json.dumps(["律所", "律师", "合同纠纷"], ensure_ascii=False), now),
        )
        for text in ("沈阳合同纠纷律师哪家好", "沈阳公司法务咨询收费"):
            conn.execute(
                """INSERT INTO question(id, tenant_id, question_set_id, text, normalized_text, tier, status, created_at)
                   VALUES (?, ?, ?, ?, ?, 'long_tail', 'active', ?)""",
                (uid(), TENANT, QUESTION_SET2, text, text, now),
            )
        for qid, text, tier in QUESTIONS:
            conn.execute(
                """INSERT INTO question(id, tenant_id, question_set_id, text, normalized_text, tier, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'active', ?)""",
                (qid, TENANT, QUESTION_SET, text, text, tier, now),
            )
        conn.execute(
            """INSERT INTO sampling_plan(id, tracker_id, channel, model_keys, core_times, long_tail_times,
                                         timezone, effective_from)
               VALUES (?, ?, 'api', ?, 3, 1, 'Asia/Shanghai', '2026-11-01')""",
            (uid(), TRACKER, json.dumps(MODELS)),
        )

        plan = []
        for day_offset in range(3):
            day = (datetime.fromisoformat("2026-11-01") + timedelta(days=day_offset)).date().isoformat()
            run_id = uid()
            conn.execute(
                """INSERT INTO run(id, tenant_id, tracker_id, trigger, status, scheduled_for, started_at, finished_at, stats)
                   VALUES (?, ?, ?, 'schedule', 'completed', ?, ?, ?, '{}')""",
                (run_id, TENANT, TRACKER, f"{day}T08:00:00+08:00", f"{day}T08:00:00+08:00", f"{day}T08:10:00+08:00"),
            )
            for model in MODELS:
                for sample_idx in [1]:
                    with_brand = (day == "2026-11-01" and model == "doubao") or (
                        day == "2026-11-02" and model == "deepseek"
                    )
                    position = {("2026-11-01", "doubao"): 2, ("2026-11-02", "deepseek"): 5}.get(
                        (day, model)
                    )
                    sentiment = "pos" if position == 2 else "neu"
                    insert_answer(
                        conn, day, model, sample_idx, Q1, with_brand, position, sentiment, True, run_id
                    )
                    plan.append((day, model, sample_idx, with_brand, position))

        run4 = uid()
        conn.execute(
            """INSERT INTO run(id, tenant_id, tracker_id, trigger, status, scheduled_for, started_at, finished_at, stats)
               VALUES (?, ?, ?, 'schedule', 'completed', '2026-11-04T08:00:00+08:00', '2026-11-04T08:00:00+08:00', '2026-11-04T08:05:00+08:00', '{}')""",
            (run4, TENANT, TRACKER),
        )
        for model in MODELS:
            with_brand = model == "kimi"
            insert_answer(conn, "2026-11-04", model, 1, Q1, with_brand, None, "neg", False, run4)

        run5 = uid()
        conn.execute(
            """INSERT INTO run(id, tenant_id, tracker_id, trigger, status, scheduled_for, started_at, finished_at, stats)
               VALUES (?, ?, ?, 'schedule', 'completed', '2026-11-05T08:00:00+08:00', '2026-11-05T08:00:00+08:00', '2026-11-05T08:05:00+08:00', '{}')""",
            (run5, TENANT, TRACKER),
        )
        for model, with_brand, position in [("doubao", True, 1), ("kimi", False, None)]:
            insert_answer(conn, "2026-11-05", model, 1, Q2, with_brand, position, "pos", True, run5)

        # 演示采样失败率（FR-010）：两次失败的 run_item 不产生答案，
        # 因此不会进入任何指标的分母，只会体现在失败率上（2/16 = 12.5%）。
        for model, code in [("doubao", "RATE_LIMITED"), ("deepseek", "TIMEOUT")]:
            conn.execute(
                """INSERT INTO run_item(id, run_id, question_id, model_key, channel, sample_idx,
                                        status, error_code, attempted_at)
                   VALUES (?, ?, ?, ?, 'api', 2, 'failed', ?, '2026-11-05T09:05:00+08:00')""",
                (uid(), run5, Q2, model, code),
            )

        conn.commit()
        seed_rich_period(conn)
        seed_studio(conn)
        seed_alt_tracker(conn)
        seed_catalog_trackers(conn)
        conn.commit()

        # 演示报告也必须是种子数据的一部分：否则用户 --reset 后 /reports 是空的，
        # 报告详情页没有可点的内容，面试演示会断在这一步。
        # 延迟导入避免 seed <-> services 的模块级循环依赖。
        from geo.services import report_service

        report_now = "2026-11-06T09:00:00+08:00"
        report_service.generate(conn, TRACKER, TENANT, "2026-11-01", "2026-11-03", report_now)
        report_service.generate(conn, TRACKER, TENANT, "2026-11-04", "2026-11-05", report_now)
        # 富周期周报：报告中心列表有 5 份，覆盖「不可比 / 可比 + 涨跌」不同状态。
        report_service.generate(conn, TRACKER, TENANT, "2026-11-07", "2026-11-13", "2026-11-14T09:00:00+08:00")
        report_service.generate(conn, TRACKER, TENANT, "2026-11-14", "2026-11-20", "2026-11-21T09:00:00+08:00")
        report_service.generate(conn, TRACKER, TENANT, "2026-11-21", "2026-11-30", "2026-12-01T09:00:00+08:00")

        # 演示账号必须能登录：上面重建了 app_user（没有密码哈希），这里补上。
        # 登录不了等于演示第一步就断了，所以它属于"能用的演示数据"的一部分。
        from geo.services import auth_service

        auth_service.ensure_admin_password(conn)

    return {
        "tenant_id": TENANT,
        "tracker_id": TRACKER,
        "brand": BRAND_NAME,
        "question_ids": {"core": Q1, "long_tail": Q2},
        "articles": {"published": ART_PUBLISHED, "draft": ART_DRAFT, "review": ART_REVIEW},
        "expected": {
            "2026-11-01..2026-11-03": {"valid_total": 9, "mention_total": 2, "mention_rate": 0.2222},
            "2026-11-01..2026-11-04": {"valid_total": 12, "mention_total": 3, "no_list_samples": 1},
            "2026-11-05": {"valid_total": 2, "mention_rate_status": "insufficient_samples"},
            "2026-11-07..2026-11-30": {"valid_total": 1288, "questions": 12},
            "reports": {"count": 5, "comparable_period": "2026-11-04..2026-11-05", "delta_pp": 23.3},
        },
    }


# ---------------------------------------------------------------------------
# 富数据窗口 2026-11-06..30：把演示数据做厚，让页面像成品而不是空壳。
# 黄金窗口 11-01..05 的数据一行都不动；这里只往后追加。
# ---------------------------------------------------------------------------

RICH_START = "2026-11-06"
RICH_END = "2026-11-30"
OUTAGE_DAYS = {"2026-11-06", "2026-11-22"}
FAIL_EVERY = 15  # 每天 60 槽位中 slot_no % 15 == 7 的 4 个失败
ERROR_CODES = ["RATE_LIMITED", "TIMEOUT", "UPSTREAM_ERROR", "AUTH_FAILED"]

# 信源池：domain, media_name, media_type, owner_type, published_at_unknown
# owner_type 只有 brand/competitor/media 三种（schema CHECK），品牌自有渠道记 brand。
CITE_POOL = [
    ("news.example.com", "示例新闻", "news", "media", 0),
    ("video.example.com", "示例视频", "video", "media", 1),
    ("zhihu.example.com", "知乎专栏", "forum", "media", 0),
    ("weixin.example.com", "辽征律所公众号", "wechat", "brand", 0),
    ("law.example.com", "法律资讯网", "news", "media", 0),
    ("blog.example.com", "律师博客", "blog", "media", 0),
    ("baike.example.com", "百科词条", "wiki", "media", 1),
    ("zhuanlan.example.com", "行业专栏", "news", "media", 0),
    ("case.example.com", "裁判文书案例库", "database", "media", 0),
    ("video2.example.com", "视频号", "video", "media", 1),
]

# 热点信源：约 1/4 的引用复用同一批 URL（真实世界里权威稿件会被反复引用）。
# 没有它们，明细页每行 times 都是 1，「按 URL 去重、次数累计」这个特性在页面上看不出来。
HOT_URLS = []
for _i in range(30):
    _d = CITE_POOL[_i % len(CITE_POOL)]
    HOT_URLS.append((f"https://{_d[0]}/hot/{_i + 1}",) + _d)


def _hint(text: str) -> int:
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:12], 16)


def _mention_pct(day: str, tier: str) -> int:
    """内容投放后的提及率爬坡：早期 ~28%，中期 ~35%，后期 ~42%。"""
    if day <= "2026-11-12":
        return 34 if tier == "core" else 18
    if day <= "2026-11-19":
        return 42 if tier == "core" else 24
    return 50 if tier == "core" else 30


def _rich_answer_text(question_text: str, with_list: bool, with_brand: bool, position: int | None) -> str:
    if with_list:
        lines = [f"针对「{question_text}」，沈阳地区比较受关注的律所包括："]
        for i in range(1, 6):
            if with_brand and position == i:
                lines.append(f"{i}. {BRAND_NAME}")
            else:
                lines.append(f"{i}. 某某律师事务所（示例 {i}）")
        lines.append("建议结合案件类型与律师面谈后再决定。")
        return "\n".join(lines)
    if with_brand:
        return (
            f"关于「{question_text}」，沈阳有多家律所可以处理此类案件，"
            f"{BRAND_NAME} 是其中之一，建议先电话咨询并比较收费方式。"
        )
    return f"关于「{question_text}」，沈阳有多家律所可以处理此类案件，建议先电话咨询并比较收费方式。"


def _ensure_competitor_brands(conn) -> list[tuple[str, str]]:
    """竞品在 mention 里也要有 brand 行（外键指向 brand），打上 kind='competitor'。

    没有这一步，竞品提及根本插不进去；若按名字当成 own，指标就会把竞品算成本方
    —— 迁移 003 修的就是这个坑。这里显式写 kind，不靠「在不在 brand 表」反推。
    """
    rows = conn.execute(
        "SELECT id, name FROM brand WHERE tenant_id = ? AND kind = 'competitor'", (TENANT,)
    ).fetchall()
    if rows:
        return [(r["id"], r["name"]) for r in rows]

    names = conn.execute(
        """SELECT c.name FROM competitor c
           JOIN competitor_set cs ON cs.id = c.competitor_set_id
           JOIN tracker t ON t.competitor_set_id = cs.id
           WHERE t.id = ? ORDER BY c.name""",
        (TRACKER,),
    ).fetchall()
    out: list[tuple[str, str]] = []
    for r in names:
        brand_id = uid()
        conn.execute(
            "INSERT INTO brand(id, tenant_id, name, kind, created_at) VALUES (?, ?, ?, 'competitor', ?)",
            (brand_id, TENANT, r["name"], "2026-11-01T08:00:00+08:00"),
        )
        out.append((brand_id, r["name"]))
    return out


def seed_rich_period(conn) -> dict:
    """2026-11-06..30：12 问题 x 3 模型，core 每题 3 次/日、long_tail 1 次/日（60 槽位/日）。

    - 11-06、11-22 采集中断：整日 run_item 全部 failed，run.status='failed'
    - 其余 23 天每天恰好 4 个槽位失败（slot_no % 15 == 7），56 条答案/日，共 1288 条
    - 失败的 run_item 不产生答案、不进任何指标分母（FR-010 口径）
    """
    stats = {"answers": 0, "failed_items": 0, "days": 0}
    comp_brands = _ensure_competitor_brands(conn)
    day = datetime.fromisoformat(RICH_START).date()
    end = datetime.fromisoformat(RICH_END).date()
    while day <= end:
        day_s = day.isoformat()
        outage = day_s in OUTAGE_DAYS
        run_id = uid()
        conn.execute(
            """INSERT INTO run(id, tenant_id, tracker_id, trigger, status, scheduled_for, started_at, finished_at, stats)
               VALUES (?, ?, ?, 'schedule', ?, ?, ?, ?, '{}')""",
            (
                run_id,
                TENANT,
                TRACKER,
                "failed" if outage else "completed",
                f"{day_s}T08:00:00+08:00",
                f"{day_s}T08:00:00+08:00",
                f"{day_s}T08:10:00+08:00",
            ),
        )

        slot_no = 0
        for qid, q_text, tier in QUESTIONS:
            sample_ids = [1, 2, 3] if tier == "core" else [1]
            for model in MODELS:
                for sample_idx in sample_ids:
                    key = f"{day_s}|{qid}|{model}|{sample_idx}"
                    failed = outage or slot_no % FAIL_EVERY == 7
                    slot_no += 1
                    if failed:
                        conn.execute(
                            """INSERT INTO run_item(id, run_id, question_id, model_key, channel, sample_idx,
                                                    status, error_code, attempted_at)
                               VALUES (?, ?, ?, ?, 'api', ?, 'failed', ?, ?)""",
                            (
                                uid(),
                                run_id,
                                qid,
                                model,
                                sample_idx,
                                ERROR_CODES[slot_no % len(ERROR_CODES)],
                                f"{day_s}T09:00:00+08:00",
                            ),
                        )
                        stats["failed_items"] += 1
                        continue

                    with_brand = _hint("m|" + key) % 100 < _mention_pct(day_s, tier)
                    with_list = _hint("l|" + key) % 5 < 3
                    position = 1 + _hint("p|" + key) % 5 if (with_brand and with_list) else None
                    hs = _hint("s|" + key) % 10
                    sentiment = "pos" if hs < 4 else ("neu" if hs < 9 else "neg")
                    alias = ALIASES[_hint("a|" + key) % len(ALIASES)][0]

                    run_item_id = uid()
                    collected = f"{day_s}T09:00:00+08:00"
                    conn.execute(
                        """INSERT INTO run_item(id, run_id, question_id, model_key, channel, sample_idx, status, attempted_at)
                           VALUES (?, ?, ?, ?, 'api', ?, 'succeeded', ?)""",
                        (run_item_id, run_id, qid, model, sample_idx, collected),
                    )
                    text = _rich_answer_text(q_text, with_list, with_brand, position)
                    answer_id = uid()
                    conn.execute(
                        """INSERT INTO answer(id, run_item_id, tenant_id, tracker_id, question_id, model_key, model_version,
                                              channel, raw_uri, text, text_hash, collected_at, latency_ms, is_valid)
                           VALUES (?, ?, ?, ?, ?, ?, ?, 'api', ?, ?, ?, ?, ?, 1)""",
                        (
                            answer_id,
                            run_item_id,
                            TENANT,
                            TRACKER,
                            qid,
                            model,
                            f"{model}-2026-10",
                            f"s3://geo-raw/demo/{answer_id}.json",
                            text,
                            h(text),
                            collected,
                            800 + _hint("lat|" + key) % 1600,
                        ),
                    )
                    stats["answers"] += 1

                    if with_list:
                        for i in range(1, 6):
                            conn.execute(
                                """INSERT INTO answer_block(id, answer_id, seq, block_type, text, list_index)
                                   VALUES (?, ?, ?, 'list_item', ?, ?)""",
                                (uid(), answer_id, i + 1, f"第 {i} 家律所", i),
                            )
                    if with_brand:
                        conn.execute(
                            """INSERT INTO mention(id, answer_id, brand_id, subject_type, matched_alias, match_type,
                                                   position, position_type, sentiment, sentiment_confidence, snippet)
                               VALUES (?, ?, ?, 'own', ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                uid(),
                                answer_id,
                                BRAND,
                                alias,
                                "exact",
                                position,
                                "list_index" if position is not None else None,
                                sentiment,
                                0.75 + (_hint("sc|" + key) % 20) / 100,
                                f"{alias} 出现在答案中",
                            ),
                        )

                    # 竞品提及：只写在 rich 期间（11-06 起），完全避开黄金窗口 11-01..03，
                    # 保证 22.22% / 9 样本那些手算值不受影响。
                    # 竞品只出现在给出列表的答案里 —— 没有列表就没有"排第几"，不该有竞品位次。
                    if with_list and _hint("cp|" + key) % 100 < 55:
                        used_pos = {position} if position is not None else set()
                        for ci in range(1 + _hint("cpn|" + key) % 2):
                            cid, cname = comp_brands[_hint(f"cpb|{key}|{ci}") % len(comp_brands)]
                            pos = 1 + _hint(f"cpp|{key}|{ci}") % 5
                            if pos in used_pos:
                                pos = next((p for p in range(1, 6) if p not in used_pos), None)
                            if pos is None:
                                continue
                            used_pos.add(pos)
                            senti_c = ["pos", "pos", "neu", "neu", "neu", "neg"][
                                _hint(f"cps|{key}|{ci}") % 6
                            ]
                            # INSERT OR IGNORE：UNIQUE(answer_id, brand_id) 保证同一答案
                            # 不会因为循环抽到同一竞品而撞约束
                            conn.execute(
                                """INSERT OR IGNORE INTO mention
                                       (id, answer_id, brand_id, subject_type, matched_alias, match_type,
                                        position, position_type, sentiment, sentiment_confidence, snippet)
                                   VALUES (?, ?, ?, 'competitor', ?, 'exact', ?, 'list_index', ?, ?, ?)""",
                                (
                                    uid(),
                                    answer_id,
                                    cid,
                                    cname,
                                    pos,
                                    senti_c,
                                    0.7 + (_hint(f"cpc|{key}|{ci}") % 20) / 100,
                                    f"{cname} 出现在答案中",
                                ),
                            )

                    n_cites = 2 + _hint("c|" + key) % 2
                    start = _hint("d|" + key) % len(CITE_POOL)
                    for ci in range(n_cites):
                        # 25% 复用热点 URL：同一个 URL 被多条答案引用，明细页 times 才会 > 1
                        if _hint(f"hot|{key}|{ci}") % 100 < 25:
                            url, domain, media, mtype, owner, unknown = HOT_URLS[
                                _hint(f"hn|{key}|{ci}") % len(HOT_URLS)
                            ]
                            url_hash = h(url)
                            published_at = None if unknown else "2026-10-20T08:00:00+08:00"
                        else:
                            domain, media, mtype, owner, unknown = CITE_POOL[(start + ci * 3) % len(CITE_POOL)]
                            url = f"https://{domain}/a/{answer_id[:8]}-{ci + 1}"
                            url_hash = h(f"{domain}-{answer_id}-{ci + 1}")
                            published_at = None if unknown else f"{day_s}T08:00:00+08:00"
                        conn.execute(
                            """INSERT INTO citation(id, answer_id, url, url_hash, domain, media_name, media_type,
                                                    owner_type, position, cite_type, published_at, published_at_unknown)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                uid(),
                                answer_id,
                                url,
                                url_hash,
                                domain,
                                media,
                                mtype,
                                owner,
                                ci + 1,
                                "video" if mtype == "video" else ("reference" if mtype in ("wiki", "database") else "inline"),
                                published_at,
                                unknown,
                            ),
                        )
        stats["days"] += 1
        day += timedelta(days=1)
    return stats


def seed_studio(conn) -> None:
    """内容工作室演示数据：3 篇文章、7 个渠道、2 条已完成发布记录。"""
    now = "2026-11-03T09:00:00+08:00"

    for cid, key, name, account, status, mode in CHANNELS:
        conn.execute(
            """INSERT INTO channel_account
               (id, tenant_id, platform_key, platform_name, account_name, status, login_mode, last_checked_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            # last_checked_at 只在 connected 时有意义;初始诚实态从未检测过
            (cid, TENANT, key, name, account, status, mode, now if status == "connected" else None),
        )

    articles = [
        (
            ART_PUBLISHED,
            "沈阳刑事律师怎么选：三个可核实的判断标准",
            "\n".join(
                [
                    "刑事案件找律师，最怕两件事：一问就承诺结果，二说不清自己做过什么。",
                    "",
                    "## 三个可以核实的判断标准",
                    "",
                    "1. 是否专做刑事。刑事辩护与民商事诉讼的流程、节点、文书完全不同，团队常年只做刑事，"
                    "对会见、阅卷、庭前会议的节奏更熟。",
                    "2. 是否说清辩护思路。第一次沟通就该讲明白：本案的争议焦点在哪、证据链哪一环最薄弱、"
                    "下一步先做什么。只谈关系不谈思路的，要谨慎。",
                    "3. 收费是否写进合同。侦查、审查起诉、审判三个阶段分别如何计费，退费条件是什么，"
                    "都应当落在书面协议里。",
                    "",
                    "## 常见误区",
                    "",
                    "把「保证胜诉」当成专业。任何对结果的承诺都不写进合同，也不该成为选择依据。",
                    "",
                    "辽宁辽征律师事务所在沈阳，团队只做刑事案件，首次沟通会给出书面辩护思路。",
                ]
            ),
            "criminal-law-v2",
            "published",
            3,
            "针对「沈阳刑事律师事务所」创作深度内容，以刑事辩护专业度、本地案例、收费透明度为核心卖点。",
            "该问题下品牌提及率提升至 100%，并进入至少 3 个渠道的前三位。",
            "复测时 5 个渠道回答均提到品牌，且至少 3 个渠道将品牌列入推荐列表前三位。",
            "辽宁辽征律师事务所，沈阳，刑事辩护团队，成立 12 年。",
            "事实性数字需引用可核验来源，未标注来源的一律标记为待核实。",
            "第 7 段「保证胜诉」属合规风险表述，建议改为「提升胜诉可能性」，不得对结果作承诺。",
        ),
        (
            ART_DRAFT,
            "取保候审的适用条件与常见误区",
            "取保候审不是「花钱就能办」。是否批准，取决于社会危险性评估，而不是单一情节。\n\n"
            "## 常见误区\n\n1. 以为退了赃就一定能取保；\n2. 以为批捕之后就没有机会；\n3. 忽略固定住所与保证人的作用。",
            "bail-v1",
            "draft",
            1,
            "回答「取保候审需要什么条件」，承接搜索意图，导向免费评估表。",
            "进入该问题回答的前 5 条推荐，提及率不低于 60%。",
            "3 个模型、连续 3 天，至少 2 天出现品牌名。",
            "辽宁辽征律师事务所，沈阳，刑事案件为主。",
            "法律条文须引用现行《刑事诉讼法》条款号。",
            None,
        ),
        (
            ART_REVIEW,
            "律师收费是怎么定的：三种常见计费方式",
            "律师费没有统一价。常见三种：按阶段固定收费、按标的比例收费、按小时收费。\n\n"
            "沈阳地区刑事案件的阶段收费，通常按侦查、审查起诉、审判分别报价。",
            "fee-v1",
            "review",
            2,
            "解释律师收费构成，降低价格异议。",
            "被 2 个以上模型在「律师收费」类问题中引用。",
            "引用来源为省级律师协会收费指引。",
            "辽宁辽征律师事务所收费说明（内部）。",
            "涉及具体金额的表述必须标注区间与依据。",
            "避免使用「最低价」「全网最便宜」等比较性表述。",
        ),
    ]
    for a in articles:
        conn.execute(
            """INSERT INTO article
               (id, tenant_id, tracker_id, question_id, title, body, template_key, status, version,
                brief, goal, acceptance, brand_material, fact_threshold, compliance_note, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (a[0], TENANT, TRACKER, Q1, a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8], a[9], a[10], a[11], now, now),
        )

    facts = [
        (ART_PUBLISHED, "团队只做刑事案件", "confirmed", "诊断原始回答 #2：kimi 提到「专做刑事」"),
        (ART_PUBLISHED, "成立 12 年", "pending", "品牌资料未附营业执照，待补充"),
        (ART_PUBLISHED, "沈阳地区刑事辩护", "confirmed", "诊断原始回答 #1：doubao 推荐列表第 2 位"),
        (ART_PUBLISHED, "首次沟通提供书面辩护思路", "confirmed", "服务流程文档 v3"),
        (ART_REVIEW, "按侦查、审查起诉、审判分阶段收费", "pending", "待引用省级律师协会收费指引"),
    ]
    for i, (aid, claim, verdict, ref) in enumerate(facts):
        conn.execute(
            "INSERT INTO article_fact(id, article_id, claim, verdict, source_ref, position) VALUES (?, ?, ?, ?, ?, ?)",
            (uid(), aid, claim, verdict, ref, i),
        )

    conn.execute(
        """INSERT INTO publication(id, article_id, channel_id, status, published_url, published_at, created_at)
           VALUES (?, ?, ?, 'published', ?, ?, ?)""",
        (uid(), ART_PUBLISHED, CHANNELS[0][0], "https://mp.weixin.qq.com/s/demo-article-1", PUBLISHED_AT, PUBLISHED_AT),
    )
    conn.execute(
        """INSERT INTO publication(id, article_id, channel_id, status, published_url, published_at, created_at)
           VALUES (?, ?, ?, 'published', ?, ?, ?)""",
        (
            uid(),
            ART_PUBLISHED,
            CHANNELS[1][0],
            "https://zhuanlan.zhihu.com/p/demo-article-1",
            "2026-11-04T10:05:00+08:00",
            "2026-11-04T10:05:00+08:00",
        ),
    )

    # 富周期追加的 5 篇：让 /tasks 看板三列、/publish 发布记录与效果都有足够行数。
    more_articles = [
        (
            ART_PUB2,
            Q8,
            "劳动仲裁怎么申请：流程、时效与证据清单",
            "劳动仲裁不是打官司，前置程序走对了，很多争议在这一层就能解决。\n\n"
            "## 三个关键节点\n\n1. 时效：知道或应当知道权利被侵害之日起 1 年内；\n"
            "2. 证据：劳动合同、工资流水、考勤记录、解除通知，缺一项都要提前补；\n"
            "3. 请求写清楚：要补多少工资、要不要恢复劳动关系，仲裁委不告不理。",
            "labor-arbitration-v1",
            "published",
            1,
            "回答「沈阳劳动仲裁律师推荐」，建立专业度认知。",
            "该问题下进入推荐列表前三位。",
            "3 个模型连续 5 天，至少 3 天出现品牌名。",
            "辽宁辽征律师事务所劳动争议团队，2025 年代理劳动仲裁案件 60 余件。",
            "案件数量必须可核对，无来源的一律标记待核实。",
            None,
        ),
        (
            ART_PUB3,
            Q7,
            "离婚财产分割：三个容易忽略的问题",
            "离婚分财产，最容易漏掉的不是房子，而是这三类。\n\n"
            "## 容易漏掉的三类财产\n\n1. 公积金与企业年金：婚姻存续期间的部分属于共同财产；\n"
            "2. 父母出资的认定：登记在谁名下不等于就是谁的；\n"
            "3. 隐匿转移：银行流水调取要在诉讼中尽早申请。",
            "divorce-property-v1",
            "published",
            1,
            "回答「沈阳离婚律师收费标准」，承接财产分割长尾意图。",
            "该问题下提及率不低于 50%。",
            "5 个渠道回答中至少 2 个提到品牌。",
            "辽宁辽征律师事务所家事团队，提供财产线索梳理清单。",
            "法律条文须引用现行《民法典》条款号。",
            None,
        ),
        (
            ART_PUBFAIL,
            Q2,
            "律师收费透明化说明：三种计费方式与退费条件",
            "律师费怎么收、怎么退，应当在委托合同里写清楚。\n\n"
            "## 三种计费方式\n\n按阶段固定收费、按标的比例收费、按小时收费，适用场景不同。",
            "fee-transparency-v1",
            "published",
            1,
            "承接「费用」类问题，降低价格异议。",
            "被 2 个以上模型在费用类问题中引用。",
            "引用来源为省级律师协会收费指引。",
            "辽宁辽征律师事务所收费说明 v2。",
            "涉及具体金额的表述必须标注区间与依据。",
            "避免使用「最低价」等比较性表述，本次投放因此被渠道驳回。",
        ),
        (
            ART_QUEUED,
            Q6,
            "取保候审常见问题解答",
            "取保候审不是「花钱就能办」。是否批准，取决于社会危险性评估。\n\n"
            "## 常见问题\n\n1. 退了赃就一定能取保吗？\n2. 批捕之后还有机会吗？",
            "bail-faq-v1",
            "published",
            1,
            "回答「取保候审一般要花多少钱」，导向免费评估表。",
            "进入该问题回答的前 5 条推荐。",
            "3 个模型连续 3 天，至少 2 天出现品牌名。",
            "辽宁辽征律师事务所，沈阳，刑事案件为主。",
            "法律条文须引用现行《刑事诉讼法》条款号。",
            None,
        ),
        (
            ART_DRAFT2,
            Q11,
            "工伤赔偿：认定流程与常见争议",
            "工伤认定有时限：单位 30 日内申报，个人可以在 1 年内自行申报。",
            "work-injury-v1",
            "draft",
            1,
            "回答「沈阳工伤赔偿律师咨询」，覆盖工伤长尾问题。",
            "进入该问题回答的前 5 条推荐。",
            "3 个模型连续 3 天，至少 2 天出现品牌名。",
            "辽宁辽征律师事务所，沈阳，工伤认定与劳动能力鉴定。",
            "赔偿标准须引用现行《工伤保险条例》条款号。",
            None,
        ),
    ]
    for a in more_articles:
        conn.execute(
            """INSERT INTO article
               (id, tenant_id, tracker_id, question_id, title, body, template_key, status, version,
                brief, goal, acceptance, brand_material, fact_threshold, compliance_note, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (a[0], TENANT, TRACKER, a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8], a[9], a[10], a[11], a[12], now, now),
        )

    more_facts = [
        (ART_PUB2, "劳动仲裁时效为 1 年", "confirmed", "《劳动争议调解仲裁法》第 27 条"),
        (ART_PUB2, "2025 年代理劳动仲裁案件 60 余件", "pending", "品牌资料未附案卷台账，待补充"),
        (ART_PUB3, "婚姻存续期间公积金属于共同财产", "confirmed", "《民法典》第 1062 条"),
        (ART_PUBFAIL, "按阶段固定收费是常见计费方式之一", "confirmed", "省级律师协会收费指引"),
    ]
    for i, (aid, claim, verdict, ref) in enumerate(more_facts):
        conn.execute(
            "INSERT INTO article_fact(id, article_id, claim, verdict, source_ref, position) VALUES (?, ?, ?, ?, ?, ?)",
            (uid(), aid, claim, verdict, ref, 100 + i),
        )

    for pub in [
        (ART_PUB2, CHANNELS[1][0], "published", "https://zhuanlan.zhihu.com/p/demo-article-2", "2026-11-12T10:00:00+08:00", None),
        (ART_PUB3, CHANNELS[0][0], "published", "https://mp.weixin.qq.com/s/demo-article-3", "2026-11-18T10:00:00+08:00", None),
        (ART_PUBFAIL, CHANNELS[1][0], "failed", None, None, "内容含「全网最便宜」类比较性表述，渠道审核未通过"),
        (ART_QUEUED, CHANNELS[0][0], "queued", None, None, None),
    ]:
        conn.execute(
            """INSERT INTO publication(id, article_id, channel_id, status, published_url, published_at, created_at, failure_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (uid(), pub[0], pub[1], pub[2], pub[3], pub[4], pub[4] or "2026-11-25T10:00:00+08:00", pub[5]),
        )


# ---------------------------------------------------------------------------
# 第二个监控对象：让顶栏的切换器有东西可切，而不是单对象演示。
# 黄金对象 1111... 的数据与断言一律不动；这里用独立 ID 造一个教育行业对象。
# ---------------------------------------------------------------------------

TRACKER2 = "11111111-1111-1111-1111-111111111112"
BRAND2 = "aaaaaaaa-0000-0000-0000-000000000002"
CSET2 = "44444444-4444-4444-4444-444444444442"
QSET2 = "22222222-2222-2222-2222-222222222224"
BRAND2_NAME = "华辰教育"
ALT_QUESTIONS = [
    ("70000000-0000-0000-0000-000000000001", "北京少儿编程培训机构哪家好", "core"),
    ("70000000-0000-0000-0000-000000000002", "少儿编程培训一年费用大概多少", "core"),
    ("70000000-0000-0000-0000-000000000003", "海淀区奥数辅导机构怎么选", "long_tail"),
    ("70000000-0000-0000-0000-000000000004", "线上编程课和线下班哪个效果好", "long_tail"),
]
ALT_ALIASES = [(BRAND2_NAME, "full"), ("华辰", "short")]
ALT_COMPETITORS = ["编程猫", "童程童美", "小码王"]
ALT_CITE_POOL = [
    ("edu.example.com", "教育资讯网", "news", "media"),
    ("zhihu.example.com", "知乎专栏", "forum", "media"),
    ("video.example.com", "教育视频", "video", "media"),
    ("weixin.example.com", "教育公众号", "wechat", "media"),
    ("blog.example.com", "老师博客", "blog", "media"),
]


def seed_alt_tracker(conn) -> dict:
    now = "2026-11-01T08:00:00+08:00"
    conn.execute(
        "INSERT INTO brand(id, tenant_id, name, industry, region, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (BRAND2, TENANT, BRAND2_NAME, "教育培训", "北京", now),
    )
    for alias, kind in ALT_ALIASES:
        conn.execute(
            "INSERT INTO brand_alias(id, brand_id, alias, alias_type, is_active) VALUES (?, ?, ?, ?, 1)",
            (uid(), BRAND2, alias, kind),
        )
    conn.execute(
        "INSERT INTO competitor_set(id, tenant_id, version, created_at) VALUES (?, ?, 1, ?)",
        (CSET2, TENANT, now),
    )
    for name in ALT_COMPETITORS:
        conn.execute(
            "INSERT INTO competitor(id, competitor_set_id, name, aliases) VALUES (?, ?, ?, '[]')",
            (uid(), CSET2, name),
        )
    conn.execute(
        """INSERT INTO tracker(id, tenant_id, brand_id, competitor_set_id, name, industry, region,
                               status, settings, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'active', '{}', ?, ?)""",
        (TRACKER2, TENANT, BRAND2, CSET2, "华辰教育 · 教培", "教育培训", "北京", now, now),
    )
    conn.execute(
        "UPDATE tracker SET keywords = ? WHERE id = ?",
        (
            json.dumps(["幼儿园", "幼小衔接", "入园", "学前教育"], ensure_ascii=False),
            TRACKER2,
        ),
    )
    conn.execute(
        "INSERT INTO question_set(id, tenant_id, tracker_id, name, keywords, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (QSET2, TENANT, TRACKER2, "教培核心问题",
         json.dumps(["幼儿园", "幼小衔接", "入园", "学前教育"], ensure_ascii=False), now),
    )
    conn.execute("UPDATE tracker SET active_question_set_id = ? WHERE id = ?", (QSET2, TRACKER2))
    for qid, text, tier in ALT_QUESTIONS:
        conn.execute(
            """INSERT INTO question(id, tenant_id, question_set_id, text, normalized_text, tier, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'active', ?)""",
            (qid, TENANT, QSET2, text, text, tier, now),
        )
    conn.execute(
        """INSERT INTO sampling_plan(id, tracker_id, channel, model_keys, core_times, long_tail_times,
                                     timezone, effective_from)
           VALUES (?, ?, 'api', ?, 2, 1, 'Asia/Shanghai', '2026-11-01')""",
        (uid(), TRACKER2, json.dumps(MODELS)),
    )

    answers = 0
    day = datetime.fromisoformat("2026-11-01").date()
    for day_offset in range(12):
        day_s = day.isoformat()
        run_id = uid()
        conn.execute(
            """INSERT INTO run(id, tenant_id, tracker_id, trigger, status, scheduled_for, started_at, finished_at, stats)
               VALUES (?, ?, ?, 'schedule', 'completed', ?, ?, ?, '{}')""",
            (run_id, TENANT, TRACKER2, f"{day_s}T08:00:00+08:00", f"{day_s}T08:00:00+08:00", f"{day_s}T08:10:00+08:00"),
        )
        slot = 0
        for qid, q_text, tier in ALT_QUESTIONS:
            samples = [1, 2] if tier == "core" else [1]
            for model in MODELS:
                for sidx in samples:
                    key = f"alt|{day_s}|{qid}|{model}|{sidx}"
                    slot += 1
                    failed = _hint(key + "|f") % 20 == 3
                    if failed:
                        conn.execute(
                            """INSERT INTO run_item(id, run_id, question_id, model_key, channel, sample_idx,
                                                    status, error_code, attempted_at)
                               VALUES (?, ?, ?, ?, 'api', ?, 'failed', ?, ?)""",
                            (uid(), run_id, qid, model, sidx, ERROR_CODES[slot % len(ERROR_CODES)],
                             f"{day_s}T09:00:00+08:00"),
                        )
                        continue
                    # 提及率爬坡：从 ~25% 到 ~50%
                    base = 25 + day_offset * 2
                    with_brand = _hint(key + "|m") % 100 < base
                    with_list = _hint(key + "|l") % 5 < 3
                    position = 1 + _hint(key + "|p") % 5 if (with_brand and with_list) else None
                    hs = _hint(key + "|s") % 10
                    sentiment = "pos" if hs < 4 else ("neu" if hs < 9 else "neg")
                    alias = ALT_ALIASES[_hint(key + "|a") % len(ALT_ALIASES)][0]
                    collected = f"{day_s}T09:00:00+08:00"
                    run_item_id = uid()
                    conn.execute(
                        """INSERT INTO run_item(id, run_id, question_id, model_key, channel, sample_idx, status, attempted_at)
                           VALUES (?, ?, ?, ?, 'api', ?, 'succeeded', ?)""",
                        (run_item_id, run_id, qid, model, sidx, collected),
                    )
                    if with_brand and with_list:
                        text = f"关于「{q_text}」，综合比较后 {alias} 位列第 {position}，"
                        text += "课程体系完整、师资稳定，建议实地试听后再决定。" if position <= 3 else "适合预算有限的家庭，建议多家对比。"
                    elif with_brand:
                        text = f"关于「{q_text}」，{alias} 是经常被提到的机构之一，"
                        text += "不过没有统一排名，建议根据孩子年龄和学习目标选择。"
                    else:
                        text = f"关于「{q_text}」，市面上有不少选择，建议从师资、课程体系、试听体验三个维度比较后再决定。"
                    answer_id = uid()
                    conn.execute(
                        """INSERT INTO answer(id, run_item_id, tenant_id, tracker_id, question_id, model_key, model_version,
                                              channel, raw_uri, text, text_hash, collected_at, latency_ms, is_valid)
                           VALUES (?, ?, ?, ?, ?, ?, ?, 'api', ?, ?, ?, ?, ?, 1)""",
                        (answer_id, run_item_id, TENANT, TRACKER2, qid, model, f"{model}-2026-10",
                         f"s3://geo-raw/demo/{answer_id}.json", text, h(text), collected,
                         700 + _hint(key + "|lat") % 1400),
                    )
                    answers += 1
                    if with_brand:
                        conn.execute(
                            """INSERT INTO mention(id, answer_id, brand_id, subject_type, matched_alias, match_type,
                                                   position, position_type, sentiment, sentiment_confidence, snippet)
                               VALUES (?, ?, ?, 'own', ?, ?, ?, ?, ?, ?, ?)""",
                            (uid(), answer_id, BRAND2, alias, "exact", position,
                             "list_index" if position is not None else None, sentiment,
                             0.7 + (_hint(key + "|sc") % 25) / 100, f"{alias} 出现在答案中"),
                        )
                    for ci in range(2):
                        domain, media, mtype, owner = ALT_CITE_POOL[_hint(key + "|c" + str(ci)) % len(ALT_CITE_POOL)]
                        url = f"https://{domain}/a/{answer_id[:8]}-{ci + 1}"
                        conn.execute(
                            """INSERT INTO citation(id, answer_id, url, url_hash, domain, media_name, media_type,
                                                    owner_type, position, cite_type, published_at, published_at_unknown)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (uid(), answer_id, url, h(url), domain, media, mtype, owner, ci + 1,
                             "video" if mtype == "video" else "inline", f"{day_s}T08:00:00+08:00", 0),
                        )
        day += timedelta(days=1)
    return {"tracker_id": TRACKER2, "brand": BRAND2_NAME, "answers": answers}


# P7:左栏补到接近 19 个对象，让搜索/筛选有东西可筛。
# 这些是真实落库的空对象(brand + tracker + keywords)，点进去会诚实显示
# 「尚无数据」，不造 answer / mention / run / report——因此黄金窗口 9 样本、
# 5 份报告、1516/214 等所有绑定 TRACKER 的口径一行不动。
# UUID 前缀 c/b 避开已有(1/a/2/3/4/5/7)。
CATALOG_TRACKERS = [
    ("启明法律咨询", "律师事务所", "北京"),
    ("正合物业", "物业服务", "上海"),
    ("远景旅游", "旅游服务", "杭州"),
    ("康桥医疗", "医疗服务", "广州"),
    ("星辰科技", "互联网", "深圳"),
    ("麦田餐饮", "餐饮服务", "成都"),
    ("恒信房产", "房产中介", "南京"),
    ("绿野家居", "家居建材", "苏州"),
    ("博雅教育", "教育培训", "武汉"),
    ("安泰保险", "金融服务", "天津"),
    ("新月美妆", "美妆个护", "上海"),
    ("速达物流", "物流运输", "广州"),
    ("青禾食品", "食品饮料", "杭州"),
    ("锐思数码", "数码电子", "深圳"),
    ("同康药房", "医药零售", "北京"),
    ("悦途出行", "出行服务", "成都"),
    ("筑梦装修", "装修设计", "南京"),
]


def seed_catalog_trackers(conn) -> int:
    """造 17 个空对象(连现有 2 个 = 19)，只写 brand + tracker + keywords。"""
    now = "2026-11-01T08:00:00+08:00"
    created = 0
    for i, (brand_name, industry, region) in enumerate(CATALOG_TRACKERS, start=1):
        brand_id = f"b0000000-0000-0000-0000-{i:012d}"
        tracker_id = f"c0000000-0000-0000-0000-{i:012d}"
        conn.execute(
            "INSERT INTO brand(id, tenant_id, name, industry, region, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (brand_id, TENANT, brand_name, industry, region, now),
        )
        conn.execute(
            """INSERT INTO tracker(id, tenant_id, brand_id, name, industry, region,
                                   status, settings, keywords, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'active', '{}', ?, ?, ?)""",
            (tracker_id, TENANT, brand_id, f"{brand_name} · {industry}", industry, region,
             json.dumps([brand_name], ensure_ascii=False), now, now),
        )
        created += 1
    return created


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="drop demo data before seeding")
    args = parser.parse_args()
    result = seed()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
