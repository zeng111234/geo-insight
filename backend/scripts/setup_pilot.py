"""建一个可采样的试点监控对象（目标问题 + 对照问题 + 采样计划）。

为什么需要这个脚本：建 tracker 的接口不会建采样计划，`/settings` 那块也是只读的。
结果就是"对象建好了、问题导好了，一采就报 '还没有采样计划，先去 /settings 配一个'"。
试点要可复现，建对象的步骤就必须是一条能审查、能重跑的命令，而不是手工改库。

用法：

    python scripts/setup_pilot.py --name "辽征试点" --brand "辽宁辽征" \
        --target "沈阳离婚律师收费标准是多少" --control "沈阳合同纠纷律师怎么找"

    # 先看看会建什么，不写库
    python scripts/setup_pilot.py ... --dry-run

    # 从头再来：先备份再 --reset，不用这个脚本删对象

做对的四件事（缺一件，试点就采不出可用的基线）：
  1. 用独立 tracker，不往演示库灌真实答案（否则种子数据和真实数据混在一起）；
  2. 导入问题后把 active_question_set_id 指过去——不设这个指针，所有指标查询按 NULL
     过滤，看起来就是"没数据"；
  3. 写 sampling_plan（core 3 次 / 长尾 1 次是 D1 的判据，不是随口定的）；
  4. 写完立刻用 compute_gaps 自校验，缺口算不出来就整笔回滚，不留半个对象。

退出码：0 建好；1 参数/状态错误（已存在同名、缺参数等）。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from geo import db
from geo.domain.relevance import parse_keywords, validate_keywords
from geo.repositories import config_repository as config_repo
from geo.repositories import sqlite_repository as repo
from geo.services import config_service, sampling_service, tracker_service
from geo.settings import settings

DEFAULT_MODEL = "doubao"
DEFAULT_CORE_TIMES = 3
DEFAULT_LONG_TAIL_TIMES = 1


class SetupError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _now() -> str:
    from datetime import datetime, timedelta, timezone

    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="建一个可采样的试点监控对象")
    p.add_argument("--name", required=True, help="试点对象名称，例如「辽征试点」")
    p.add_argument("--brand", required=True, help="本方品牌名（提及识别的主别名）")
    p.add_argument("--target", required=True, help="目标问题原文（投放的问题）")
    p.add_argument("--control", required=True, help="对照问题原文（同期不投放）")
    p.add_argument("--aliases", default="", help="品牌其他别名，逗号分隔")
    p.add_argument("--competitors", default="", help="竞品名，逗号分隔")
    p.add_argument("--keywords", default="", help="核心词，逗号分隔（写入生效问题集）")
    p.add_argument("--model", default=DEFAULT_MODEL, help=f"主平台 model_key，默认 {DEFAULT_MODEL}")
    p.add_argument("--core-times", type=int, default=DEFAULT_CORE_TIMES, help="核心问题每天采样次数")
    p.add_argument("--long-tail-times", type=int, default=DEFAULT_LONG_TAIL_TIMES, help="长尾问题每天次数")
    p.add_argument("--dry-run", action="store_true", help="只打印计划，不写库")
    return p


def _split(raw: str) -> list[str]:
    return [item.strip() for item in raw.replace("，", ",").split(",") if item.strip()]


def validate(args: argparse.Namespace) -> None:
    for flag in ("name", "brand", "target", "control"):
        if not getattr(args, flag).strip():
            raise SetupError("MISSING_ARG", f"--{flag} 不能为空")
    if args.target.strip() == args.control.strip():
        raise SetupError("SAME_QUESTION", "目标问题和对照问题不能是同一句")
    if args.core_times < 1 or args.long_tail_times < 0:
        raise SetupError("BAD_TIMES", "核心次数 ≥ 1，长尾次数 ≥ 0")
    words = parse_keywords(args.keywords)
    error = validate_keywords(words)
    if error:
        raise SetupError("BAD_KEYWORDS", error)


def setup(conn, args: argparse.Namespace) -> dict:
    tenant = settings.default_tenant_id
    now = _now()

    # 防重复试点：同名对象已经存在就停手，别悄悄建第二个（基线会分叉）
    exists = conn.execute(
        "SELECT id FROM tracker WHERE tenant_id = ? AND name = ?", (tenant, args.name.strip())
    ).fetchone()
    if exists:
        raise SetupError("ALREADY_EXISTS", f"已存在同名监控对象 {exists['id']}，换个名字或先处理旧对象")

    made = config_service.create(
        conn,
        tenant,
        {
            "name": args.name.strip(),
            "brand_name": args.brand.strip(),
            "brand_aliases": _split(args.aliases),
            "competitor_names": _split(args.competitors),
        },
        now,
    )
    tracker_id = made["tracker_id"]

    imported = config_repo.import_questions(
        conn,
        tracker_id,
        tenant,
        [
            {"text": args.target.strip(), "tier": "core"},
            {"text": args.control.strip(), "tier": "core"},
        ],
        now,
    )
    set_id = imported["question_set_id"]

    # 关键一步：create_tracker 建了空问题集但没设活动指针。
    # 不设它，所有指标 SQL 都按 active_question_set_id = NULL 过滤，问题等于不存在。
    tracker_service.set_active_question_set(conn, tracker_id, tenant, set_id)

    words = parse_keywords(args.keywords)
    if words:
        tracker_service.update_question_set(conn, set_id, tenant, None, words)

    # 采样计划没有写入接口，这里直接写。channel='api' 对应联网采集通道。
    conn.execute(
        """INSERT INTO sampling_plan(id, tracker_id, channel, model_keys,
                                     core_times, long_tail_times, timezone, effective_from)
           VALUES (?, ?, 'api', ?, ?, ?, 'Asia/Shanghai', ?)""",
        (
            config_repo.uid(),
            tracker_id,
            json.dumps([args.model], ensure_ascii=False),
            args.core_times,
            args.long_tail_times,
            date.today().isoformat(),
        ),
    )
    conn.commit()

    # 自校验：缺口算不出来就说明上面哪一步没接上，整笔回滚。
    today = date.today().isoformat()
    try:
        gaps = sampling_service.compute_gaps(conn, tracker_id, today, today, model=args.model)
    except ValueError as exc:
        conn.rollback()
        raise SetupError("SELF_CHECK_FAILED", f"建完算不出缺口，已回滚：{exc}") from exc

    return {
        "tracker_id": tracker_id,
        "question_set_id": set_id,
        "questions": imported["created"],
        "model_key": args.model,
        "today_missing": gaps["totals"]["missing"],
        "daily_planned": gaps["totals"]["expected"],
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        validate(args)
    except SetupError as exc:
        print(f"[x] {exc.message}", file=sys.stderr)
        return 1

    print("将创建：")
    print(f"  对象       {args.name}（品牌：{args.brand}）")
    print(f"  目标问题   {args.target}")
    print(f"  对照问题   {args.control}")
    print(f"  采样计划   {args.model} · 核心 {args.core_times} 次/天 · 长尾 {args.long_tail_times} 次/天")
    if args.dry_run:
        print("\n--dry-run：未写库")
        return 0

    conn = db.connect()
    try:
        try:
            result = setup(conn, args)
        except SetupError as exc:
            conn.rollback()
            print(f"[x] {exc.message}", file=sys.stderr)
            return 1
    finally:
        conn.close()

    print(f"\n已建好：tracker={result['tracker_id']}")
    print(f"  问题集   {result['question_set_id']}（已设为生效集）")
    for q in result["questions"]:
        print(f"  {q['tier']:<9} {q['text']}")
    print(f"  今日计划 {result['daily_planned']} 条，当前缺口 {result['today_missing']} 条")
    print("\n下一步：填好 key，然后 python scripts/collect.py --tracker " + result["tracker_id"] + " --loop")
    return 0


if __name__ == "__main__":
    if not Path(settings.database_path).is_file():
        print("[x] 数据库不存在，先启动一次服务（python run.py --no-browser）让 seed 建库", file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(main())
