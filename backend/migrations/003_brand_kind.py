"""给 brand 增加 kind，明确区分本方品牌与竞品。

为什么要这一列：mention.brand_id 外键指向 brand，导竞品提及时必须给竞品也建
brand 行，否则外键过不去。但建完就与"本方品牌"无法区分了——导入第二批数据时，
按名称查出来的竞品行会被误判成本方，提及率跟着算高。

这不是理论问题：不建这一列，第二次导入同一份竞品提及就会把它记成 own。
"""

from __future__ import annotations

import sqlite3


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def apply(conn: sqlite3.Connection) -> None:
    if "kind" not in _columns(conn, "brand"):
        # ALTER 加不了 CHECK 约束，取值由应用层保证（只写 own / competitor）
        conn.execute("ALTER TABLE brand ADD COLUMN kind TEXT NOT NULL DEFAULT 'own'")

    # 回填：历史上已经因为导入而建出来的竞品行，按竞品清单认回来
    conn.execute(
        """
        UPDATE brand
        SET kind = 'competitor'
        WHERE kind = 'own'
          AND name IN (
              SELECT c.name FROM competitor c
              JOIN competitor_set cs ON cs.id = c.competitor_set_id
              WHERE cs.tenant_id = brand.tenant_id
          )
        """
    )

    # 已经写错的提及一并纠正：这些行当初因为 kind 缺失被记成了本方
    conn.execute(
        """
        UPDATE mention
        SET subject_type = 'competitor'
        WHERE subject_type = 'own'
          AND brand_id IN (SELECT id FROM brand WHERE kind = 'competitor')
        """
    )
