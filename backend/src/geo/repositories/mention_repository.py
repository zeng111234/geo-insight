"""提及重跑的读写层。

与 ``sqlite_repository`` 分开（跟 ``config_repository`` 同一理由）：仪表盘的
读模型保持小，重跑是一次性的管理动作，不混进日常查询。全库只有一处写
``mention`` 的常规入口（导入通道在 ``import_repository``），这里是第二处，
两处的字段口径必须一致——``insert_mention`` 的注释里写了约定。
"""

from __future__ import annotations

import sqlite3
import uuid


def uid() -> str:
    return str(uuid.uuid4())


def answer_texts(conn: sqlite3.Connection, tracker_id: str, tenant_id: str) -> list[dict]:
    """重跑要扫的全部有效答案。``is_valid=0`` 的不进任何指标分母，也不该进识别。"""
    rows = conn.execute(
        """SELECT id, text FROM answer
           WHERE tracker_id = ? AND tenant_id = ? AND is_valid = 1
           ORDER BY collected_at""",
        (tracker_id, tenant_id),
    ).fetchall()
    return [dict(r) for r in rows]


def own_mention_count(conn: sqlite3.Connection, tracker_id: str, brand_id: str) -> int:
    """该品牌在该监控对象下的自品牌提及数。删前快照，进审计的 before。"""
    row = conn.execute(
        """SELECT COUNT(*) FROM mention
           WHERE brand_id = ? AND subject_type = 'own'
             AND answer_id IN (SELECT id FROM answer WHERE tracker_id = ?)""",
        (brand_id, tracker_id),
    ).fetchone()
    return int(row[0])


def delete_own_mentions(conn: sqlite3.Connection, tracker_id: str, brand_id: str) -> int:
    """清空自品牌提及。竞品提及（subject_type='competitor'）不动——v1 的识别
    只覆盖自品牌，把识别管不到的行删了，竞品口径就没地方补回来了。"""
    cur = conn.execute(
        """DELETE FROM mention
           WHERE brand_id = ? AND subject_type = 'own'
             AND answer_id IN (SELECT id FROM answer WHERE tracker_id = ?)""",
        (brand_id, tracker_id),
    )
    return cur.rowcount


def insert_mention(
    conn: sqlite3.Connection, answer_id: str, brand_id: str, hit
) -> None:
    """按识别命中写一条自品牌提及。

    字段口径与 ``import_repository.insert_mention`` 一致：有位次才写
    ``position_type='list_index'``；``sentiment_confidence`` 是识别器的
    置信度（否定线索 0.6 / 无线索 0.5），与导入通道人工给的分不比大小。
    """
    conn.execute(
        """INSERT INTO mention(id, answer_id, brand_id, subject_type, matched_alias,
                               match_type, position, position_type, sentiment,
                               sentiment_confidence, snippet)
           VALUES (?, ?, ?, 'own', ?, ?, ?, ?, ?, ?, ?)""",
        (
            uid(),
            answer_id,
            brand_id,
            hit.alias,
            hit.match_type,
            hit.position,
            "list_index" if hit.position is not None else None,
            hit.sentiment,
            hit.sentiment_confidence,
            hit.snippet,
        ),
    )
