import sqlite3
from collections.abc import Iterator
from contextlib import closing

from geo import migrate
from geo.settings import settings


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.database_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> str | None:
    """把数据库带到最新结构。

    结构由 ``backend/migrations/`` 的迁移链负责（见 geo.migrate），
    这里是唯一的入口：建表、改字段、认领老库都走同一条路径。

    返回值是本次新应用的迁移标签，已经是最新时返回 None ——
    seed 与启动脚本会把"这次动了什么结构"打出来。
    """
    # sqlite3 的 with 只提交事务，不会关闭连接；这里显式关闭，
    # 否则测试与 --reset 反复调用会累积未关闭的句柄。
    with closing(connect()) as conn:
        newly = migrate.apply(conn)
    return "、".join(newly) or None


def db_session() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()
