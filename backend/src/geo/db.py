import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

from geo.settings import settings

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.database_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    # sqlite3 的 with 只提交事务，不会关闭连接；这里显式关闭，
    # 否则测试与 --reset 反复调用会累积未关闭的句柄。
    with closing(connect()) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()


def db_session() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()
