# -*- coding: utf-8 -*-
"""知乎发布 CLI:python scripts/zhihu_publish.py login|check|publish draft.json

子命令转发到 geo.publishers.zhihu,保持脚本层薄。
"""
import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

if __name__ == "__main__":
    runpy.run_module("geo.publishers.zhihu", run_name="__main__")
