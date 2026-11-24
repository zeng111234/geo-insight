"""渠道假状态数据修正(决策 011 的收尾一步)。

007 只改了结构(CHECK 词表),老库里的假数据原样保留:wechat/zhihu 挂着
「已连接 + scan_qr/oauth + 假账号名」——这正是要清掉的展示态残留。
判断依据:真实连接流程只会写 login_mode='browser'(浏览器登录)或 'api'
(百家号密钥),seed 的假数据用的是 scan_qr/oauth;真实流程也不可能给
没有发布器的渠道写状态。

修正规则(全部幂等,重跑零行受影响):
- connected + scan_qr/oauth → disconnected,清账号名/登录方式/检测时间/附注
- baijiahao → needs_config(密钥未配置)
- xiaohongshu / toutiao / douyin → not_implemented(没有发布器)

新库(seed 已按诚实初始态灌)跑这条迁移是零操作;只有带着老数据的库会真正改行。
"""

from __future__ import annotations

import sqlite3

FAKE_LOGIN_MODES = ("scan_qr", "oauth", "demo")


def apply(conn: sqlite3.Connection) -> None:
    marks = ",".join("?" for _ in FAKE_LOGIN_MODES)
    conn.execute(
        f"""UPDATE channel_account
            SET status = 'disconnected', account_name = NULL, login_mode = NULL,
                last_checked_at = NULL, last_check_note = NULL
            WHERE status = 'connected' AND login_mode IN ({marks})""",
        FAKE_LOGIN_MODES,
    )
    conn.execute(
        "UPDATE channel_account SET status = 'needs_config' WHERE platform_key = 'baijiahao' "
        "AND status IN ('disconnected', 'expired')"
    )
    conn.execute(
        "UPDATE channel_account SET status = 'not_implemented', login_mode = NULL "
        "WHERE platform_key IN ('xiaohongshu', 'toutiao', 'douyin') "
        "AND status IN ('disconnected', 'expired')"
    )
    conn.commit()
