"""环境自检：换一台电脑跑之前先执行这个。

    python backend/scripts/doctor.py
    python backend/scripts/doctor.py --port 8124

为什么需要它：这个仓库在两台电脑上轮流跑，而有些依赖是**机器的、不进仓库的**
（Python 版本、装了哪些包、浏览器装在哪、本地账密文件、数据库状态）。
这些东西出问题时，症状往往是「页面打不开」或「发布按钮没反应」，
从报错看不出是哪一层。自检脚本把「换机器后需要确认什么」变成一次可执行的检查，
而不是靠记忆逐条想。

只读检查，不改任何文件。退出码：全部必需项通过 = 0，否则 = 1。

服务器部署前再加一层：`python backend/scripts/doctor.py --server`。
它叠在基础自检之上，查的是"本地能跑、上线才炸"的那几项——
演示默认密码、鉴权被关、无显示器却没开 headless、/dev/shm、磁盘、HTTPS 反代。
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
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


def stray_verdict(db_path: Path, stray: Path) -> tuple[str, str, str]:
    """判断历史分叉库该怎么报。纯函数，便于测试——这是决策 013 的守门逻辑。

    三种情况分清楚：
    - 没有分叉文件，或它其实就是当前库（同一路径的不同写法）→ OK
    - 分叉文件是 0 字节（连上没写过留下的空壳）→ WARN，说清可以直接删
    - 分叉文件有内容 → WARN「数据库分叉」，让人确认哪份最新，不能含糊
    """
    if not stray.is_file() or stray.resolve() == db_path.resolve():
        return OK, "数据库位置", str(db_path)
    if stray.stat().st_size == 0:
        return WARN, "数据库", "backend/geo.db 是个空文件（0 字节），可以直接删掉"
    return (
        WARN,
        "数据库分叉",
        "backend/geo.db 也有内容：老版本按 cwd 建库留下的。确认哪份是最新后删掉另一份，"
        "否则换启动方式会看到不同数据",
    )


# ---- 服务器就绪判据（纯函数，便于测试；--server 才跑）----
#
# 这些是"本地能跑、上线才炸"的那类坑：默认密码裸奔、无显示器却开有头浏览器、
# cookie 只在 HTTPS 下才带 Secure/None。写成纯函数是为了让测试能造出
# "Linux 无 DISPLAY" 这种本地再造不出来的场景，而不是等真上了服务器才发现。


def security_verdict(password: str, auth_required: bool, demo_password: str) -> tuple[str, str, str]:
    """鉴权必须开、密码必须不是演示值。两者任一不满足都不能对外。"""
    if not auth_required:
        return (
            FAIL,
            "鉴权",
            "GEO_AUTH_REQUIRED=0 是自动化测试专用；对外服务开着等于任何人都能看全部数据",
        )
    if password == demo_password:
        return (
            FAIL,
            "管理员密码",
            "还在用演示默认密码。必须设 GEO_ADMIN_PASSWORD（决策 008），否则等于没锁门",
        )
    return OK, "鉴权与密码", "鉴权已开，且不是演示默认密码"


def headless_verdict(is_linux: bool, headless: bool, has_display: bool) -> tuple[str, str, str]:
    """服务器无显示器时不设 headless，五个浏览器渠道会在启动那步直接崩。"""
    if is_linux and not headless:
        if not has_display:
            return (
                FAIL,
                "无头发布",
                "Linux 无显示器必须设 GEO_PUBLISH_HEADLESS=1，否则知乎/公众号/抖音等渠道浏览器启动即崩",
            )
        return (
            WARN,
            "无头发布",
            "Linux 有 DISPLAY 但没开 headless；服务器建议统一设 1，别依赖 X11 转发",
        )
    if headless:
        return OK, "无头发布", "已开启（指纹更易被风控识别，见 README 安全取舍）"
    return OK, "无头发布", "本机有头模式，无需改动"


def shm_verdict(total_mb: int) -> tuple[str, str, str]:
    """Docker 默认 /dev/shm 只有 64MB，Chrome 容易崩（-1 表示读不到）。"""
    if total_mb < 0:
        return WARN, "/dev/shm", "读不到大小；非 Linux 可忽略，容器里请确认 --shm-size"
    if total_mb < 64:
        return (
            FAIL,
            "/dev/shm",
            f"只有 {total_mb}MB，Chrome 会崩。加 --shm-size=512m（代码已带 --disable-dev-shm-usage，但别赌）",
        )
    if total_mb < 256:
        return WARN, "/dev/shm", f"只有 {total_mb}MB，发布多并发时可能不足"
    return OK, "/dev/shm", f"{total_mb}MB"


def disk_verdict(free_gb: float, minimum_gb: float = 1.0, comfortable_gb: float = 5.0) -> tuple[str, str, str]:
    """原始响应按天落盘、备份也占空间；给个下限免得跑到一半写不进去。"""
    if free_gb < minimum_gb:
        return FAIL, "磁盘空间", f"只剩 {free_gb:.1f}GB，原始响应与备份都写不下"
    if free_gb < comfortable_gb:
        return WARN, "磁盘空间", f"剩 {free_gb:.1f}GB，建议 ≥ {comfortable_gb:.0f}GB（raw + 备份会持续增长）"
    return OK, "磁盘空间", f"剩 {free_gb:.1f}GB"


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
    level, item, detail = stray_verdict(db_path, stray)
    record(level, item, detail)

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


def check_server_readiness() -> None:
    """服务器专项检查，叠在基础自检之后。只读，不改配置。"""
    from geo.settings import DEMO_ADMIN_PASSWORD, settings

    level, item, detail = security_verdict(
        settings.admin_password, settings.auth_required, DEMO_ADMIN_PASSWORD
    )
    record(level, item, detail)

    is_linux = sys.platform.startswith("linux")
    has_display = bool(os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY"))
    level, item, detail = headless_verdict(is_linux, settings.publish_headless, has_display)
    record(level, item, detail)

    if is_linux:
        try:
            shm_mb = shutil.disk_usage("/dev/shm").total // (1024 * 1024)
        except OSError:
            shm_mb = -1
        level, item, detail = shm_verdict(shm_mb)
        record(level, item, detail)

    try:
        free_gb = shutil.disk_usage(ROOT).free / (1024 ** 3)
        level, item, detail = disk_verdict(free_gb)
    except OSError as exc:
        level, item, detail = WARN, "磁盘空间", f"读不到：{exc}"
    record(level, item, detail)

    # cookie 的 Secure/None 由「请求看起来是不是 HTTPS」决定（app.py 的 _is_https）。
    # 反代不透传 X-Forwarded-Proto 时它是 false，应用卡 iframe 里会被弹回登录页——
    # 这是线上白屏的唯一已知原因，必须专门提示，不能混在"网络配置"里。
    record(
        WARN,
        "HTTPS 反代",
        "对外必须 HTTPS，反代要透传 X-Forwarded-Proto: https；"
        "否则 cookie 不带 Secure/None，应用卡 iframe 登录后白屏（决策 010）",
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
    parser.add_argument(
        "--server", action="store_true",
        help="叠加服务器部署检查（默认密码、鉴权、headless、shm、磁盘、HTTPS 反代）",
    )
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
    if args.server:
        check_server_readiness()

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
