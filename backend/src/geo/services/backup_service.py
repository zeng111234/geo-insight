"""数据库备份与恢复。

## 为什么不用 `copy geo.db`

SQLite 的写入可能还留在 ``-wal`` 文件里，或者有事务尚未落盘。直接复制主文件会得到
「少了最近几条」甚至「文件头与页不一致」的库——而且**打开时不一定报错**：
它会静静地少几条数据，等你对不上账时才发现，那时已经不知道缺的是哪一段。

``VACUUM INTO`` 由 SQLite 自己生成一致快照（读事务隔离 + 顺带整理碎片），
**服务在跑的时候也能安全执行**。这是它相对 copy 的唯一但决定性的优势：
备份不该要求先停服务，否则「忘了停」就会产出一个看起来正常、实际残缺的文件。

## 恢复为什么要先自动备份

恢复是覆盖操作，而且要停服务。万一选错了文件（比如拿了三天前的快照），
没有自动备份就再也回不到恢复前的状态。所以每次恢复前先把当前库存一份
``pre-restore-*``，它可以被误操作后的自己救回来。

## 恢复后必须跑迁移

备份可能是旧版本的库（备份时 schema 是 v13，现在是 v15）。
直接拿来用会让新代码查不存在的列。所以恢复完成后立刻 apply 迁移链，
和启动时走同一条路径——不另写一套升级逻辑。
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from geo import migrate
from geo.settings import BACKEND_DIR, settings


class BackupError(RuntimeError):
    """备份/恢复失败。code 供 CLI 区分该提示什么。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def backup_dir() -> Path:
    """备份目录。``GEO_BACKUP_DIR`` 可覆盖——测试必须能指向临时目录，
    否则每跑一次测试就往真实的 backups/ 里塞几个 4MB 文件。"""
    override = os.getenv("GEO_BACKUP_DIR")
    return Path(override) if override else BACKEND_DIR / "backups"


def _stamp(moment: datetime | None = None) -> str:
    return (moment or datetime.now()).strftime("%Y%m%d-%H%M%S")


def create_backup(
    db_path: str | Path | None = None,
    dest: str | Path | None = None,
    *,
    prefix: str = "geo",
) -> dict:
    """生成一份一致快照，返回文件信息（含答案数，供人一眼确认备份的是哪一版）。"""
    source = Path(db_path or settings.database_path)
    if not source.is_file():
        raise BackupError("NO_DATABASE", f"数据库不存在：{source}")

    target = Path(dest) if dest else backup_dir() / f"{prefix}-{_stamp()}.db"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        # 同一秒备份两次会撞名。覆盖是危险的：可能正在备份的文件被当成新备份。
        raise BackupError("EXISTS", f"备份文件已存在：{target.name}（同一秒内重复备份）")

    try:
        with closing(sqlite3.connect(source)) as conn:
            # VACUUM INTO 的路径用参数绑定，避免文件名里有引号时拼出错的 SQL
            conn.execute("VACUUM INTO ?", (str(target),))
    except sqlite3.OperationalError as exc:
        # 源库有未提交的写事务、或别处长期持锁时，VACUUM 拿不到排他锁。
        # 这里必须失败：给一份"少了最近几条"的备份，比明说没做成危险得多——
        # 用户会以为手里有可用备份，等真需要它时才发现缺了关键的一段。
        target.unlink(missing_ok=True)
        raise BackupError("LOCKED", f"源库正被写入，拿不到一致性快照：{exc}") from exc

    info = verify_backup(target)
    if not info["ok"]:
        # 刚做出来的备份打不开，说明源库本身有问题，不能留着冒充好备份
        raise BackupError(
            "BAD_BACKUP", f"备份刚生成就校验失败：{info['integrity']}（源库可能已损坏）"
        )

    return {"path": str(target), "size": target.stat().st_size, **info}


def verify_backup(path: str | Path) -> dict:
    """只读打开一个备份，报出它是否可用、里面有多少数据。

    用 ``mode=ro``：校验动作绝不能创建或修改文件，
    否则一个路径打错就会凭空造出一个 0 字节的"备份"。
    """
    target = Path(path)
    if not target.is_file():
        raise BackupError("NOT_FOUND", f"文件不存在：{target}")

    uri = f"file:{target.as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise BackupError("UNREADABLE", f"打不开：{exc}") from exc

    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        tables = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'"
        ).fetchone()[0]
        version = _scalar(conn, "SELECT MAX(version) FROM schema_migration")
        answers = _scalar(conn, "SELECT COUNT(*) FROM answer")
        mentions = _scalar(conn, "SELECT COUNT(*) FROM mention")
        citations = _scalar(conn, "SELECT COUNT(*) FROM citation")
    except sqlite3.Error as exc:
        raise BackupError("NOT_A_DATABASE", f"不是可用的 SQLite 库：{exc}") from exc
    finally:
        conn.close()

    return {
        "integrity": integrity,
        "schema_version": version,
        "tables": tables,
        "answers": answers,
        "mentions": mentions,
        "citations": citations,
        # 没有迁移表的文件不是我们的库；完整性检查不是 ok 的更不能用
        "ok": integrity == "ok" and version is not None,
    }


