"""AI 写作真调验证：用现有 DeepSeek key 生成一篇真草稿。

只在临时库副本上跑，绝不污染主库 geo.db。产出（标题/事实/阶段/账本）回读打印，
原始响应落盘可回溯。key 从既有 keys.local.json / 环境变量读，不打印。

用法：python backend/scripts/writer_eval.py [--tracker ID] [--reference "参考要点"]
退出码：真调+落库全对 0；失败 1。
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))
os.chdir(BACKEND)

from geo.repositories import sqlite_repository as repo  # noqa: E402
from geo.services import assistant_service, credit_service  # noqa: E402
from geo.settings import settings  # noqa: E402

DEFAULT_TRACKER = "d3aae9ce-cb61-468a-9ab2-51d2f4d28bf1"  # 惠策 · GEO服务


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AI 写作真调验证（临时库隔离）")
    ap.add_argument("--tracker", default=DEFAULT_TRACKER)
    ap.add_argument("--reference", default="", help="人工贴入的参考要点（不爬取）")
    args = ap.parse_args(argv)

    if not assistant_service.available():
        print("[x] 没有 DeepSeek key——设 GEO_DEEPSEEK_API_KEY 或写 backend/data/keys.local.json")
        return 1

    tmp = tempfile.TemporaryDirectory()
    try:
        copy_path = Path(tmp.name) / "writer-eval.db"
        source = Path(settings.database_path)
        _copy(source, copy_path)
        # settings 是 frozen dataclass，db.connect() 又只认 settings.database_path。
        # 直接对副本 sqlite3.connect，行为与 db.connect 一致（row_factory + 外键），
        # 绝不碰主库连接——否则真调会写进主库，这是安全事故。
        import sqlite3
        conn = sqlite3.connect(str(copy_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            tracker = repo.get_tracker(conn, args.tracker, settings.default_tenant_id)
            if not tracker:
                print(f"[x] 临时库里找不到对象 {args.tracker}")
                return 1
            print(f"对象：{tracker['name']}  brand={tracker.get('brand_name')}")
            before = conn.execute("SELECT COUNT(*) FROM article").fetchone()[0]
            result = assistant_service.generate_draft(
                conn, args.tracker, settings.default_tenant_id,
                reference=args.reference,
            )
            conn.commit()
            after = conn.execute("SELECT COUNT(*) FROM article").fetchone()[0]
            facts = conn.execute(
                "SELECT claim, source_ref FROM article_fact WHERE article_id = ? ORDER BY position",
                (result["id"],),
            ).fetchall()
            stages = conn.execute(
                "SELECT stage, status FROM article_stage WHERE article_id = ? ORDER BY stage",
                (result["id"],),
            ).fetchall()
            balance = credit_service.balance(conn, settings.default_tenant_id)
            print("\n=== 真调结果 ===")
            print(f"标题：{result['title']}")
            print(f"正文：{result['body'][:200]}{'…' if len(result['body']) > 200 else ''}")
            print(f"模型：{result['model_version']}  token：{result['token_in']}→{result['token_out']}")
            print(f"原始响应：{result['raw_uri']}")
            print(f"事实条目：{len(facts)} 条")
            for f in facts:
                print(f"  - {f['claim'][:60]}  ← {f['source_ref'] or '无来源'}")
            print(f"六阶段：{len(stages)} 个，状态={sorted(set(s['status'] for s in stages))}")
            print(f"文章数：{before} → {after}  积分余额：{balance}")
            print(f"数据来源标记：{result['source']}")
            ok = (after == before + 1 and len(stages) == 6 and balance <= -5
                  and bool(result["raw_uri"]) and bool(result["title"]) and bool(result["body"]))
            print("\n" + ("[OK] 真调+落库全对" if ok else "[x] 有项没对上"))
            return 0 if ok else 1
        finally:
            conn.close()
    finally:
        tmp.cleanup()


def _copy(src: Path, dst: Path) -> None:
    import shutil
    shutil.copy2(src, dst)


if __name__ == "__main__":
    raise SystemExit(main())
