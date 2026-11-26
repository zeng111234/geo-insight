"""备份 / 校验 / 恢复数据库。

    python backend/scripts/backup.py                  # 备份一次
    python backend/scripts/backup.py --list           # 看有哪些备份
    python backend/scripts/backup.py --verify 某文件   # 校验一个备份能不能用
    python backend/scripts/backup.py --restore 某文件  # 用它覆盖当前库（**先停服务**）
    python backend/scripts/backup.py --keep 7         # 备份后只留最近 7 份

备份可以在服务运行时做：用的是 SQLite 的 VACUUM INTO，不要求先停服务。
**恢复必须先停服务**——这里会尝试拿独占锁，拿不到就明确报错，不会硬写。

退出码：0 成功；1 失败（含恢复时库被占用）；2 参数问题。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))

from geo.services import backup_service  # noqa: E402
from geo.services.backup_service import BackupError  # noqa: E402


def resolve_backup_arg(raw: str) -> str:
    """把用户给的备份名解析成能打开的路径。

    `--list` 打印的是文件名（`geo-2026...db`），照抄去 `--verify` / `--restore`
    时如果按 cwd 解析就找不到——备份实际在 backups/ 子目录。所以先按文件名在
    backup_dir() 里找；传的是完整路径、或 backups 里没有时，原样返回交给
    verify_backup 报 NOT_FOUND，不静默改写成别的路径。
    """
    from pathlib import Path

    given = Path(raw)
    if given.is_file():
        return raw  # 完整路径/相对 cwd 已存在，照用
    candidate = backup_service.backup_dir() / given.name
    if candidate.is_file():
        return str(candidate)
    return raw  # 找不到时保留原值，错误信息里显示用户实际输入


def _mb(size: int) -> str:
    return f"{size / 1024 / 1024:.1f} MB"


def _describe(info: dict) -> str:
    version = info.get("schema_version")
    return (
        f"{info['answers']} 答案 / {info['mentions']} 提及 / {info['citations']} 引用"
        f" · schema v{version}"
    )


def cmd_backup(keep: int | None) -> int:
    info = backup_service.create_backup()
    print(f"[ok] 已备份：{info['path']}")
    print(f"     {_mb(info['size'])} · {_describe(info)}")
    if keep:
        removed = backup_service.prune_backups(keep)
        if removed:
            print(f"     清理旧备份 {len(removed)} 份：{'、'.join(removed)}")
    return 0


def cmd_list() -> int:
    items = backup_service.list_backups()
    if not items:
        print("没有备份。跑一次 `python backend/scripts/backup.py` 试试。")
        return 0

    width = max(len(item["name"]) for item in items)
    for item in items:
        mark = "（恢复前自动存的）" if item["is_safety"] else ""
        print(
            f"  {item['name'].ljust(width)}  {item['mtime']}  "
            f"{_mb(item['size']).rjust(9)}  {item['answers']} 答案  "
            f"v{item['schema_version']}{mark}"
        )
    print(f"\n共 {len(items)} 份，目录：{backup_service.backup_dir()}")
    return 0


def cmd_verify(path: str) -> int:
    info = backup_service.verify_backup(resolve_backup_arg(path))
    state = "可用" if info["ok"] else "不可用"
    print(f"[{state}] {path}")
    print(f"  integrity_check: {info['integrity']}")
    print(f"  {_describe(info)} · {info['tables']} 张表")
    return 0 if info["ok"] else 1


def cmd_restore(path: str) -> int:
    print("恢复会覆盖当前数据库。如果服务还在跑，请先关掉它的窗口。")
    result = backup_service.restore_backup(resolve_backup_arg(path))
    before, after = result["before"], result["after"]
    print(f"[ok] 已用 {Path(result['from']).name} 恢复到 {result['path']}")
    print(
        f"     恢复前：{before['answers']} 答案 / {before['mentions']} 提及 / "
        f"{before['citations']} 引用"
    )
    print(
        f"     恢复后：{after['answers']} 答案 / {after['mentions']} 提及 / "
        f"{after['citations']} 引用"
    )
    if result["safety_copy"]:
        print(f"     恢复前的库已另存：{Path(result['safety_copy']).name}（选错文件时用它回退）")
    if result["migrations_applied"]:
        print(f"     备份是旧结构，已应用迁移：{result['migrations_applied']}")
    return 0


def cmd_prune(keep: int) -> int:
    removed = backup_service.prune_backups(keep)
    if removed:
        print(f"[ok] 删掉 {len(removed)} 份旧备份：{'、'.join(removed)}")
    else:
        print(f"不需要清理（不超过 {keep} 份）。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="备份 / 校验 / 恢复数据库")
    parser.add_argument("--list", action="store_true", help="列出已有备份")
    parser.add_argument("--verify", metavar="文件", help="校验一个备份文件")
    parser.add_argument("--restore", metavar="文件", help="用备份覆盖当前库（先停服务）")
    parser.add_argument("--keep", type=int, metavar="N", help="备份后只保留最近 N 份常规备份")
    parser.add_argument("--prune", type=int, metavar="N", help="只清理，不备份：保留最近 N 份")
    args = parser.parse_args(argv)

    try:
        if args.list:
            return cmd_list()
        if args.verify:
            return cmd_verify(args.verify)
        if args.restore:
            return cmd_restore(args.restore)
        # --prune 才是"只清理"。--keep 永远跟着备份一起用——
        # 早期把 --keep 单独用做成"只清理"，于是 bat 里一句 `--keep 14`
        # 压根没做备份，还打印"不需要清理"，看起来像成功了。
        if args.prune is not None:
            return cmd_prune(args.prune)
        return cmd_backup(args.keep)
    except BackupError as exc:
        print(f"[x] [{exc.code}] {exc}", file=sys.stderr)
        if exc.code == "BUSY":
            print("    停服务的办法：关掉 start.bat 的黑色命令行窗口，再重跑这条命令。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
