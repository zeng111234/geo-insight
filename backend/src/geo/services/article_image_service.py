# -*- coding: utf-8 -*-
"""文章图片的文件存储（决策：图片挂文章、存服务器本地目录）。

为什么存本地目录而不是 DB blob：
- 发布器（抖音/小红书）要的是 draft.images 里的**本地文件路径**，存本地正好直接给；
- backend/data/ 已经是项目的本地文件区（publish_drafts / *.local.json 都在那，已 gitignore），
  沿用同一约定，本地开发和服务端部署的差异只在"文件在哪台机器上"，路径逻辑一致。

安全边界（上传是外部输入，必须守住）：
- 文件名一律服务端生成 uuid，**绝不接受调用方给的路径** → 目录穿越无从谈起；
- 扩展名白名单，且按实际文件头校验（防伪装成 png 的可执行文件）；
- 大小上限，防止上传大文件打爆磁盘。
"""
from __future__ import annotations

import uuid
from pathlib import Path

from geo.settings import BACKEND_DIR

IMAGE_DIR = BACKEND_DIR / "data" / "article_images"
MAX_BYTES = 10 * 1024 * 1024  # 10MB，够手机拍的封面图
ALLOWED = {
    ".png": b"\x89PNG\r\n\x1a\n",
    ".jpg": b"\xff\xd8\xff",
    ".jpeg": b"\xff\xd8\xff",
    ".gif": b"GIF87a",
}
# gif 的另一种文件头
ALLOWED_GIF_89A = b"GIF89a"


class ImageError(ValueError):
    """上传校验失败：消息直接给用户看，要说清原因。"""


def ensure_dir() -> None:
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)


def path_for(name: str) -> Path:
    """把库里存的文件名还原成绝对路径。只接受纯文件名，防穿越。"""
    if not name or Path(name).name != name or name in (".", ".."):
        raise ImageError("非法的图片文件名")
    return IMAGE_DIR / name


def save(data: bytes, filename: str) -> str:
    """保存上传内容，返回存进 article.images 的文件名。"""
    if not data:
        raise ImageError("图片文件为空")
    if len(data) > MAX_BYTES:
        raise ImageError(f"图片超过 {MAX_BYTES // 1024 // 1024}MB 上限")
    suffix = Path(filename or "").suffix.lower()
    magic = ALLOWED.get(suffix)
    if magic is None:
        raise ImageError("只支持 png / jpg / jpeg / gif 图片")
    if not data.startswith(magic) and not (suffix == ".gif" and data.startswith(ALLOWED_GIF_89A)):
        raise ImageError("文件内容不是有效的图片格式")

    ensure_dir()
    name = f"{uuid.uuid4().hex}{suffix}"
    path_for(name).write_bytes(data)
    return name


def delete(name: str) -> None:
    """删除图片文件；文件已不在也算成功（幂等）。"""
    path = path_for(name)
    if path.exists():
        path.unlink()


def absolute_paths(names: list[str]) -> list[str]:
    """给 draft 用：文件名 → 发布器能直接 set_input_files 的绝对路径。

    只返回真实存在的文件，缺失的不塞进 draft——发布器本来也会校验缺失路径，
    但在构造侧先滤掉能让失败原因更早、更清楚。
    """
    result = []
    for name in names:
        try:
            path = path_for(name)
        except ImageError:
            continue
        if path.is_file():
            result.append(str(path))
    return result
