# -*- coding: utf-8 -*-
"""搜狐发布 CLI:python scripts/sohu_publish.py login|check|publish draft.json

子命令转发到 geo.publishers.sohu,保持脚本层薄。
"""
import runpy
import sys
from pathlib import Path

# 从 backend/ 目录直接运行时,geo 包在 src/ 下,不加点会 ModuleNotFoundError
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

if __name__ == "__main__":
    runpy.run_module("geo.publishers.sohu", run_name="__main__")
