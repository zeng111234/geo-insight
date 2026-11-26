"""部署首启演练：在一台"干净机器"上从零起服务，验证全链路能走通。

为什么需要它：所有单测/集成测试都在 **已建好的库** 上跑（fixtures.build_minimal_db），
迁移测试也只验"能建表"。但服务器首次落地走的是另一条路：

    空库 → 迁移链(001..019) → seed 灌演示数据 → uvicorn 起服务 → 页面/接口 200

这条路从来没被端到端跑过。中间任何一步断了（迁移 checksum 对不上、seed 依赖某个新表、
页面引用了 seed 没造的数据），都要等真上服务器才暴露——而那时已经没有"干净环境"可复现了。

本脚本用子进程跑**真实的 run.py**（不是复刻它的逻辑），把 GEO_DB_PATH 指到临时空库、
端口随机选，避免碰演示库、避免和正在跑的 8123 冲突。结束自动杀进程、删临时目录。

    python backend/scripts/deploy_rehearse.py

退出码：0 全通过；1 某步失败（打印出卡在哪一步及服务日志尾部）；2 参数/环境问题。
"""

from __future__ import annotations

import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND / "src"))

# 首启后必须能打开的页面（对应侧栏 5 组 12 项 + 首页）。
# 带参数的页面（报告详情/答案详情）靠列表接口拿到 id 再验，见下方。
PAGES = [
    "/", "/monitor", "/insight", "/opportunities", "/tasks",
    "/studio", "/publish", "/reports", "/pilot", "/assets",
    "/sources", "/settings", "/update",
]
# 首启必须返回 200 的关键接口（页面 JS 首屏就要打这些）
ENDPOINTS = ["/health", "/v1/meta/methodology", "/v1/meta/channels", "/v1/trackers"]


class RehearsalError(Exception):
    pass


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _get(url: str, timeout: float = 3.0) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _wait_ready(base: str, proc: subprocess.Popen, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RehearsalError(f"服务进程提前退出（code={proc.returncode}）")
        try:
            code, _ = _get(base + "/health", timeout=1)
            if code == 200:
                return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.3)
    raise RehearsalError(f"{timeout:.0f} 秒内 /health 没就绪")


def check_empty_db_bootstrap(db_path: Path) -> dict:
    """子进程跑真实 run.py，验证空库被自动建表 + seed。"""
    # 前置确认：确实从空库开始，否则这个演练没有意义
    if db_path.exists():
        raise RehearsalError(f"演练库必须一开始不存在：{db_path}")

    env = dict(os.environ)
    env.update({
        "GEO_DB_PATH": str(db_path),
        "GEO_PORT": str(_free_port()),
        "GEO_HOST": "127.0.0.1",
        # 页面要能直接打开；鉴权链路有单独的测试，这里只验首启能不能起
        "GEO_AUTH_REQUIRED": "0",
        # 演练不触发真实发布/采集，关掉 worker 免得子进程乱起浏览器
        "GEO_PUBLISH_WORKER": "0",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    port = env["GEO_PORT"]
    base = f"http://127.0.0.1:{port}"
    log_path = db_path.parent / "service.log"

    with log_path.open("wb") as log:
        proc = subprocess.Popen(
            [sys.executable, str(BACKEND / "run.py"), "--no-browser"],
            cwd=str(BACKEND), env=env, stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            print(f"  起服务 port={port} db={db_path.name}")
            _wait_ready(base, proc)
            print("  ✓ /health 就绪")

            for path in PAGES:
                code, body = _get(base + path)
                if code != 200:
                    raise RehearsalError(f"页面 {path} 返回 {code}（期望 200）")
                if b"<html" not in body.lower():
                    raise RehearsalError(f"页面 {path} 不是 HTML")
            print(f"  ✓ {len(PAGES)} 个页面全部 200")

            for path in ENDPOINTS:
                code, _ = _get(base + path)
                if code != 200:
                    raise RehearsalError(f"接口 {path} 返回 {code}（期望 200）")
            print(f"  ✓ {len(ENDPOINTS)} 个首屏接口全部 200")

        finally:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

    # 起服务失败时日志尾部是唯一线索，读出来随异常一起报
    tail = ""
    if log_path.is_file():
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        tail = "\n".join(lines[-25:])
    if not db_path.is_file():
        raise RehearsalError(f"服务起了但空库没被建出来\n--- 服务日志尾部 ---\n{tail}")

    conn = sqlite3.connect(db_path)
    try:
        version = conn.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0]
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        trackers = conn.execute("SELECT COUNT(*) FROM tracker").fetchone()[0]
        answers = conn.execute("SELECT COUNT(*) FROM answer").fetchone()[0]
    finally:
        conn.close()

    for table in ("schema_migration", "tracker", "question", "answer", "mention", "citation"):
        if table not in tables:
            raise RehearsalError(f"空库首启后缺少表 {table}")
    # 版本判据不能写死数字：隔壁加 019、以后加 020 都会让"≥18"变成摆设。
    # 期望值从仓库实际迁移文件算——首启必须跑到当前最高版本，一个不落。
    from geo import migrate

    shipped = migrate.discover()
    expected_version = max(m.version for m in shipped)
    if not version or version != expected_version:
        raise RehearsalError(
            f"迁移版本 {version}，但仓库有 {len(shipped)} 个迁移、最高 {expected_version}"
            f"——首启必须全部应用"
        )
    print(f"  ✓ 迁移应用到仓库最高版本 v{expected_version}（{len(shipped)} 个文件）")
    if trackers < 1 or answers < 1:
        raise RehearsalError(f"seed 没灌数据：{trackers} 对象 / {answers} 答案")

    print(f"  ✓ 空库 → 迁移 v{version} → {trackers} 对象 / {answers} 答案 / {len(tables)} 张表")
    return {"version": version, "trackers": trackers, "answers": answers, "tables": len(tables)}


def main() -> int:
    print("部署首启演练（临时空库 + 随机端口，不碰真实 geo.db）")
    try:
        with tempfile.TemporaryDirectory(prefix="geo-deploy-") as tmp:
            result = check_empty_db_bootstrap(Path(tmp) / "fresh.db")
    except RehearsalError as exc:
        print(f"[x] {exc}", file=sys.stderr)
        return 1
    print(f"\n✓ 首启全链路通过：空库可起服务，{result['answers']} 条演示答案就绪。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
