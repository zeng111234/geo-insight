"""更新应用器：服务停掉后，把暂存区的新代码换进仓库根，再重启服务。

由 update_service.apply_update 以分离进程派生（跑的是 data/updates/ 下的
worker 副本，不是仓库原件——交换过程会把仓库原件挪走）。全程只依赖
标准库，因为被更新的是它自己所在的环境。

三步，顺序不能乱：

1. **停服务**（按 PID 杀进程树）。服务不先停，Windows 上换不动正在
   被占用的文件；杀的是 PID 不是端口，端口可能被开发版占着。
2. **交换**。当前代码整体挪进 ``.update-old/``（失败时的手工恢复源），
   暂存区的新代码挪进来。**保留清单**（geo.db、data/、.git 等）原地
   不动——这是数据与代码分离的另一半，见 update_service 的模块注释。
3. **重启**。优先 start-team.bat（团队机器的正式入口，自带鉴权与绑
   0.0.0.0）；没有它就退回 run.py（开发机场景）。

日志写 ``backend/data/update.log``，出问题先看它。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

# 交换时原地保留的顶层项：数据、本地配置、开发态目录、交换工作目录本身。
# 注意 backend/data 整个保留——updates/ 暂存区就在里面，交换期间它必须是稳定的。
PRESERVED_TOP = {
    ".git",
    ".zcode",
    ".workbuddy",
    ".reasonix",
    ".claude",
    ".specify",
    ".update-old",
    ".update-tmp",
    "dist",
}
# backend 内部要保留的项（backend 是顶层目录，单独走 child 级交换）。
PRESERVED_BACKEND = {
    "geo.db",
    "data",
    "backups",
    "import_reports",
}


def _log(root: Path, message: str) -> None:
    log_dir = root / "backend" / "data"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(log_dir / "update.log", "a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] {message}\n")
    except OSError:
        # 日志都写不了（比如磁盘满）—— stderr 兜底，流程继续
        print(f"[update] {message}", file=sys.stderr, flush=True)


def kill_server(pid: int) -> None:
    """杀掉旧服务进程树。Windows 用 taskkill /T（连带 uvicorn 的子进程）。"""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
    else:
        import signal

        try:
            import os

            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass


def swap_tree(root: Path, staging: Path) -> None:
    """暂存区新树换进根目录，保留清单原地不动。

    动的是「目录项移动」（同盘 rename），不是逐文件复制——快，且中途
    失败时旧代码都还在 ``.update-old/`` 里，手工挪回去即可恢复。
    """
    old = root / ".update-old"
    if old.exists():
        shutil.rmtree(old)
    (old / "backend").mkdir(parents=True)

    # 1) 旧代码挪进 .update-old/（保留清单跳过）
    for item in root.iterdir():
        if item.name in PRESERVED_TOP or item == old:
            continue
        if item.name == "backend":
            for child in item.iterdir():
                if child.name in PRESERVED_BACKEND:
                    continue
                shutil.move(str(child), str(old / "backend" / child.name))
        else:
            shutil.move(str(item), str(old / item.name))

    # 2) 新代码从暂存区挪进来。清单项若在包里也出现（不该出现，打包端
    #    已排除），以本地保留的为准——数据永远赢代码。
    (root / "backend").mkdir(parents=True, exist_ok=True)
    for item in staging.iterdir():
        if item.name in PRESERVED_TOP:
            continue
        if item.name == "backend":
            for child in item.iterdir():
                if child.name in PRESERVED_BACKEND:
                    continue
                target = root / "backend" / child.name
                if target.exists():
                    shutil.rmtree(target, ignore_errors=True)
                shutil.move(str(child), str(target))
            # 搬空了的容器壳顺手清掉，暂存区还原成空目录
            if not any(item.iterdir()):
                item.rmdir()
        else:
            target = root / item.name
            if target.exists():
                continue  # 保留项同名（如 dist），本地为准
            shutil.move(str(item), str(target))

    # 3) 成功才清旧代码；失败走异常路径，.update-old 留给人工恢复
    shutil.rmtree(old, ignore_errors=True)


def restart_server(root: Path) -> None:
    """重启：团队机器用 start-team.bat，开发机退回 run.py。"""
    flags = 0
    if sys.platform == "win32":
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    team_bat = root / "start-team.bat"
    if team_bat.exists():
        subprocess.Popen(["cmd", "/c", str(team_bat)], creationflags=flags, close_fds=True)
    else:
        subprocess.Popen(
            [sys.executable, str(root / "backend" / "run.py")],
            creationflags=flags,
            close_fds=True,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="应用已暂存的更新包")
    parser.add_argument("--root", required=True, help="仓库根目录")
    parser.add_argument("--staging", required=True, help="暂存区（新代码树）")
    parser.add_argument("--pid", type=int, default=0, help="要停掉的服务进程")
    parser.add_argument("--wait", type=float, default=2.5, help="杀进程前给响应落地的宽限期（秒）")
    parser.add_argument("--no-kill", action="store_true", help="测试用：不停服务")
    parser.add_argument("--no-restart", action="store_true", help="测试用：不重启")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    staging = Path(args.staging).resolve()
    _log(root, f"开始应用更新：staging={staging}")

    if args.wait > 0:
        time.sleep(args.wait)
    if args.pid and not args.no_kill:
        kill_server(args.pid)
        _log(root, f"已停止服务进程 {args.pid}")

    try:
        swap_tree(root, staging)
        _log(root, "代码交换完成（数据与本地配置未动）")
    except Exception as exc:  # noqa: BLE001 - worker 独立进程，异常必须进日志
        _log(root, f"[x] 交换失败：{exc}。旧代码在 .update-old/，手工挪回可恢复")
        return 1

    if not args.no_restart:
        restart_server(root)
        _log(root, "已派生重启命令")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
