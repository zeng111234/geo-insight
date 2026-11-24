"""按采样计划算「哪天真缺样本」，可导出人工采集清单。

用法：

    python scripts/sample_gaps.py                          # 今天的缺口
    python scripts/sample_gaps.py --date 2026-11-20
    python scripts/sample_gaps.py --from 2026-11-18 --to 2026-11-24
    python scripts/sample_gaps.py --model doubao --export gaps.csv

导出的 CSV 用中文表头，人工填完「答案」列就能直接喂给 import_answers.py，
不需要另做一套模板。采集时间已预填当天 09:00，按实际改。

核销是实时差分：导入后重新跑一次，缺口自然缩小或归零，没有"待办表"要维护。

退出码：0 正常；1 出错；加 --fail-on-gap 时，存在缺口返回 2（便于定时任务报警）。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))
os.chdir(BACKEND)

from geo import db  # noqa: E402
from geo.repositories import import_repository as import_repo  # noqa: E402
from geo.services import sampling_service  # noqa: E402
from geo.settings import settings  # noqa: E402

TZ = timezone(timedelta(hours=8))
TIER_LABEL = {"core": "核心", "long_tail": "长尾"}
EXPORT_HEADER = ["计划日期", "问题", "模型", "层级", "第几次", "采集时间", "答案", "提及", "引用"]


def _width(text: str) -> int:
    """终端显示宽度：中日韩全角字符按 2 列算，否则表格会错位。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _width(text))


def _truncate(text: str, width: int) -> str:
    """按显示宽度截断，留一列给省略号。按字符数截断会让中文列挤歪。"""
    if _width(text) <= width:
        return text
    kept: list[str] = []
    used = 0
    for ch in text:
        step = 2 if unicodedata.east_asian_width(ch) in "WF" else 1
        if used + step > width - 1:
            break
        kept.append(ch)
        used += step
    return "".join(kept) + "…"


def _print_table(gaps: dict, show_all: bool, limit: int) -> None:
    rows = gaps["rows"] if show_all else [r for r in gaps["rows"] if r["missing"]]
    period = gaps["period"]
    span = period["from"] if period["from"] == period["to"] else f"{period['from']} ~ {period['to']}"
    plan = gaps["plan"]
    print(
        f"采样缺口（{span}，{gaps['question_count']} 个问题 × {len(gaps['models'])} 个模型）"
    )
    print(f"计划：核心 {plan['core_times']} 次/天，长尾 {plan['long_tail_times']} 次/天\n")

    if not rows:
        print("没有缺口，这几天都采齐了。")
        return

    print(f"{_pad('日期', 12)}{_pad('问题', 26)}{_pad('模型', 10)}{_pad('层级', 6)}应有 已有 缺")
    for row in rows[:limit]:
        print(
            f"{_pad(row['date'], 12)}{_pad(_truncate(row['question_text'], 26), 26)}"
            f"{_pad(row['model'], 10)}{_pad(TIER_LABEL.get(row['tier'], row['tier']), 6)}"
            f"{row['expected']:>4} {row['actual']:>4} {row['missing']:>2}"
        )
    if len(rows) > limit:
        print(f"……还有 {len(rows) - limit} 行未显示（用 --export 导出，或缩小日期范围）")

    totals = gaps["totals"]
    print(
        f"\n合计：应有 {totals['expected']}，已有 {totals['actual']}，缺口 {totals['missing']}"
    )


def _export(gaps: dict, path: Path) -> int:
    tickets = sampling_service.gap_tickets(gaps)
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig：Excel 双击打开不乱码
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(EXPORT_HEADER)
        for ticket in tickets:
            writer.writerow(
                [
                    ticket["date"],
                    ticket["question"],
                    ticket["model"],
                    TIER_LABEL.get(ticket["tier"], ticket["tier"]),
                    ticket["slot"],
                    ticket["collected_at"],
                    ticket["answer"],
                    ticket["mentions"],
                    ticket["citations"],
                ]
            )
    return len(tickets)


def main(argv: list[str] | None = None) -> int:
    today = datetime.now(TZ).date().isoformat()
    parser = argparse.ArgumentParser(description="按采样计划算样本缺口")
    parser.add_argument("--tracker", default=None, help="监控对象 id，默认取第一个")
    parser.add_argument("--date", default=None, help="只看某一天（默认今天）")
    parser.add_argument("--from", dest="start", default=None, help="区间起始，需与 --to 一起用")
    parser.add_argument("--to", dest="end", default=None, help="区间结束")
    parser.add_argument("--model", default=None, help="只看某个模型")
    parser.add_argument("--export", default=None, help="导出待采集 CSV 的路径")
    parser.add_argument("--all", action="store_true", help="连不缺的行也显示")
    parser.add_argument("--limit", type=int, default=40, help="表格最多显示多少行")
    parser.add_argument("--fail-on-gap", action="store_true", help="存在缺口时返回 2")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    if args.date and (args.start or args.end):
        print("[x] --date 与 --from/--to 只能选一个", file=sys.stderr)
        return 1
    if bool(args.start) != bool(args.end):
        print("[x] --from 与 --to 要成对出现", file=sys.stderr)
        return 1
    start = args.date or args.start or today
    end = args.date or args.end or today

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
        gaps = sampling_service.compute_gaps(
            conn, tracker_id, start, end, model=args.model
        )
    except ValueError as exc:
        print(f"[x] {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    if args.export:
        written = _export(gaps, Path(args.export))
        print(f"已导出 {written} 行待采集清单：{args.export}\n")

    if args.json:
        print(json.dumps(gaps, ensure_ascii=False, indent=2))
    else:
        _print_table(gaps, args.all, args.limit)

    if args.fail_on_gap and gaps["totals"]["missing"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
