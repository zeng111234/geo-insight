"""把平台原始响应落盘，供事后复核。

为什么值得单独存一份：采集回来的 ``answer.text`` 是"加工后"的正文，
而「当时平台到底返回了什么」——有没有 http_search 块、引用是几层结构、
usage 里的搜索次数是多少——只有原始 JSON 能回答。事后要解释
「为什么这条答案没提到品牌」，没有原始响应就只能猜。

落盘失败不影响采集：正文已经拿到了，写库是主路径；
这里失败只在 ``result.raw["_raw_save_error"]`` 里留一句，由调用方打印。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from geo.settings import BACKEND_DIR

DEFAULT_TZ = timezone(timedelta(hours=8))


def raw_dir() -> Path:
    """``backend/data/raw``，可用 GEO_RAW_DIR 覆盖（测试用临时目录）。"""
    import os

    override = os.getenv("GEO_RAW_DIR")
    return Path(override) if override else BACKEND_DIR / "data" / "raw"


def save_raw(platform: str, payload: dict, *, stamp: datetime | None = None) -> tuple[str, str]:
    """写一个 JSON 文件，返回 ``(raw_uri, error)``。

    raw_uri 形如 ``collect://raw/2026-11-25/deepseek-143012-4821.json``——
    与 csv_bank 的 ``collect://csv_bank/<文件名>`` 同一种写法，
    看 ``answer.raw_uri`` 一眼能知道数据从哪来。

    目录按自然日分，一天几百个文件也不会挤在一个目录里。
    """
    moment = stamp or datetime.now(DEFAULT_TZ)
    directory = raw_dir() / moment.strftime("%Y-%m-%d")
    name = f"{platform}-{moment.strftime('%H%M%S-%f')}.json"

    try:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        return "", f"原始响应落盘失败：{exc}"

    return f"collect://raw/{moment.strftime('%Y-%m-%d')}/{name}", ""
