"""服务正在运行的代码版本标识，用来识别「改了代码但服务没重启」。

背景（2026-11-25 实际踩到，当天多次）：两台电脑轮流跑、git pull 之后，
8123 上跑的还是旧进程。改完前端/后端再跑 browsercheck，
/studio 步骤条、/monitor 失败率告警那几条假红——代码是对的，
只是服务没加载新版本。靠人记「改完要重启」不可靠，把版本暴露出来让机器比。

设计：
- 进程启动时读一次 git HEAD（短 hash），之后固定不变——它代表**这个进程加载的代码**。
  每次请求都读 git 是浪费，而且服务运行期间 HEAD 变了也不该改返回值。
- 非 git 检出（用户直接拷目录、没有 .git）返回 "unknown"，不崩、不造假。
  freshcheck 遇到 unknown 会明确说「无法判断，请手动确认」，不会误报通过。
"""

from __future__ import annotations

import subprocess
from functools import lru_cache

from geo.settings import PROJECT_ROOT


@lru_cache(maxsize=1)
def code_rev() -> str:
    """当前检出的短 commit hash；读不到就返回 "unknown"。"""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if out.returncode != 0:
        return "unknown"
    value = out.stdout.strip()
    return value or "unknown"
