"""恢复演练：把旧版备份恢复到当前代码，验证迁移自动补齐。

为什么需要它：roadmap H4-4 的判据是「从备份恢复到可用状态」，但现有测试只在
fixture 造的小库上验过 restore。真实 backups/ 里躺着 v15 的旧备份，当前代码已经
到 v20（跨 016/017/018/019/020 五个迁移）。真出事要恢复时才发现迁移没接上，
是最糟的时机——那时没有第二台机器可复现。

本脚本用真实旧备份（只读）演一遍：
  旧备份 → 恢复到临时"当前库" → 迁移自动跑到 v20 → 数据条数回来 → 安全副本在
全程 tempfile，不碰真实 geo.db、不碰 backups/ 里的文件。

    python backend/scripts/restore_rehearse.py

退出码：0 全通过；1 某步失败；2 没有可用的旧备份（演练前提不成立）。
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))

from geo import db, migrate  # noqa: E402
from geo.services import backup_service  # noqa: E402
from geo.services.backup_service import BackupError  # noqa: E402


class RehearsalError(Exception):
    pass


def _schema_version(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_migration").fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


def _counts(path: Path) -> dict[str, int]:
    conn = sqlite3.connect(path)
    try:
        def n(table: str) -> int:
            try:
                return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            except sqlite3.Error:
                return 0
        return {"answers": n("answer"), "mentions": n("mention"), "citations": n("citation")}
    finally:
        conn.close()


def find_old_backup() -> Path:
    """挑一份 schema 比当前代码旧的真实备份；没有就说明这个演练现在没意义。"""
    current_max = max(m.version for m in migrate.discover())
    folder = BACKEND / "backups"
    candidates = sorted(folder.glob("geo-*.db"), reverse=True) if folder.is_dir() else []
    for path in candidates:
        if _schema_version(path) < current_max:
            return path
    raise RehearsalError(
        f"backups/ 里没有比当前 v{current_max} 更旧的备份，恢复演练缺输入；"
        "先造一份旧库备份再跑"
    )


def rehearse() -> dict:
    source = find_old_backup()
    source_version = _schema_version(source)
    source_counts = _counts(source)
    print(f"恢复源：{source.name}（schema v{source_version}，{source_counts['answers']} 条答案）")

    with tempfile.TemporaryDirectory(prefix="geo-restore-") as tmp:
        root = Path(tmp)
        # "当前库" = 旧备份的一份副本。真实恢复是把备份盖到当前库上，
        # 这里让当前库本来就是旧结构，才能验"恢复后迁移自动补齐"。
        target = root / "current.db"
        shutil.copyfile(source, target)
        backup_dir = root / "backups"
        backup_dir.mkdir()
        env = mock_env(backup_dir)

        try:
            result = backup_service.restore_backup(source, target)
        except BackupError as exc:
            raise RehearsalError(f"恢复失败：[{exc.code}] {exc}") from exc
        finally:
            env.stop()

        expected_version = max(m.version for m in migrate.discover())
        final_version = _schema_version(target)
        if final_version != expected_version:
            raise RehearsalError(
                f"恢复后 schema 是 v{final_version}，期望 v{expected_version}"
                f"（迁移没自动补齐：{result['migrations_applied']}）"
            )
        print(f"  ✓ 迁移自动补齐 v{source_version} → v{final_version}"
              f"（应用 {result['migrations_applied']}）")

        after = _counts(target)
        if after != source_counts:
            raise RehearsalError(f"恢复后数据不一致：{source_counts} → {after}")
        print(f"  ✓ 数据回来：{after['answers']} 答案 / {after['mentions']} 提及 / {after['citations']} 引用")

        safety = result.get("safety_copy")
        if not safety or not Path(safety).is_file():
            raise RehearsalError("恢复前没有生成 pre-restore 安全副本，误恢复时无法回退")
        print(f"  ✓ 安全副本：{Path(safety).name}")

        # 恢复后的库要能被当前代码正常查询：验迁移新增的列和表都在。
        # 尤其 answer_correction 是 020 迁移才建的——它在 = 020 真跑过了，
        # 而不是只有版本号被改了。
        conn = sqlite3.connect(target)
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(answer)")}
            for required in ("client", "sample_day", "search_called", "text_hash"):
                if required not in cols:
                    raise RehearsalError(f"恢复后的 answer 表缺列 {required}")
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if "answer_correction" not in tables:
                raise RehearsalError("恢复后缺表 answer_correction（020 迁移没建出来）")
            article_cols = {r[1] for r in conn.execute("PRAGMA table_info(article)")}
            if "images" not in article_cols:
                raise RehearsalError("恢复后 article 缺 images 列（019 迁移没生效）")
        finally:
            conn.close()
        print("  ✓ 恢复后迁移新增的列与表齐全（含 019 images 列 / 020 纠正表），当前代码可查询")

        # 表存在 ≠ 功能可用。恢复后的库要真能写入 P2 的人工纠正，
        # 且 answer 删掉时纠正随之外键级联消失——否则备份恢复完，
        # 用户点的"纠正"会写不进去，或留一堆孤儿行。
        from geo.services import correction_service

        conn = sqlite3.connect(target)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            answer_id = conn.execute("SELECT id FROM answer ORDER BY id LIMIT 1").fetchone()[0]
            correction_service.upsert_correction(
                conn,
                answer_id=answer_id,
                tenant_id="00000000-0000-0000-0000-000000000001",
                payload={"note": "恢复演练临时写入"},
            )
            conn.commit()
            read = correction_service.get_correction(
                conn, answer_id, "00000000-0000-0000-0000-000000000001"
            )
            if not read or read["note"] != "恢复演练临时写入":
                raise RehearsalError("恢复后的纠正写不进/读不回，P2 功能在这份库上不可用")
            # 级联：走真实的 delete_tracker（它会按依赖顺序删子表）。
            # 不能直接 DELETE answer——answer 还被 citation/mention/run_item 引用，
            # 单独删会撞外键，那是测试写法错，不是产品 bug。
            from geo.repositories import config_repository as config_repo

            tracker_id = conn.execute(
                "SELECT tracker_id FROM answer WHERE id = ?", (answer_id,)
            ).fetchone()[0]
            config_repo.delete_tracker(conn, tracker_id, "00000000-0000-0000-0000-000000000001")
            conn.commit()
            left = conn.execute(
                "SELECT COUNT(*) FROM answer_correction WHERE answer_id = ?", (answer_id,)
            ).fetchone()[0]
            if left != 0:
                raise RehearsalError(f"删对象后纠正留了 {left} 条孤儿行")
        finally:
            conn.close()
        print("  ✓ 恢复后纠正可写入回显，删除对象时级联清掉纠正")

        return {
            "source": source.name,
            "from_version": source_version,
            "to_version": final_version,
            "counts": after,
            "migrations": result["migrations_applied"],
        }


def mock_env(backup_dir: Path):
    """把备份目录指到临时目录——恢复前会自动存 pre-restore 副本，不能污染真实 backups/。"""
    from unittest import mock

    patcher = mock.patch.dict(os.environ, {"GEO_BACKUP_DIR": str(backup_dir)})
    patcher.start()
    return patcher


def main() -> int:
    print("恢复演练（真实旧备份 → 临时库 → 迁移补齐，不碰真实库）")
    try:
        result = rehearse()
    except RehearsalError as exc:
        print(f"[x] {exc}", file=sys.stderr)
        return 1
    print(f"\n✓ 恢复演练通过：{result['source']} v{result['from_version']} "
          f"恢复到 v{result['to_version']}，数据完整。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
