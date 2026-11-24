"""单用户鉴权：密码哈希 + 进程内 session。

刻意不做的三件事：

- **不引 PyJWT。** 无状态 token 的价值是横向扩展，而这里是单进程、单用户、双击启动。
- **不做 RBAC。** `app_user.role` 已经在表里，但首版只有一个角色用得上；先把"谁能进来"解决。
- **不把 session 存表。** 存在进程内 dict，重启即失效。维护一张 session 表要处理过期清理与并发，
  对一个本地工具不划算。代价是重启后要重新登录一次，README 里写明了。

密码用标准库 `hashlib.scrypt`，不引 bcrypt/argon2：scrypt 是内存硬的，对本场景足够，
且不增加任何依赖。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
import uuid

from geo.settings import settings

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
KEY_LEN = 32

# token -> {user_id, email, role, tenant_id, csrf_token, expires_at}
_SESSIONS: dict[str, dict] = {}
_LOCK = threading.Lock()

# 登录限流状态：标识 -> 最近失败时间戳列表（过期条目惰性清理）
_LOGIN_FAILURES: dict[str, list[float]] = {}
_LOGIN_LOCK = threading.Lock()


# ---------------------------------------------------------------- 密码


def hash_password(password: str) -> str:
    """返回 `scrypt$n$r$p$salt$hash`。参数写进字符串，将来调强度不用迁移。"""
    if not password:
        raise ValueError("密码不能为空")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=KEY_LEN,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    """校验。格式不对、算法不认识、为空，一律返回 False，不抛异常。"""
    if not password or not stored:
        return False
    try:
        algorithm, n, r, p, salt_hex, expected = stored.split("$")
        if algorithm != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(bytes.fromhex(expected)),
        )
    except (ValueError, TypeError):
        return False
    # 定长比较，避免按字节提前返回泄露信息
    return hmac.compare_digest(digest.hex(), expected)


# ---------------------------------------------------------------- 用户


def find_user(conn: sqlite3.Connection, email: str) -> dict | None:
    if not email:
        return None
    row = conn.execute(
        "SELECT * FROM app_user WHERE lower(email) = lower(?) AND status = 'active'",
        (email.strip(),),
    ).fetchone()
    return dict(row) if row else None


def authenticate(conn: sqlite3.Connection, email: str, password: str) -> dict | None:
    user = find_user(conn, email)
    if not user or not verify_password(password, user.get("password_hash")):
        return None
    return user


def ensure_admin_password(
    conn: sqlite3.Connection, *, email: str | None = None, password: str | None = None
) -> str:
    """保证有一个能登录的管理员账号。

    返回 ``created`` / ``password_set`` / ``unchanged``，供启动日志说明做了什么。

    已有密码时**不覆盖**：改环境变量不该悄悄换掉现有密码，否则"为什么登不上"会很难查。
    要换密码就用 ``--reset`` 重建演示数据（seed 会按新密码重新生成账号）。
    """
    target_email = email or settings.admin_email
    target_password = password or settings.admin_password

    user = find_user(conn, target_email)
    if user is None:
        tenant_id = settings.default_tenant_id
        # 空库自愈:app_user.tenant_id 外键指向 tenant,「迁移过但没 seed」的库
        # (pytest 的临时库就是这种状态)里租户行不存在,直接插用户会 FK 崩,
        # 连 create_app 都起不来。租户已存在时这里什么都不做。
        if conn.execute("SELECT 1 FROM tenant WHERE id = ?", (tenant_id,)).fetchone() is None:
            conn.execute(
                """INSERT INTO tenant(id, name, slug, plan, status, created_at)
                   VALUES (?, '默认租户', 'default', 'internal', 'active', ?)""",
                (tenant_id, time.strftime("%Y-%m-%dT%H:%M:%S%z")),
            )
        conn.execute(
            """INSERT INTO app_user(id, tenant_id, email, display_name, role, status, created_at, password_hash)
               VALUES (?, ?, ?, '管理员', 'admin', 'active', ?, ?)""",
            (
                str(uuid.uuid4()),
                tenant_id,
                target_email,
                time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                hash_password(target_password),
            ),
        )
        conn.commit()
        return "created"

    if not user.get("password_hash"):
        conn.execute(
            "UPDATE app_user SET password_hash = ? WHERE id = ?",
            (hash_password(target_password), user["id"]),
        )
        conn.commit()
        return "password_set"

    return "unchanged"


# ---------------------------------------------------------------- session


def _purge(now: float) -> None:
    expired = [token for token, item in _SESSIONS.items() if item["expires_at"] <= now]
    for token in expired:
        _SESSIONS.pop(token, None)


def create_session(user: dict) -> tuple[str, int]:
    """返回 (token, 有效期秒数)。session 里带 csrf_token(写接口校验用)。"""
    token = secrets.token_urlsafe(32)
    ttl = max(60, settings.session_hours * 3600)
    now = time.time()
    with _LOCK:
        _purge(now)
        _SESSIONS[token] = {
            "user_id": user["id"],
            "email": user["email"],
            "role": user.get("role"),
            "tenant_id": user.get("tenant_id"),
            "csrf_token": secrets.token_urlsafe(32),
            "expires_at": now + ttl,
        }
    return token, ttl


def get_session(token: str | None) -> dict | None:
    if not token:
        return None
    now = time.time()
    with _LOCK:
        item = _SESSIONS.get(token)
        if not item:
            return None
        if item["expires_at"] <= now:
            _SESSIONS.pop(token, None)
            return None
        return dict(item)


def destroy_session(token: str | None) -> bool:
    if not token:
        return False
    with _LOCK:
        return _SESSIONS.pop(token, None) is not None


def clear_sessions() -> None:
    """测试用：清空内存会话。"""
    with _LOCK:
        _SESSIONS.clear()


# ---------------------------------------------------------------- 登录限流（FR-006）


def login_throttled(identifier: str) -> int:
    """返回该标识还需冷却的秒数（>0 = 已锁定），0 = 放行。

    标识是「账号 + 客户端 IP」的组合：单拦账号会被任意 IP 撞锁（DoS 别人），
    单拦 IP 又换账号就绕过。内存态、重启清零——防的是暴力尝试，不是持久封锁。
    """
    now = time.time()
    with _LOGIN_LOCK:
        fails = [t for t in _LOGIN_FAILURES.get(identifier, []) if now - t < settings.login_lockout_s]
        if len(fails) >= settings.login_max_failures:
            return int(settings.login_lockout_s - (now - fails[0])) + 1
    return 0


def record_login_failure(identifier: str) -> None:
    with _LOGIN_LOCK:
        _LOGIN_FAILURES.setdefault(identifier, []).append(time.time())


def clear_login_failures(identifier: str) -> None:
    with _LOGIN_LOCK:
        _LOGIN_FAILURES.pop(identifier, None)


def _reset_login_failures_for_tests() -> None:
    """测试专用：清空限流状态，避免用例间串扰。"""
    with _LOGIN_LOCK:
        _LOGIN_FAILURES.clear()
