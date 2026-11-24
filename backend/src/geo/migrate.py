"""数据库迁移链：按版本号顺序应用 `backend/migrations/` 下的变更。

为什么不用 Alembic：本地一键启动的前提是零额外依赖（见
docs/decisions/002-local-run-no-docker.md）。Alembic 要拖进 SQLAlchemy 与一整套
autogenerate 配置，对"单进程 + 单文件 SQLite"收益不对等。这里只保留三件必需的能力：

1. 有序版本表 —— 记录哪些变更已经落地，重复启动不会重复执行；
2. checksum 校验 —— 已应用的迁移文件被事后改动会直接报错，而不是悄悄漂移；
3. 老库基线认领 —— 用旧 ``schema.sql`` 建出来的库可以平滑接入，不必删库丢数据。

迁移文件两种形式，按文件名数字前缀排序：

    NNN_name.sql    纯 DDL/DML 脚本
    NNN_name.py     Python 模块，需实现 ``apply(conn)``

两条规则，破坏任一条都会让迁移链失去可信度：

- **已应用的迁移不能再改。** 校验和不一致会报错，要改就新增一个版本号。
- **未应用的迁移必须能安全重跑。** ``.sql`` 用 ``IF NOT EXISTS`` 之类的守卫，
  ``.py`` 自己判断列/表是否存在。这样中途失败时，改完文件重跑即可，
  不需要依赖 DDL 事务回滚（SQLite 的 ``executescript`` 本身也不保证原子性）。
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from geo.settings import settings

NAME_RE = re.compile(r"^(\d{3,})_([A-Za-z0-9_]+)\.(sql|py)$")
# 建表语句里表名可能带引号或方括号，统一抓第一个标识符
CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"'\[`]?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
LINE_COMMENT_RE = re.compile(r"--[^\n]*")
VERSION_TABLE = "schema_migration"
# 老库认领基线时给标签加的记号：它不是"执行"出来的，而是"认出来"的
ADOPT_MARK = "（认领基线）"


class MigrationError(RuntimeError):
    """迁移链自身无法继续时的错误，消息直接面向人看。"""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    kind: str  # "sql" | "py"
    path: Path
    checksum: str

    @property
    def label(self) -> str:
        return f"{self.version:03d}_{self.name}"


def _checksum(path: Path) -> str:
    # 统一换行再算哈希：Windows 检出（CRLF）与仓库内容（LF）不应造成假不一致。
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def discover(directory: str | Path | None = None) -> list[Migration]:
    """扫描迁移目录，按版本号排序。文件名不合规或版本号重复都直接报错。"""
    base = Path(directory) if directory else Path(settings.migrations_dir)
    if not base.is_dir():
        raise MigrationError(f"迁移目录不存在：{base}")

    found: list[Migration] = []
    bad: list[str] = []
    for path in base.iterdir():
        if path.is_dir() or path.name.startswith(".") or path.name.startswith("_"):
            continue
        if path.suffix not in (".sql", ".py"):
            continue
        match = NAME_RE.match(path.name)
        if not match:
            bad.append(path.name)
            continue
        found.append(
            Migration(
                version=int(match.group(1)),
                name=match.group(2),
                kind=match.group(3),
                path=path,
                checksum=_checksum(path),
            )
        )

    if bad:
        raise MigrationError(
            "迁移文件名不合规（应为 001_init.sql 这种 NNN_名称.sql/.py）：" + "、".join(sorted(bad))
        )

    found.sort(key=lambda m: m.version)
    seen: dict[int, str] = {}
    for migration in found:
        if migration.version in seen:
            raise MigrationError(
                f"版本号 {migration.version:03d} 重复：{seen[migration.version]} 与 {migration.path.name}"
            )
        seen[migration.version] = migration.path.name
    return found


def ensure_version_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {VERSION_TABLE} (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def applied(conn: sqlite3.Connection) -> dict[int, sqlite3.Row]:
    ensure_version_table(conn)
    rows = conn.execute(f"SELECT * FROM {VERSION_TABLE} ORDER BY version").fetchall()
    return {int(r["version"]): r for r in rows}


def _existing_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r[0] for r in rows} - {VERSION_TABLE}


def _declared_tables(path: Path) -> set[str]:
    text = LINE_COMMENT_RE.sub("", path.read_text(encoding="utf-8"))
    return {m.group(1) for m in CREATE_TABLE_RE.finditer(text)}


def _record(conn: sqlite3.Connection, migration: Migration, now: str) -> None:
    conn.execute(
        f"INSERT INTO {VERSION_TABLE}(version, name, checksum, applied_at) VALUES (?, ?, ?, ?)",
        (migration.version, migration.name, migration.checksum, now),
    )
    # 给外部工具（sqlite3 命令行、DBeaver）一个直观的当前版本号
    conn.execute(f"PRAGMA user_version = {int(migration.version)}")
    conn.commit()


def _verify_recorded(conn: sqlite3.Connection, migrations: list[Migration]) -> None:
    by_version = {m.version: m for m in migrations}
    for version, row in applied(conn).items():
        current = by_version.get(version)
        if current is None:
            raise MigrationError(
                f"版本表记录了 {version:03d}_{row['name']}，但迁移目录里找不到对应文件。"
                "迁移文件不要删除；确需废弃请新增一个迁移去吊销它。"
            )
        if current.checksum != row["checksum"]:
            raise MigrationError(
                f"{current.label} 已应用过，但文件内容被改动了（checksum 不一致）。"
                "已应用的迁移必须保持原样，改动请新增一个版本号。"
            )


def _adopt_legacy(conn: sqlite3.Connection, migrations: list[Migration], now: str) -> Migration | None:
    """把旧 schema.sql 建出来的库认领到 001。

    只有当库非空、且 001 声明的表全部存在时才认领。缺表说明这个库不是 001 建的，
    继续往下跑会得到一堆莫名其妙的 "no such column"，不如在这里断开并说清原因。
    """
    existing = _existing_tables(conn)
    if not existing or not migrations:
        return None

    first = migrations[0]
    if first.kind != "sql":
        raise MigrationError(
            f"库里已有 {len(existing)} 张表但没有版本表，且首个迁移 {first.label} 不是 .sql，无法自动认领。"
        )
    missing = sorted(_declared_tables(first.path) - existing)
    if missing:
        raise MigrationError(
            f"库里已有表，但缺少 {first.label} 声明的：{'、'.join(missing)}。"
            "说明这个库不是由当前基线建出来的。"
            f"处理办法：备份后删除 {settings.database_path} 重新启动（会重建演示数据），"
            "或先手工对齐结构再接入迁移链。"
        )
    _record(conn, first, now)
    return first


def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location(f"geo_migration_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise MigrationError(f"无法加载迁移模块：{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(conn: sqlite3.Connection, migration: Migration) -> None:
    if migration.kind == "sql":
        conn.executescript(migration.path.read_text(encoding="utf-8"))
        conn.commit()
        return
    module = _load_module(migration.path)
    apply_fn = getattr(module, "apply", None)
    if not callable(apply_fn):
        raise MigrationError(f"{migration.path.name} 没有实现 apply(conn)")
    apply_fn(conn)
    conn.commit()


def apply(
    conn: sqlite3.Connection,
    *,
    directory: str | Path | None = None,
    now: str | None = None,
) -> list[str]:
    """应用所有未落地的迁移，返回本次新增的版本标签（空列表表示已是最新）。

    老库认领也会出现在返回值里，并带上 ``ADOPT_MARK`` —— 结构确实变了，
    不能一声不响。
    """
    migrations = discover(directory)
    stamp = now or datetime.now().astimezone().isoformat(timespec="seconds")

    ensure_version_table(conn)
    # 先对版本表：如果目录被清空但库里记着有迁移，这个错误比"目录里没文件"有用得多
    _verify_recorded(conn, migrations)
    if not migrations:
        raise MigrationError(f"迁移目录里没有任何迁移文件：{directory or settings.migrations_dir}")

    done = set(applied(conn))
    newly: list[str] = []
    if not done:
        adopted = _adopt_legacy(conn, migrations, stamp)
        if adopted is not None:
            done.add(adopted.version)
            newly.append(adopted.label + ADOPT_MARK)

    for migration in migrations:
        if migration.version in done:
            continue
        _run(conn, migration)
        _record(conn, migration, stamp)
        newly.append(migration.label)
    return newly


def status(conn: sqlite3.Connection, directory: str | Path | None = None) -> list[dict]:
    """给 CLI 用的迁移状态：每条迁移是否已落地。"""
    migrations = discover(directory)
    done = applied(conn)
    return [
        {
            "version": m.version,
            "label": m.label,
            "applied_at": done[m.version]["applied_at"] if m.version in done else None,
            "pending": m.version not in done,
        }
        for m in migrations
    ]


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="查看/应用数据库迁移")
    parser.add_argument("--apply", action="store_true", help="应用未落地的迁移（默认只查看）")
    parser.add_argument("--db", default=None, help="数据库文件，默认取 GEO_DB_PATH")
    parser.add_argument("--dir", default=None, help="迁移目录，默认 backend/migrations")
    args = parser.parse_args(argv)

    db_path = args.db or settings.database_path
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        if args.apply:
            newly = apply(conn, directory=args.dir)
            if newly:
                print(f"已应用 {len(newly)} 个迁移：" + "、".join(newly))
            else:
                print(f"数据库已是最新：{db_path}")
        else:
            rows = status(conn, args.dir)
            print(f"数据库：{db_path}")
            if not rows:
                print("  （迁移目录里没有迁移文件）")
            for row in rows:
                mark = "待应用" if row["pending"] else f"已应用 {row['applied_at']}"
                print(f"  {row['label']:<24} {mark}")
            if any(row["pending"] for row in rows):
                print("加 --apply 执行。")
    except MigrationError as exc:
        print(f"[x] {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
