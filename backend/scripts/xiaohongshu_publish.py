# -*- coding: utf-8 -*-
"""xiaohongshu发布 CLI:python scripts/xiaohongshu_publish.py login|check|publish draft.json

子命令转发到 geo.publishers.xiaohongshu,保持脚本层薄。
"""
import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

if __name__ == "__main__":
    runpy.run_module("geo.publishers.xiaohongshu", run_name="__main__")
