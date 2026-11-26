"""文章图片：给 article 加 images 列（决策：图片挂文章、存本地目录）。

背景：抖音/小红书发布器硬性要求 draft.images（本地图片路径列表），
没有图直接 rejected。但文章表和 content studio 之前完全没有图片概念，
这两个渠道从系统里发出去必然失败。本迁移让文章能带一组图片。

存的是**文件名列表的 JSON**（不是绝对路径、不是 blob）：
- 文件落在 backend/data/article_images/<文件名>，该目录已 gitignore；
- 绝对路径在构造 draft 时按部署位置拼出来——本地开发和服务端都能用同一份数据；
- 只存文件名还能防目录穿越：API 侧校验名字必须是纯文件名。

默认 '[]' 让存量文章平滑升级，不需要回填。可重跑：先查列在不在。
"""
from __future__ import annotations

import sqlite3


def apply(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(article)").fetchall()}
    if "images" not in columns:
        conn.execute(
            "ALTER TABLE article ADD COLUMN images TEXT NOT NULL DEFAULT '[]'"
        )
        conn.commit()
