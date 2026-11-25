"""抖音/头条/小红书发布器上线:渠道状态从 not_implemented 翻成 disconnected。

三个渠道的发布器已随决策 011 的第二批交付(`geo.publishers.{douyin,toutiao,
xiaohongshu}` + `scripts/*_publish.py`),「未接入」不再是事实——真实状态是
「未连接」(登录态未知,待检测)。幂等:只改 still not_implemented 的这三行。
"""

from __future__ import annotations

import sqlite3


def apply(conn: sqlite3.Connection) -> None:
    conn.execute(
        "UPDATE channel_account SET status = 'disconnected' "
        "WHERE platform_key IN ('douyin', 'toutiao', 'xiaohongshu') AND status = 'not_implemented'"
    )
    conn.commit()
