# -*- coding: utf-8 -*-
"""客户资料的文件存储（GEO 服务 SOP 第一步「收集开户资料」）。

安全边界沿用 article_image_service 已验证的做法：
- 文件名一律服务端生成 uuid，绝不接受调用方给的路径 → 目录穿越无从谈起；
- 扩展名白名单 + 按文件头校验（防伪装成图片的脚本）；
- 大小上限，防上传大文件打爆磁盘；
- 库里只存纯文件名，路径按部署位置拼——本地/服务器一致。

分类按 SOP「收集开户资料」条款：
  portrait    形象照（约 10 张）
  environment 环境设施实景（约 10 张）
  honor       荣誉墙 / 荣誉证书 / 旌旗
  event       颁奖 / 出访 / 采访 / 节目录制 / 重大活动合影（30 张+）
  other       其它
"""
from __future__ import annotations

import uuid
from pathlib import Path

from geo.settings import BACKEND_DIR

ASSET_DIR = BACKEND_DIR / "data" / "tracker_assets"
MAX_BYTES = 10 * 1024 * 1024  # 10MB，够手机拍的高清照片
ALLOWED = {
    ".png": b"\x89PNG\r\n\x1a\n",
    ".jpg": b"\xff\xd8\xff",
    ".jpeg": b"\xff\xd8\xff",
    ".gif": b"GIF87a",
}
ALLOWED_GIF_89A = b"GIF89a"
VALID_CATEGORIES = frozenset({"portrait", "environment", "honor", "event", "other"})


class AssetError(ValueError):
    """上传校验失败：消息直接给用户看，要说清原因。"""


def ensure_dir() -> None:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)


def path_for(name: str) -> Path:
    """把库里存的文件名还原成绝对路径。只接受纯文件名，防穿越。"""
    if not name or Path(name).name != name or name in (".", ".."):
        raise AssetError("非法的文件名")
    return ASSET_DIR / name


def save(data: bytes, filename: str, category: str) -> tuple[str, int]:
    """保存上传内容，返回 (存库的文件名, 字节数)。"""
    if category not in VALID_CATEGORIES:
        raise AssetError(f"不支持的资料分类：{category}")
    if not data:
        raise AssetError("文件为空")
    if len(data) > MAX_BYTES:
        raise AssetError(f"文件超过 {MAX_BYTES // 1024 // 1024}MB 上限")
    suffix = Path(filename or "").suffix.lower()
    magic = ALLOWED.get(suffix)
    if magic is None:
        raise AssetError("只支持 png / jpg / jpeg / gif 图片")
    if not data.startswith(magic) and not (suffix == ".gif" and data.startswith(ALLOWED_GIF_89A)):
        raise AssetError("文件内容不是有效的图片格式")

    ensure_dir()
    name = f"{uuid.uuid4().hex}{suffix}"
    path_for(name).write_bytes(data)
    return name, len(data)


def delete(name: str) -> None:
    """删除文件；文件已不在也算成功（幂等）。"""
    path = path_for(name)
    if path.exists():
        path.unlink()
