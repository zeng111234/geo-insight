"""环境自检：换一台电脑跑之前先执行这个。

    python backend/scripts/doctor.py
    python backend/scripts/doctor.py --port 8124

为什么需要它：这个仓库在两台电脑上轮流跑，而有些依赖是**机器的、不进仓库的**
（Python 版本、装了哪些包、浏览器装在哪、本地账密文件、数据库状态）。
这些东西出问题时，症状往往是「页面打不开」或「发布按钮没反应」，
从报错看不出是哪一层。自检脚本把「换机器后需要确认什么」变成一次可执行的检查，
而不是靠记忆逐条想。

只读检查，不改任何文件。退出码：全部必需项通过 = 0，否则 = 1。
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import socket
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "src"))

OK, WARN, FAIL = "OK", "WARN", "FAIL"
_rows: list[tuple[str, str, str]] = []


def record(level: str, item: str, detail: str) -> None:
    _rows.append((level, item, detail))


def check_python() -> None:
    v = sys.version_info
    if v >= (3, 10):
        record(OK, "Python 版本", f"{v.major}.{v.minor}.{v.micro}")
    else:
        # 代码里用了 `str | None` 这类 3.10 语法，3.9 会在导入时就 SyntaxError
        record(FAIL, "Python 版本", f"{v.major}.{v.minor} 太低，需要 3.10+")


def check_core_deps() -> None:
    for mod, label in (("starlette", "starlette"), ("uvicorn", "uvicorn")):
        spec = importlib.util.find_spec(mod)
        if spec is None:
            record(FAIL, f"依赖 {label}", "未安装：pip install -r backend/requirements.txt")
        else:
            try:
                m = __import__(mod)
                record(OK, f"依赖 {label}", getattr(m, "__version__", "已安装"))
            except Exception as exc:  # noqa: BLE001
                record(FAIL, f"依赖 {label}", f"导入失败：{exc}")


def check_optional_deps() -> None:
    # 发布通道才需要 playwright；没装不影响看板、监控、报告
    if importlib.util.find_spec("playwright") is None:
        record(WARN, "可选依赖 playwright", "未安装：发布到各平台不可用，其余功能正常")
    else:
        record(OK, "可选依赖 playwright", "已安装")


def check_browser() -> None:
    try:
        from geo.publishers._base import find_chrome
    except Exception as exc:  # noqa: BLE001
        record(WARN, "浏览器探测", f"无法调用探测函数（多半是 playwright 没装）：{exc}")
        return
    chrome = find_chrome()
    if chrome:
        record(OK, "浏览器", chrome)
    else:
        record(WARN, "浏览器", "没找到 Chrome/Edge，发布时会退回 Playwright 自带 chromium")


def check_database() -> None:
    try:
        from geo import migrate
        from geo.settings import settings
    except Exception as exc:  # noqa: BLE001
        record(FAIL, "数据库", f"无法读取配置：{exc}")
        return

    db_path = Path(settings.database_path)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    if not db_path.is_file():
        record(WARN, "数据库", f"{db_path.name} 不存在，首次启动会自动建库并灌演示数据")
        return

    # 曾经的真实事故：库路径跟着 cwd 走，双击 start.bat 写 backend/geo.db、
    # 命令行在项目根启动写 geo/geo.db，长出两个库，数据看起来"丢了"。
    # 现在路径已固定，但这个检查留着——老机器上那份历史分叉不会自己消失。
    stray = ROOT / "backend" / "geo.db"
    if stray.is_file() and stray.resolve() != db_path.resolve():
        # 空文件和有数据的库要分开说。0 字节的 geo.db 是"连上就建文件、从没写过"的
        # 残留（某次在 backend 目录里跑脚本留下的），删了毫发无损；
        # 对它喊"确认哪份是最新"，只会让人以为真有数据在两份之间分叉。
        if stray.stat().st_size == 0:
            record(WARN, "数据库", "backend/geo.db 是个空文件（0 字节），可以直接删掉")
        else:
            record(
                WARN,
                "数据库分叉",
                "backend/geo.db 也有内容：老版本按 cwd 建库留下的。确认哪份是最新后删掉另一份，"
                "否则换启动方式会看到不同数据",
            )
    else:
        record(OK, "数据库位置", str(db_path))

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        applied = conn.execute(
            "SELECT MAX(version) FROM schema_migration"
        ).fetchone()[0] if _has_table(conn, "schema_migration") else None
        answers = conn.execute("SELECT COUNT(*) FROM answer").fetchone()[0] if _has_table(conn, "answer") else 0
        conn.close()
    except sqlite3.Error as exc:
        record(FAIL, "数据库", f"打不开：{exc}")
        return

    pending = [m for m in migrate.discover() if applied is None or m.version > applied]
    if pending:
        record(WARN, "数据库", f"{db_path.name} 在 v{applied}，有 {len(pending)} 个迁移待应用（启动时会自动跑）")
    else:
        record(OK, "数据库", f"{db_path.name} · 迁移 v{applied} · {answers} 条答案")


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def check_local_files() -> None:
    # 这两个是机器本地的、被 gitignore 的：换机器后必须重新配，代码里替不了
    channels = ROOT / "backend" / "data" / "channels.local.json"
    if channels.is_file():
        record(OK, "渠道账密", "已配置（backend/data/channels.local.json）")
    else:
        record(WARN, "渠道账密", "未配置；发布时会在浏览器里转人工扫码/输密码，不影响其他功能")

    profiles = ROOT / "backend" / "src" / ".browser-profiles"
    if profiles.is_dir() and any(profiles.iterdir()):
        names = sorted(p.name for p in profiles.iterdir() if p.is_dir())
        record(OK, "渠道登录态", f"已保存：{', '.join(names)}")
    else:
        record(WARN, "渠道登录态", "这台机器还没有登录态；首次发布需要扫码登录一次")


def check_collect_keys() -> None:
    """平台 key 有没有配。

    只报「有没有、从哪来」，**绝不打印 key 本身或它的片段**——
    这个输出会被粘进聊天、截图、issue 里，一个字母都不该露。

    没 key 是正常的：不采数据时系统其余部分都能用（导入、指标、看板）。
    所以这里是 WARN 不是 FAIL。
    """
    try:
        from geo.collectors import keys
    except Exception as exc:  # noqa: BLE001 - 导入失败不该让整个自检挂掉
        record(WARN, "采集 key", f"无法读取采集配置：{exc}")
        return

    labels = {"deepseek": "DeepSeek", "doubao": "豆包/火山方舟"}
    found = []
    missing = []
    for provider, label in labels.items():
        source = keys.source_of(provider)
        if source == "missing":
            missing.append(label)
        else:
            found.append(f"{label}（{ '环境变量' if source == 'env' else 'keys.local.json' }）")

    if found:
        record(OK, "采集 key", "已配置：" + "、".join(found))
    if missing:
        record(
            WARN,
            "采集 key",
            "未配置：" + "、".join(missing)
            + "。真实采集需要它；只做导入/看板时可以不管"
            + f"（位置：{keys.keys_file()}）",
        )


def check_port(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        busy = sock.connect_ex(("127.0.0.1", port)) == 0
    if busy:
        record(WARN, f"端口 {port}", "已被占用（可能服务已经在跑了；换端口用 GEO_PORT=xxxx）")
    else:
        record(OK, f"端口 {port}", "空闲")


def check_writable() -> None:
    probe = ROOT / ".doctor-probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        record(OK, "目录可写", str(ROOT))
    except OSError as exc:
        record(FAIL, "目录可写", f"写不进去：{exc}（导入、建库、备份都会失败）")


def main() -> int:
    parser = argparse.ArgumentParser(description="换机器后先跑一遍环境自检")
    parser.add_argument("--port", type=int, default=int(os.getenv("GEO_PORT", "8123")))
    args = parser.parse_args()

    check_python()
    check_core_deps()
    check_optional_deps()
    check_browser()
    check_database()
    check_collect_keys()
    check_local_files()
    check_writable()
    check_port(args.port)

    width = max(len(item) for _l, item, _d in _rows)
    for level, item, detail in _rows:
        mark = {OK: "[ OK ]", WARN: "[WARN]", FAIL: "[FAIL]"}[level]
        print(f"{mark} {item.ljust(width)}  {detail}")

    fails = sum(1 for level, _i, _d in _rows if level == FAIL)
    warns = sum(1 for level, _i, _d in _rows if level == WARN)
    print()
    if fails:
        print(f"{fails} 项必须解决，{warns} 项提醒。按上面的提示处理后再启动。")
        return 1
    print(f"必需项全部通过{f'，{warns} 项提醒（不影响基本使用）' if warns else ''}。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
