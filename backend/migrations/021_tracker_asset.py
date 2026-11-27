"""客户资料归档：SOP 第一步「收集开户资料」的照片/文件落库。

背景：GEO 服务 SOP 要求收客户形象照约 10 张、荣誉/证书/活动合影 30 张+，
完成形式是「相关照片」。但系统此前完全没有文件归档能力——隔壁会话做文本台账
（tracker.settings 的表一/表二/表三），本迁移只补文件这一半，职责不重叠。

设计：
- 新表 tracker_asset，一条记录一个文件；category 按 SOP 的资料分类，便于统计达标；
- 文件落 backend/data/tracker_assets/<uuid>.<ext>，库里只存纯文件名（防穿越）；
- 只存文件名不存绝对路径，部署到服务器后路径逻辑一致（同 019 文章图片的决策）；
- 可重跑：先查表在不在。
"""
from __future__ import annotations

import sqlite3


def apply(conn: sqlite3.Connection) -> None:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tracker_asset'"
    ).fetchone()
    if exists:
        return
    conn.execute(
        """CREATE TABLE tracker_asset (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            tracker_id TEXT NOT NULL REFERENCES tracker(id),
            category TEXT NOT NULL,
            filename TEXT NOT NULL,
            original_name TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        "CREATE INDEX idx_tracker_asset_tracker ON tracker_asset(tracker_id, category)"
    )
    conn.commit()
