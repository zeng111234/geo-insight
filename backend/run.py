"""本地一键启动：种子数据 + 接口 + 看板，一个进程，不依赖 Docker。

用法：
    python run.py                # 默认 http://127.0.0.1:8123
    python run.py --port 8124    # 换端口
    python run.py --reset        # 重建演示数据
    python run.py --no-browser   # 不自动开浏览器

数据库是当前目录下的 geo.db（SQLite 单文件），删掉即可重置。
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
SRC_DIR = BACKEND_DIR / "src"
sys.path.insert(0, str(SRC_DIR))
# 让 geo.db 始终落在 backend/ 下，不受调用时工作目录影响
os.chdir(BACKEND_DIR)


def port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def answer_count() -> int:
    from geo import db

    conn = db.connect()
    try:
        return int(conn.execute("SELECT COUNT(*) FROM answer").fetchone()[0])
    finally:
        conn.close()


def ensure_data(reset: bool) -> str:
    from geo.seed import seed

    # seed() 自身会清空演示表再写入，所以 --reset 不需要删文件；
    # 删文件在 Windows 上会因另一个进程持有 geo.db 而失败。
    if reset:
        return f"已重建演示数据：{seed()}"

    db_file = Path("geo.db")
    if db_file.exists():
        count = answer_count()
        if count:
            return f"沿用已有数据（{count} 条答案）；要重建请加 --reset"
    return f"首次启动，已写入演示数据：{seed()}"


def open_when_ready(url: str, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url + "/health", timeout=1):
                webbrowser.open(url)
                return
        except (urllib.error.URLError, OSError):
            time.sleep(0.25)


def service_healthy(url: str) -> bool:
    """判断端口上跑的是不是我们自己的服务（而不是别的程序）。"""
    try:
        with urllib.request.urlopen(url + "/health", timeout=1.5) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Run GEO Insight locally without Docker.")
    # 部署沙箱注入 PORT 环境变量并要求绑定 0.0.0.0；本地开发默认 127.0.0.1:8123。
    _sandbox_port = os.getenv("PORT")
    _default_host = "0.0.0.0" if _sandbox_port else "127.0.0.1"
    parser.add_argument("--host", default=os.getenv("GEO_HOST") or os.getenv("HOST", _default_host))
    parser.add_argument("--port", type=int, default=int(_sandbox_port or os.getenv("GEO_PORT", "8123")))
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--reset", action="store_true", help="重建演示数据")
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}"

    if port_in_use(args.host, args.port):
        # 已经在跑就直接开页面，不报错——双击两次不该看到红字。
        if service_healthy(url):
            print(f"服务已经在运行：{url}", flush=True)
            print("已为你打开页面，这个窗口稍后自动关闭。", flush=True)
            print("提示：如果你要自己掌控进程（例如面试演示），先关掉之前那个黑窗口，再双击 start.bat。", flush=True)
            if not args.no_browser:
                webbrowser.open(url)
                time.sleep(2.5)  # 留时间读完上面两行
            return 0
        print(f"[x] 端口 {args.port} 被其他程序占用（不是本服务）。", flush=True)
        print(f"    换一个端口运行：python run.py --port 8124", flush=True)
        return 1

    print(f"[1/2] {ensure_data(args.reset)}", flush=True)
    print(f"[2/2] 看板与接口已就绪： {url}", flush=True)
    print("      按 Ctrl+C 停止；页面无需另起静态服务器。", flush=True)

    if not args.no_browser:
        threading.Thread(target=open_when_ready, args=(url,), daemon=True).start()

    import uvicorn

    uvicorn.run("geo.api.app:app", host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
