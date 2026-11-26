"""P2：回答的人工纠正记录（独立表，不改 mention）。

为什么不直接改 mention 表：
- 提及率、情感、位次这些黄金口径已被 19 项手算值锁死（11-01..11-03 的 22.22% 等）。
  人工纠正一旦写回 mention，重算就会改这些数，已验收的基准全部作废。
-  roadmap 的口径是「人工标注作为唯一事实源，识别只做预填」——纠正记录本身就是事实源，
  独立存才能既保留自动识别的可复现性，又能让人工判定有处可查、可回显。
- 将来要让纠正参与指标时，是一条明确的口径变更（改查询 + 改基准），不是静默改写数据。

一条回答最多一条纠正记录（answer_id UNIQUE）：纠正的是"这条回答的人工结论"，
不是多条评论。再纠正一次是 upsert 覆盖，旧值进审计（before_json）。
"""

from __future__ import annotations

import sqlite3


def apply(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS answer_correction (
               answer_id TEXT PRIMARY KEY REFERENCES answer(id) ON DELETE CASCADE,
               tenant_id TEXT NOT NULL,
               -- 人工判定本品牌有没有被提及：1 有 / 0 无 / NULL 未判定。
               -- 只记录判定，不写回 mention 表——写回会改已锁死的指标基准。
               own_mentioned INTEGER CHECK (own_mentioned IN (0, 1)),
               sentiment TEXT CHECK (sentiment IN ('pos', 'neu', 'neg')),
               position INTEGER CHECK (position IS NULL OR position >= 1),
               note TEXT NOT NULL DEFAULT '',
               corrected_by TEXT NOT NULL DEFAULT '',
               created_at TEXT NOT NULL,
               updated_at TEXT NOT NULL
           )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_answer_correction_tenant
           ON answer_correction(tenant_id, updated_at DESC)"""
    )
