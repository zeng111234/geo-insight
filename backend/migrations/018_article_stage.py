"""E3:文章六阶段状态落库（决策 009）。

一行一阶段，记状态与更新时间；刷新从这里恢复，不靠 localStorage——
换电脑、换浏览器都能接着写。

为什么不用 article 上加一个 current_stage 列：
- 一列只记「现在在哪」，记不住每一步什么时候做完；
- 质量审核被打回时要留下状态变化，一列不够。

阶段集合不在数据库做 CHECK：它是产品定义，写在 domain.article_stage，
将来加阶段不用改表。唯一约束只管「一个文章不能有两个 active」。
"""

from __future__ import annotations

import sqlite3


def apply(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS article_stage (
               article_id TEXT NOT NULL REFERENCES article(id) ON DELETE CASCADE,
               stage TEXT NOT NULL,
               status TEXT NOT NULL,
               updated_at TEXT NOT NULL,
               PRIMARY KEY (article_id, stage)
           )"""
    )
    conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS ux_article_stage_active
           ON article_stage(article_id) WHERE status = 'active'"""
    )

    # 历史文章初始化：已发布的六步全 done，其余第一步 active。
    # 用 Python 拼值而不是 SQL CASE 写死阶段名——阶段集合只在 domain 里有一份。
    from geo.domain import article_stage as stages

    now = conn.execute("SELECT COALESCE(MAX(updated_at), '') FROM article").fetchone()[0] or ""
    # 用位置索引而不是 row["id"]：迁移入口的连接不一定设了 row_factory，
    # 不能假设调用方会给 Row 对象。
    for article_id, article_status, updated in conn.execute(
        "SELECT id, status, updated_at FROM article"
    ).fetchall():
        for key, _ in stages.STAGES:
            conn.execute(
                """INSERT OR IGNORE INTO article_stage(article_id, stage, status, updated_at)
                   VALUES (?, ?, ?, ?)""",
                (article_id, key, stages.initial_status(article_status, key), updated or now),
            )
