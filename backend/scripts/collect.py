"""采一条（或一批）答案并落库。

用法：

    python scripts/collect.py --list
    python scripts/collect.py --question "沈阳哪一家律所最专业" --model doubao
    python scripts/collect.py --limit 5 --model kimi

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
        print(f"  {collector.key:<10} {collector.kind:<8} {state:<6} {collector.display_name}")
    print("\n离线答案库路径由 GEO_COLLECTOR_CSV 指定。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="采一条答案并落库")
    parser.add_argument("--list", action="store_true", help="列出采集器")
    parser.add_argument("--tracker", default=None, help="监控对象 id，默认取第一个")
    parser.add_argument("--question", default=None, help="问题原文")
    parser.add_argument("--model", default=None, help="模型 key，例如 doubao")
    parser.add_argument("--collector", default=None, help="采集器 key，默认取第一个可用的")
    parser.add_argument("--limit", type=int, default=1, help="不给 --question 时，按顺序采前 N 个活跃问题")
    args = parser.parse_args(argv)

    if args.list:
        return show_collectors()
    if not args.model:
        print("[x] 需要 --model（或用 --list 看看有哪些采集器）", file=sys.stderr)
        return 1

    db.init_db()
    conn = db.connect()
    try:
        tracker_id = args.tracker
        if not tracker_id:
            tracker = import_repo.default_tracker(conn, settings.default_tenant_id)
            if not tracker:
                print("[x] 没有可用的监控对象", file=sys.stderr)
                return 1
            tracker_id = tracker["id"]
            print(f"监控对象：{tracker['name']}（{tracker_id}）")

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
                )
            except CollectorError as exc:
                failed += 1
                print(f"  ✗ {text[:28]}  [{exc.code}] {exc}")
                continue
            if result["inserted"]:
                inserted += 1
                print(f"  ✓ {text[:28]}  {result['collector']} → {result['text_length']} 字")
            else:
                duplicated += 1
                print(f"  = {text[:28]}  {result['message']}")

        print(f"\n写入 {inserted}，重复跳过 {duplicated}，失败 {failed}")
        return 2 if failed else 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
