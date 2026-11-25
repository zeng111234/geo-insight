"""积分账本 CLI：人工发放额度 / 查余额 / 看流水。

决策 009：**账本是真的，定价是假的**。额度只由这个脚本发放，
不接支付、不对外收费。每次 AI 调用记一笔支出，余额 = 流水求和。

    python scripts/credits.py --balance
    python scripts/credits.py --grant 10000 --reason "试点初始额度"
    python scripts/credits.py --list --limit 20

金额单位：分。10000 分 = 100 元等值额度（只是演示口径，不代表真实收费）。
"""

from __future__ import annotations

import argparse
import sys

from geo import db
from geo.services import credit_service
from geo.settings import settings


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="积分账本（人工发放，不对外收费）")
    action = p.add_mutually_exclusive_group(required=True)
    action.add_argument("--balance", action="store_true", help="查当前余额")
    action.add_argument("--grant", type=int, metavar="分", help="发放额度（>0，单位分）")
    action.add_argument("--list", action="store_true", help="看最近流水")
    p.add_argument("--reason", default="", help="发放原因（--grant 必填）")
    p.add_argument("--limit", type=int, default=20, help="--list 的条数")
    args = p.parse_args(argv)

    if args.grant is not None and not args.reason.strip():
        print("[x] --grant 需要 --reason", file=sys.stderr)
        return 1

    conn = db.connect()
    try:
        # 库存在但没跑过迁移时，别抛裸 sqlite 异常让人猜
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='credit_ledger'"
        ).fetchone():
            print("[x] 账本表还没建：先启动一次服务（python run.py --no-browser）跑完迁移", file=sys.stderr)
            return 1
        tenant = settings.default_tenant_id
        if args.balance:
            print(f"余额：{credit_service.balance(conn, tenant)} 分（从流水求和）")
            return 0
        if args.list:
            items = credit_service.entries(conn, tenant, args.limit)
            if not items:
                print("还没有流水。用 --grant 发放初始额度。")
                return 0
            for e in items:
                sign = "+" if e["delta"] > 0 else ""
                print(
                    f"  {e['created_at']}  {sign}{e['delta']:>8}  {e['direction']:<8}"
                    f"  {e['reason']}"
                )
            return 0
        try:
            result = credit_service.grant(conn, tenant, args.grant, args.reason)
            conn.commit()
        except (credit_service.CreditError, ValueError) as exc:
            conn.rollback()
            print(f"[x] {exc}", file=sys.stderr)
            return 1
        print(f"已发放 {args.grant} 分，余额 {result['balance']} 分")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
