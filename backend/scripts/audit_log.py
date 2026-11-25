"""查看审计日志：谁在什么时候删了什么。

用法：

    python scripts/audit_log.py                    # 最近 20 条
    python scripts/audit_log.py --limit 50
    python scripts/audit_log.py --action alias.delete
    python scripts/audit_log.py --object-type competitor --json

审计只记"删改"这类不可逆或有副作用的动作，不记读操作 —— 记全量读会淹掉真正要查的那条。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import unicodedata
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))
os.chdir(BACKEND)

from geo import db  # noqa: E402
from geo.services import audit_service  # noqa: E402
from geo.settings import settings  # noqa: E402

# 每类对象在列表里值得一眼看到的字段，从 before 快照里摘
SUMMARY_FIELDS = {
    "brand_alias": ("alias", "alias_type"),
    "competitor": ("name",),
    # 回滚审计靠 source_name + 删前计数一眼认出是哪个批次（before 由 import_service 落）
    "import_batch": ("source_name",),
}


def width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def pad(text: str, target: int) -> str:
    return text + " " * max(0, target - width(text))


def clip(text: str, target: int) -> str:
    out, used = "", 0
    for ch in text:
        step = 2 if unicodedata.east_asian_width(ch) in "WF" else 1
        if used + step > target:
            return out + "…"
        out += ch
        used += step
    return out


def describe(row: dict) -> str:
    before = row.get("before") or {}
    fields = SUMMARY_FIELDS.get(row["object_type"], ())
    bits = [f"{k}={before[k]}" for k in fields if before.get(k) is not None]
    if "set_version_after" in before:
        bits.append(f"集合版本→v{before['set_version_after']}")
    return " ".join(bits)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="查看审计日志")
    parser.add_argument("--limit", type=int, default=20, help="最多显示多少条（默认 20）")
    parser.add_argument("--action", default=None, help="只看某个动作，如 alias.delete")
    parser.add_argument("--object-type", default=None, help="只看某类对象，如 competitor")
    parser.add_argument("--tenant", default=None, help="租户 id，默认取配置里的默认租户")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    tenant_id = args.tenant or settings.default_tenant_id
    conn = db.connect()
    try:
        rows = audit_service.recent(
            conn, tenant_id, limit=args.limit, action=args.action, object_type=args.object_type
        )
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
            return 0
        if not rows:
            print("没有审计记录。删除对象或回滚导入批次之后再来看看。")
            return 0

        print(f"审计日志（最近 {len(rows)} 条）\n")
        for row in rows:
            actor = row["actor_email"] or ("（未登录操作）" if not row["actor_id"] else row["actor_id"])
            print(
                f"{row['created_at'][:19].replace('T', ' ')}  "
                f"{pad(clip(actor, 24), 26)}"
                f"{pad(row['action'], 20)}"
                f"{describe(row)}"
            )
        print(f"\n库：{settings.database_path}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