def _scalar(conn: sqlite3.Connection, sql: str):
    """查一个值；表不存在返回 None（老库或残缺库不该让校验直接崩）。"""
    try:
        return conn.execute(sql).fetchone()[0]
    except sqlite3.Error:
        return None


def restore_backup(
    backup_path: str | Path,
    db_path: str | Path | None = None,
    *,
    make_safety_copy: bool = True,
) -> dict:
    """用备份覆盖当前库。返回恢复前后的数据量对照。

    **调用前必须停掉服务。** 这里会尝试拿独占锁，拿不到就明确报错，
    不会"尽力而为"——半途覆盖到一半失败，比直接拒绝糟得多。
    """
    source = Path(backup_path)
    target = Path(db_path or settings.database_path)

    info = verify_backup(source)
    if not info["ok"]:
        raise BackupError("BAD_BACKUP", f"这个备份不能用：integrity={info['integrity']}")
    if source.resolve() == target.resolve():
        raise BackupError("SAME_FILE", "备份就是当前库，无需恢复")

    # 独占检查放在最前面：它是最常见的失败原因（服务没停），
    # 先说清楚这一条，比让用户看到"安全副本失败"再猜要快。
    _ensure_exclusive(target)

    safety = None
    if target.is_file() and make_safety_copy:
        # 恢复前先存一份当前状态：选错文件时还能回来
        safety = create_backup(target, prefix="pre-restore")
    before = _counts(target)

    _drop_sidecars(target)

    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(target)) as dst:
        # 用 SQLite 的 backup API 而不是文件复制：它自己处理页一致性与锁，
        # 目标库已经被我们独占，不会有人在这期间写入。
        src.backup(dst)

    # 恢复的可能是旧 schema，立刻升到最新，与启动走同一条迁移链
    with closing(sqlite3.connect(target)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        applied = migrate.apply(conn)

    after = _counts(target)
    return {
        "path": str(target),
        "from": str(source),
        "safety_copy": safety["path"] if safety else None,
        "before": before,
        "after": after,
        # 拼成一句给人看的话；调用方直接打印，不需要再知道它是 list
        "migrations_applied": "、".join(applied) if applied else None,
    }


def _counts(path: Path) -> dict:
    if not path.is_file():
        return {"answers": 0, "mentions": 0, "citations": 0}
    with closing(sqlite3.connect(path)) as conn:
        return {
            "answers": _scalar(conn, "SELECT COUNT(*) FROM answer") or 0,
            "mentions": _scalar(conn, "SELECT COUNT(*) FROM mention") or 0,
            "citations": _scalar(conn, "SELECT COUNT(*) FROM citation") or 0,
        }


def _ensure_exclusive(path: Path) -> None:
    """拿一次独占锁。拿不到说明有人在用（服务没停），直接拒绝。

    timeout=0：不等待。等 5 秒再失败的意义不大——真有人占用，
    5 秒后它多半还在，而"转圈 5 秒然后报错"看起来像卡死。
    """
    try:
        conn = sqlite3.connect(path, timeout=0)
    except sqlite3.Error as exc:
        raise BackupError("UNREADABLE", f"打不开目标库：{exc}") from exc
    try:
        conn.execute("BEGIN EXCLUSIVE")
        conn.rollback()
    except sqlite3.OperationalError as exc:
        raise BackupError(
            "BUSY", f"数据库正在被使用（服务还在跑？先停掉服务再恢复）：{exc}"
        ) from exc
    finally:
        conn.close()


def _drop_sidecars(path: Path) -> None:
    """删掉 -wal / -shm。

    留着旧 WAL 配新主文件，SQLite 会把它当成"未提交的变更"重放，
    结果是恢复出来的库里混着新旧两份数据——比恢复失败更难排查。
    """
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(f"{path}{suffix}")
        if sidecar.exists():
            sidecar.unlink()


def list_backups(directory: str | Path | None = None) -> list[dict]:
    """按时间倒序列出备份。只读文件与几个计数，不做完整校验（那要打开每个文件）。"""
    folder = Path(directory) if directory else backup_dir()
    if not folder.is_dir():
        return []

    items = []
    for path in folder.glob("*.db"):
        stat = path.stat()
        with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as conn:
            items.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "size": stat.st_size,
                    "mtime": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    "answers": _scalar(conn, "SELECT COUNT(*) FROM answer") or 0,
                    "schema_version": _scalar(conn, "SELECT MAX(version) FROM schema_migration"),
                    "is_safety": path.name.startswith("pre-restore-"),
                }
            )
    items.sort(key=lambda item: item["mtime"], reverse=True)
    return items


def prune_backups(keep: int = 7, directory: str | Path | None = None) -> list[str]:
    """只保留最近 ``keep`` 份常规备份，返回被删的文件名。

    ``pre-restore-*`` 不参与清理：那是"恢复出错时唯一能回退的东西"，
    自动删掉它等于拆掉安全带。它们数量少（只有恢复时才产生），手动清即可。
    """
    folder = Path(directory) if directory else backup_dir()
    if not folder.is_dir() or keep < 1:
        return []

    regular = sorted(
        (p for p in folder.glob("geo-*.db")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    removed = []
    for path in regular[keep:]:
        path.unlink()
        removed.append(path.name)
    return removed
