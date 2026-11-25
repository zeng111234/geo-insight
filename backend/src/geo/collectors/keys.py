"""API key 的读取：环境变量优先，其次本地文件。

**key 不进 git，也不进日志。** 这个模块只回答「有没有」「从哪来」，
永远不返回可打印的片段——连前四位都不打。日志里出现 key 前缀，
等于把它送到截图、录屏和 issue 里。

两个来源，优先级从高到低：

1. 环境变量（``GEO_DEEPSEEK_API_KEY`` / ``GEO_ARK_API_KEY``）：适合临时跑、CI；
2. ``backend/data/keys.local.json``：适合长期放在本机，格式:

       {"deepseek": "sk-...", "doubao": "..."}

两台电脑轮流跑的约束：这个文件已被 .gitignore 排除，
换机器时手动复制一份，或者只在一台机器上采数据。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from geo.settings import BACKEND_DIR

# provider -> 环境变量名（按优先级）。豆包走火山方舟，两个名字都认，
# 因为官方文档用 ARK_API_KEY，而项目里其他地方叫 doubao，两种都会有人写。
_ENV_NAMES: dict[str, tuple[str, ...]] = {
    "deepseek": ("GEO_DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY"),
    "doubao": ("GEO_ARK_API_KEY", "GEO_DOUBAO_API_KEY", "ARK_API_KEY"),
}

PROVIDERS = tuple(_ENV_NAMES)


def keys_file() -> Path:
    override = os.getenv("GEO_KEYS_FILE")
    if override:
        return Path(override)
    return BACKEND_DIR / "data" / "keys.local.json"


def get_key(provider: str) -> str | None:
    """返回 key 本身。取不到返回 None（不是空串，空串和没配要能区分）。"""
    for name in _ENV_NAMES.get(provider, ()):
        value = (os.getenv(name) or "").strip()
        if value:
            return value

    loaded = _read_file()
    value = str(loaded.get(provider) or "").strip()
    return value or None


def source_of(provider: str) -> str:
    """这个 key 从哪来：``env`` / ``file`` / ``missing``。给 doctor 用。"""
    for name in _ENV_NAMES.get(provider, ()):
        if (os.getenv(name) or "").strip():
            return "env"
    if str(_read_file().get(provider) or "").strip():
        return "file"
    return "missing"


def status() -> dict[str, str]:
    return {provider: source_of(provider) for provider in PROVIDERS}


def _read_file() -> dict:
    """读 keys.local.json。文件不存在、坏了都当「没配」——不抛异常。

    这里不抛是对的：没配 key 是正常状态（离线跑、只做导入），
    不该让 `collectors --list` 因为一个坏 JSON 就整个报错。
    """
    path = keys_file()
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}
