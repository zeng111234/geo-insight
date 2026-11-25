"""提及识别评估：在人工标注样本上算精确率/召回率，导出漏检清单。

用法：

    python scripts/mention_eval.py                       # 默认 30 条标注样本
    python scripts/mention_eval.py --out misses.csv      # 漏检清单导出 CSV
    python scripts/mention_eval.py --samples my.json     # 换一份标注样本

样本文件见 ``tests/labeled_mentions.json``（30 条，含别名配置与标注说明）。
真实采集跑起来之后，把真实答案人工标注一遍替换进去即可——评估器不关心
样本从哪来，只认「text + mentioned」。

口径（FR-008）：精确率与召回率 ≥ 0.8 算过线。漏检（标注说提及、识别没
找到）是召回缺口，直接把提及率的分子做小，优先修；误报是精确缺口，会
虚提提及率。清单分开打印，改 ``brand_alias`` 配置时知道该看哪份。

退出码：0 过线；2 未过线（可挂定时任务报警）；1 参数/文件错误。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))

from geo.domain import mention_parser  # noqa: E402

THRESHOLD = 0.8


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="提及识别精确率/召回率评估")
    parser.add_argument(
        "--samples",
        default=str(BACKEND / "tests" / "labeled_mentions.json"),
        help="标注样本 JSON 路径（默认 tests/labeled_mentions.json）",
    )
    parser.add_argument("--out", default=None, help="漏检清单导出 CSV 路径")
    args = parser.parse_args(argv)

    path = Path(args.samples)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"[x] 样本文件读不了：{path}（{exc}）", file=sys.stderr)
        return 1

    aliases = [item["alias"] for item in data.get("aliases", [])]
    samples = data.get("samples", [])
    if not aliases or not samples:
        print("[x] 样本文件缺 aliases 或 samples", file=sys.stderr)
        return 1

    result = mention_parser.evaluate(samples, aliases)

    print(f"品牌：{data.get('brand', '?')}    别名 {len(aliases)} 个    样本 {result['samples']} 条")
    print(
        f"命中 {result['true_positive']}    "
        f"精确率 {result['precision']:.2%}    召回率 {result['recall']:.2%}    "
        f"（过线口径：双双 ≥ {THRESHOLD:.0%}）"
    )
    if result["misses"]:
        print(f"\n漏检 {len(result['misses'])} 条（标注为提及，识别没找到）：")
        for miss in result["misses"]:
            print(f"  #{miss['index']:>2}  {miss['text'][:48]}…")
    if result["false_alarms"]:
        print(f"\n误报 {len(result['false_alarms'])} 条（标注未提及，识别报了）：")
        for alarm in result["false_alarms"]:
            print(f"  #{alarm['index']:>2}  {alarm['text'][:48]}…")

    if args.out:
        with open(args.out, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["序号", "样本原文", "标注说明"])
            for miss in result["misses"]:
                writer.writerow([miss["index"], miss["text"], miss.get("note", "")])
        print(f"\n漏检清单已导出：{args.out}")

    passed = result["precision"] >= THRESHOLD and result["recall"] >= THRESHOLD
    if not passed:
        print("\n[x] 未过线：先补 brand_alias（错写清单最见效），再重跑。", file=sys.stderr)
        return 2
    print("\n[ok] 过线。改过别名后记得跑重跑接口刷新存量数据。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
