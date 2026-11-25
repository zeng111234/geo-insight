"""文章六阶段流水线的读写与推进（E3）。

不自己 commit：调用方可能要在同一事务里写文章正文和阶段状态。
「完成当前阶段」与「设为下一阶段 active」必须在同一事务里，
否则中途失败会留下两个 active 或一个都没有。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from geo.domain import article_stage as stages

# 与 credit_service 相同的本地时区口径：阶段时间给人看，不用 UTC
_TZ = timezone(timedelta(hours=8))


class StageError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _now() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


def ensure_stages(conn: sqlite3.Connection, article_id: str, now: str | None = None) -> None:
    """文章若没有阶段行（迁移之后新建的），补齐六行。幂等。"""
    exists = conn.execute(
        "SELECT 1 FROM article_stage WHERE article_id = ? LIMIT 1", (article_id,)
    ).fetchone()
    if exists:
        return
    stamp = now or _now()
    for key, _ in stages.STAGES:
        conn.execute(
            """INSERT OR IGNORE INTO article_stage(article_id, stage, status, updated_at)
               VALUES (?, ?, ?, ?)""",
            (article_id, key, stages.initial_status("draft", key), stamp),
        )


def get_stages(conn: sqlite3.Connection, article_id: str) -> dict:
    """返回六阶段状态；缺行时补齐（不改数据库，只是读时兜住）。"""
    rows = {
        r["stage"]: dict(r)
        for r in conn.execute(
            "SELECT stage, status, updated_at FROM article_stage WHERE article_id = ?",
            (article_id,),
        ).fetchall()
    }
    stamp = _now()
    items = []
    for key, label in stages.STAGES:
        row = rows.get(key)
        items.append(
            {
                "key": key,
                "label": label,
                "status": row["status"] if row and row["status"] in stages.VALID_STATUSES else stages.PENDING,
                "updated_at": row["updated_at"] if row else None,
            }
        )
    current = next((x["key"] for x in items if x["status"] == stages.ACTIVE), None)
    return {
        "items": items,
        "current": current,
        "completed": sum(1 for x in items if x["status"] == stages.DONE),
        "total": len(items),
    }


def set_current(conn: sqlite3.Connection, article_id: str, stage_key: str) -> dict:
    """把某阶段设为当前（active），其余阶段不改动 done 状态。

    只允许把「未 done」的阶段设为 active：已经做完的阶段要回退，得显式重开，
    不能靠点一下步骤条就把历史状态抹掉。往回跳到 pending 阶段是允许的
    （审核打回时确实要回头），但目标阶段若是 done 就拒绝。
    """
    if not stages.is_known_stage(stage_key):
        raise StageError("UNKNOWN_STAGE", f"未知阶段：{stage_key}")
    ensure_stages(conn, article_id)
    rows = {
        r["stage"]: r["status"]
        for r in conn.execute(
            "SELECT stage, status FROM article_stage WHERE article_id = ?", (article_id,)
        ).fetchall()
    }
    if rows.get(stage_key) == stages.DONE:
        raise StageError("STAGE_DONE", "这个阶段已完成，不能退回")
    if rows.get(stage_key) == stages.ACTIVE:
        return get_stages(conn, article_id)

    # 先把旧的 active 降回 pending。不能降级为 done——没完成就别标完成。
    conn.execute(
        "UPDATE article_stage SET status = ?, updated_at = ? "
        "WHERE article_id = ? AND status = ?",
        (stages.PENDING, _now(), article_id, stages.ACTIVE),
    )
    conn.execute(
        "UPDATE article_stage SET status = ?, updated_at = ? "
        "WHERE article_id = ? AND stage = ?",
        (stages.ACTIVE, _now(), article_id, stage_key),
    )
    return get_stages(conn, article_id)


def complete_current(conn: sqlite3.Connection, article_id: str) -> dict:
    """完成当前阶段，并推进到下一阶段。最后一阶段完成后没有 current。"""
    # 自建阶段行：历史文章或绕过 create_article 入口进来的文章都可能没有，
    # 读到空状态就报错会让流水线在最常见的情况下卡死。
    ensure_stages(conn, article_id)
    current = get_stages(conn, article_id)["current"]
    if current is None:
        raise StageError("NO_CURRENT", "没有进行中的阶段")
    nxt = stages.next_stage(current)
    stamp = _now()
    conn.execute(
        "UPDATE article_stage SET status = ?, updated_at = ? "
        "WHERE article_id = ? AND stage = ?",
        (stages.DONE, stamp, article_id, current),
    )
    if nxt:
        # 下一阶段若是 done（审核打回后重走），重置为 active——否则没有
        # 进行中的阶段，流水线会卡死。updated_at 记下这次重走的时间，
        # 「曾经做完」的事实仍在流水线的推进记录里，不会凭空消失。
        conn.execute(
            "UPDATE article_stage SET status = ?, updated_at = ? "
            "WHERE article_id = ? AND stage = ?",
            (stages.ACTIVE, stamp, article_id, nxt),
        )
    return get_stages(conn, article_id)
