"""采一条（或一批）答案并落库。

用法：

    python scripts/collect.py --list
    python scripts/collect.py --question "沈阳哪一家律所最专业" --model doubao
    python scripts/collect.py --limit 5 --model kimi
    # 批量：对所有需要采集的对象各跑一轮（systemd 每日任务用这个，不硬编码对象 ID）
    python scripts/collect.py --all --model deepseek --collector deepseek --loop --rounds 1
    # 排除演示对象（避免污染已验收口径）：
    #   GEO_COLLECT_EXCLUDE=<id1>,<id2> python scripts/collect.py --all --model deepseek

没有平台 API 权限时，先设好离线答案库再跑，用来演练整条链路：

    set GEO_COLLECTOR_CSV=data/import-sample.jsonl

退出码：0 全部写入（含"重复未写"）；2 部分失败；1 无法开始。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))
os.chdir(BACKEND)

from geo import db  # noqa: E402
from geo.collectors import registry  # noqa: E402
from geo.collectors.base import CollectorError  # noqa: E402
from geo.repositories import import_repository as import_repo  # noqa: E402
from geo.repositories import sqlite_repository as repo  # noqa: E402
from geo.services import collect_service  # noqa: E402
from geo.settings import settings  # noqa: E402


def show_collectors() -> int:
    items = registry.all_collectors()
    if not items:
        print("没有注册任何采集器")
        return 1
    for collector in items:
        state = "可用" if collector.available() else "未配置"
        models = "、".join(getattr(collector, "MODEL_KEYS", ())) or "—"
        print(f"  {collector.key:<10} {collector.kind:<8} {state:<6} 模型:{models:<10} {collector.display_name}")
    print("\n真实采集的 key：GEO_DEEPSEEK_API_KEY / GEO_ARK_API_KEY（或 backend/data/keys.local.json）。")
    print("离线答案库路径由 GEO_COLLECTOR_CSV 指定。")
    return 0


def run_all(conn, args) -> int:
    """对所有需要采集的对象各跑一轮（systemd 每日任务的入口）。

    设计要点：
    - 不硬编码对象 ID：新加的对象自动纳入，不用改脚本或定时任务；
    - 用 GEO_COLLECT_EXCLUDE 排除演示基线等不该采的对象（systemd 模板里写明原因）；
    - 每个对象各自按自己的采样计划补当天缺口，重复跑不重复采（fill_gaps 的缺口语义）；
    - 一个对象失败不影响其它对象，汇总后按"有无失败"决定退出码。
    """
    excluded = {x.strip() for x in (os.getenv("GEO_COLLECT_EXCLUDE") or "").split(",") if x.strip()}
    trackers = [
        t for t in repo.list_trackers(conn, settings.default_tenant_id)
        if t["id"] not in excluded
    ]
    if not trackers:
        print("[x] 没有可采集的对象（都被 GEO_COLLECT_EXCLUDE 排除了？）", file=sys.stderr)
        return 1

    total_inserted = total_failed = 0
    skipped = []
    for t in trackers:
        tid = t["id"]
        try:
            stats = collect_service.fill_gaps(
                conn, tracker_id=tid, model_key=args.model,
                force=args.force, collector_key=args.collector,
            )
        except ValueError as exc:
            # 没采样计划 / 没启用问题——跳过这个对象，不让它拖累其它对象。
            skipped.append((t["name"], str(exc)))
            continue
        total_inserted += stats["inserted"]
        total_failed += stats["failed"]
        print(
            f"  {t['name']:<24} 缺口 {stats['planned_missing']} → 补采 {stats['inserted']}，"
            f"失败 {stats['failed']}，今日还差 {stats['remaining_missing']}"
        )
        for err in stats["errors"][:3]:
            print(f"      ! [{err['code']}] {err['question'][:20]}  {err['message'][:70]}")
    for name, reason in skipped:
        print(f"  - {name:<24} 跳过：{reason}")
    if excluded:
        print(f"  （按 GEO_COLLECT_EXCLUDE 排除 {len(excluded)} 个对象）")
    print(f"\n合计：补采 {total_inserted}，失败 {total_failed}，跳过 {len(skipped)}")
    return 2 if total_failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="采一条答案并落库")
    parser.add_argument("--list", action="store_true", help="列出采集器")
    parser.add_argument("--tracker", default=None, help="监控对象 id，默认取第一个")
    parser.add_argument("--all", action="store_true",
                        help="对所有对象各跑一轮（配合 --loop --rounds 1）。"
                             "用 GEO_COLLECT_EXCLUDE=id1,id2 排除不想采的对象（如演示基线）。"
                             "新加的对象自动纳入，无需改脚本或定时任务")
    parser.add_argument("--question", default=None, help="问题原文")
    parser.add_argument("--model", default=None, help="模型 key，例如 doubao")
    parser.add_argument("--collector", default=None, help="采集器 key，默认取第一个可用的")
    parser.add_argument("--limit", type=int, default=1, help="不给 --question 时，按顺序采前 N 个活跃问题")
    # --once 是 --limit 1 的别名。试点计划里 D2/D3 的判据都写的是「跑一次」，
    # 有这个参数就不用记 --limit 的默认值。
    parser.add_argument("--once", action="store_true", help="只采一条（等同 --limit 1）")
    parser.add_argument(
        "--force", action="store_true",
        help="越过当天的计划量补采（人工补数据用；会超出计划，指标里多算样本）",
    )
    parser.add_argument(
        "--loop", action="store_true",
        help="按缺口循环采集：跑一轮 → 睡 interval 秒 → 再跑。Ctrl+C 停",
    )
    parser.add_argument(
        "--interval", type=int, default=3600, help="--loop 的间隔秒数，默认 3600",
    )
    parser.add_argument(
        "--rounds", type=int, default=0,
        help="--loop 最多跑几轮后正常退出；0 表示一直跑（默认）。"
             "自动化/定时任务请显式给 1，别靠 Ctrl+C",
    )
    args = parser.parse_args(argv)
    if args.once:
        args.limit = 1
    if args.rounds < 0:
        print("[x] --rounds 不能是负数（0 表示无限循环）", file=sys.stderr)
        return 1

    if args.list:
        return show_collectors()
    if not args.model:
        print("[x] 需要 --model（或用 --list 看看有哪些采集器）", file=sys.stderr)
        return 1

    db.init_db()
    conn = db.connect()
    try:
        if args.all:
            if args.tracker or args.question:
                print("[x] --all 不能与 --tracker/--question 同用", file=sys.stderr)
                return 1
            return run_all(conn, args)
        tracker_id = args.tracker
        if not tracker_id:
            tracker = import_repo.default_tracker(conn, settings.default_tenant_id)
            if not tracker:
                print("[x] 没有可用的监控对象", file=sys.stderr)
                return 1
            tracker_id = tracker["id"]
            print(f"监控对象：{tracker['name']}（{tracker_id}）")

        if args.loop:
            return run_loop(conn, tracker_id, args)

        questions = [args.question] if args.question else repo.active_question_texts(conn, tracker_id, args.limit)
        if not questions:
            print("[x] 没有可采集的问题", file=sys.stderr)
            return 1

        inserted = duplicated = failed = 0
        for text in questions:
            try:
                result = collect_service.run_once(
                    conn,
                    tracker_id=tracker_id,
                    question_text=text,
                    model_key=args.model,
                    collector_key=args.collector,
                    allow_over=args.force,
                )
            except CollectorError as exc:
                failed += 1
                print(f"  ✗ {text[:28]}  [{exc.code}] {exc}")
                continue
            if result["inserted"]:
                inserted += 1
                # 一行里必须能看出「搜没搜、引了几条」：D1 判「目标问题至少 2/3 次
                # 触发搜索」全靠这两个数，藏在库里就得每次手工查 SQL。
                print(
                    f"  ✓ {text[:28]}  {result['collector']} → {result['text_length']} 字"
                    f"  [{result['sample_day']} 第 {result['sample_idx']} 条]"
                    f"  | {result['message']}"
                    f"  | raw={result.get('raw_uri') or '（未落盘）'}"
                )
            else:
                duplicated += 1
                print(f"  = {text[:28]}  {result['message']}")

        print(f"\n写入 {inserted}，重复跳过 {duplicated}，失败 {failed}")
        return 2 if failed else 0
    finally:
        conn.close()


def run_loop(conn, tracker_id: str, args) -> int:
    """按缺口循环采集。Ctrl+C 正常退出（返回 0，不是异常）。

    每轮都把「今天还差几条」打印出来再决定采不采：这一行是调度器唯一
    可观测的地方，出问题时能立刻分清是"没缺口所以没采"还是"采了但失败"。
    """
    import time
    from datetime import datetime

    rounds = 0
    try:
        while args.rounds == 0 or rounds < args.rounds:
            rounds += 1
            try:
                stats = collect_service.fill_gaps(
                    conn,
                    tracker_id=tracker_id,
                    model_key=args.model,
                    force=args.force,
                    collector_key=args.collector,
                )
            except ValueError as exc:
                # 没采样计划、没启用中的问题：这是配置问题，循环下去没意义
                print(f"[x] {exc}", file=sys.stderr)
                return 1

            stamp = datetime.now().strftime("%H:%M:%S")
            print(
                f"[{stamp}] 第 {rounds} 轮 · {stats['date']} · {args.model}："
                f"计划缺口 {stats['planned_missing']} → 补采 {stats['inserted']}，"
                f"失败 {stats['failed']}，今日还差 {stats['remaining_missing']}"
            )
            for err in stats["errors"][:5]:
                print(f"    ! [{err['code']}] {err['question'][:20]}  {err['message'][:90]}")

            if stats["aborted"]:
                # 提前收手时要说清原因，否则会被当成"采完了"
                print("    本轮提前结束（认证失败 / 采满 / 连续限流）。间隔后重试")

            if args.rounds and rounds >= args.rounds:
                # 跑满指定轮数就收工，不再睡——定时任务靠退出码判断成败，
                # 白睡一个 interval（默认 3600s）会被当成卡死。
                break
            if stats["remaining_missing"] == 0:
                print(f"    今天已经采满，{args.interval} 秒后再检查")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print(f"\n已停止（共 {rounds} 轮）。")
        return 0
    # 跑满指定轮数后要正常退出，不能停在"等下一轮"的姿态：
    # 定时任务靠退出码判断成败，而 --rounds 1 的整条命令已经在上面打完账了。
    print(f"\n已完成 {rounds} 轮（--rounds {args.rounds}），正常退出。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
