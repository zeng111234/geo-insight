"""文档承诺 vs 数据库真相的对账（只读，不改任何数据）。

背景：审查发现若干「文档写已完成，库里对不上」的落差——
  - README:521 / video-reference-plan:390 说监控对象 19 个，当前库只有个位数；
  - 决策 009:22 说 credit_ledger「每次生成记一条流水」，库里 0 行；
  - 决策 011 说真实发布，publication 里 published 多为 seed 的 demo-article 假链接。

这些落差靠人肉翻库才发现。本脚本把三项做成可复现对账：打印实际值、承诺值、差额，
不符就 WARN 并在退出码里体现。它不修数据、不改文档——只让落差可见、可重复、可追。

用法：
    python backend/scripts/auditgapcheck.py          # 用默认 geo.db
    python backend/scripts/auditgapcheck.py --db /path/to/geo.db
    python backend/scripts/auditgapcheck.py --json   # 机器可读

退出码：三项全对上 0；有落差 1；库打不开 2。
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))
os.chdir(BACKEND)

from geo.settings import settings  # noqa: E402

# 文档承诺值。出处写在旁边，改承诺就改这里，别散落在判断里。
PROMISES = {
    "tracker_count": {
        "expected": 19,
        "source": "README.md:521 / docs/video-reference-plan.md:390",
        "label": "监控对象数（文档承诺 19）",
    },
    "credit_ledger_rows": {
        "expected": 1,  # 「每次生成记一条流水」→ 至少 1 行才算真接线
        "source": "docs/decisions/009-full-replica.md:22",
        "label": "积分流水行数（决策009:每次生成记一条，≥1）",
    },
    "real_publications": {
        "expected": 1,  # 至少 1 篇真发出去（URL 可访问且非 demo 假链）
        "source": "docs/decisions/011-real-channel-integration.md / effect-pilot-plan.md:79",
        "label": "真实发布成功数（至少 1 篇真发，非 demo 假链）",
    },
}


def _count(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def measure(db_path: Path) -> dict:
    """只读测三项实际值，全部来自库的真实计数。"""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        tracker_count = _count(conn, "SELECT COUNT(*) FROM tracker")

        # credit_ledger 表可能还没建（老库），视为 0 而不是崩。
        try:
            ledger_rows = _count(conn, "SELECT COUNT(*) FROM credit_ledger")
        except sqlite3.OperationalError:
            ledger_rows = 0

        # 真实发布 = status='published' 且 published_url 是 http(s) 且不是 seed 的 demo-article 假链。
        # 口径与前端 pubtextcheck 的 isValidUrl 保持一致。
        try:
            real_pubs = _count(
                conn,
                """SELECT COUNT(*) FROM publication
                   WHERE status = 'published'
                     AND published_url IS NOT NULL
                     AND published_url GLOB 'http*'
                     AND published_url NOT LIKE '%demo-article%'""",
            )
            all_published = _count(conn, "SELECT COUNT(*) FROM publication WHERE status = 'published'")
        except sqlite3.OperationalError:
            real_pubs, all_published = 0, 0
        return {
            "tracker_count": tracker_count,
            "credit_ledger_rows": ledger_rows,
            "real_publications": real_pubs,
            "published_raw": all_published,
        }
    finally:
        conn.close()


def reconcile(actual: dict) -> tuple[list[dict], int]:
    """逐项对账，返回 (结果列表, 落差项数)。"""
    results = []
    gaps = 0
    for key, promise in PROMISES.items():
        got = actual[key]
        expected = promise["expected"]
        ok = got >= expected
        if not ok:
            gaps += 1
        results.append({
            "key": key,
            "label": promise["label"],
            "actual": got,
            "expected": expected,
            "source": promise["source"],
            "status": "OK" if ok else "GAP",
            "detail": ("" if ok else f"差 {expected - got}（文档/决策写 {expected}，实际 {got}）"),
        })
    return results, gaps


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="文档承诺 vs 数据库真相对账（只读）")
    ap.add_argument("--db", default=str(settings.database_path), help="geo.db 路径")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    db_path = Path(args.db)
    # 上面 os.chdir(BACKEND) 后，命令行传的相对路径应相对调用时的工作目录解析。
    if not db_path.is_absolute():
        db_path = (Path.cwd() / db_path).resolve()
    if not db_path.exists():
        print(f"[x] 库不存在：{db_path}", file=sys.stderr)
        return 2
    try:
        actual = measure(db_path)
    except sqlite3.Error as exc:
        print(f"[x] 读库失败：{exc}", file=sys.stderr)
        return 2

    results, gaps = reconcile(actual)
    if args.json:
        print(json.dumps({"db": str(db_path), "actual": actual, "results": results, "gaps": gaps},
                         ensure_ascii=False, indent=2))
    else:
        print(f"对账：{db_path}")
        print("-" * 68)
        for r in results:
            mark = "OK " if r["status"] == "OK" else "GAP"
            print(f"[{mark}] {r['label']}: 实际 {r['actual']} / 承诺 {r['expected']}")
            if r["detail"]:
                print(f"       {r['detail']}")
            print(f"       出处：{r['source']}")
        print("-" * 68)
        print(f" Publication 原始 published={actual['published_raw']}，其中真发={actual['real_publications']}（其余为演示/无效链接）")
        print(f" 结论：{gaps} 项落差" + ("（文档与真相不一致，见上）" if gaps else "（全部对上）"))
    return 1 if gaps else 0


if __name__ == "__main__":
    raise SystemExit(main())
