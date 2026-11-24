"""把外部 CSV/JSONL 导入答案库。

用法（在 backend 目录或项目根目录都行）：

    python scripts/import_answers.py --file data/answers.csv --dry-run
    python scripts/import_answers.py --file data/answers.csv
    python scripts/import_answers.py --file data/answers.jsonl --create-questions
    python scripts/import_answers.py --file bad.csv --on-error abort

CSV 至少需要这些列（表头支持中英文，见 import_service.COLUMN_ALIASES）：

    question,answer,model,collected_at[,channel][,mentions][,citations]

其中 mentions 与 citations 用 JSON 数组字符串，例如：

    "[{""brand"":""某竞品"",""sentiment"":""neg"",""position"":3}]"

退出码：0 全部成功；2 有失败行但其余已入库；1 整批无法开始。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))
# 与 run.py 一致：库文件以 backend/ 为基准，避免在项目根目录又生成一个 geo.db
os.chdir(BACKEND)

from geo import db  # noqa: E402
from geo.services import import_service  # noqa: E402


def _print_human(report) -> None:
    tag = "【演练】" if report.dry_run else ""
    print(f"{tag}文件：{report.source_name}（{report.source_format}，{report.total} 行）")
    if report.batch_id:
        print(f"批次：{report.batch_id}")
    print(
        f"{tag}结果：新增 {report.inserted}，跳过重复 {report.skipped}，失败 {report.failed}"
    )
    if report.message:
        print(f"说明：{report.message}")

    if report.errors:
        print("失败行：")
        for error in report.errors[:10]:
            field = f"[{error.field}] " if error.field else ""
            print(f"  第 {error.row_number} 行 {field}{error.code}：{error.message}")
        if len(report.errors) > 10:
            print(f"  ……还有 {len(report.errors) - 10} 条，见报告文件")
    if report.report_path:
        print(f"失败行报告：{report.report_path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导入答案（CSV/JSONL）")
    parser.add_argument("--file", required=True, help="要导入的文件")
    parser.add_argument("--tracker", default=None, help="监控对象 id，默认取该租户第一个")
    parser.add_argument("--tenant", default=None, help="租户 id，默认取 GEO_TENANT_ID")
    parser.add_argument("--format", default="auto", choices=["auto", "csv", "jsonl"], help="文件格式")
    parser.add_argument("--dry-run", action="store_true", help="只校验不落库（会回滚）")
    parser.add_argument(
        "--create-questions", action="store_true", help="问题不存在时自动建到该监控对象下"
    )
    parser.add_argument(
        "--on-error", default="skip", choices=["skip", "abort"], help="skip=坏行跳过，abort=有错整批不落库"
    )
    parser.add_argument("--report-dir", default=None, help="失败行报告目录")
    parser.add_argument("--json", action="store_true", help="输出 JSON，便于脚本处理")
    args = parser.parse_args(argv)

    db.init_db()
    conn = db.connect()
    try:
        report = import_service.import_file(
            conn,
            path=Path(args.file),
            tenant_id=args.tenant,
            tracker_id=args.tracker,
            dry_run=args.dry_run,
            create_questions=args.create_questions,
            on_error=args.on_error,
            report_dir=args.report_dir,
        )
    except import_service.ImportFailure as exc:
        if args.json:
            print(json.dumps({"status": "failed", "message": str(exc)}, ensure_ascii=False))
        else:
            print(f"[x] {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # 落库阶段的意外：保留原始异常，方便定位
        if args.json:
            print(json.dumps({"status": "failed", "message": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        else:
            print(f"[x] 导入中断：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    if args.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    else:
        _print_human(report)

    if report.status == "aborted":
        return 2
    return 2 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
