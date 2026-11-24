"""密码哈希与内存 session。

这些是安全相关的最小单元，断言要具体到"哪种坏输入必须被拒绝"，
而不是"能登录就行"。
"""

import time
import unittest
from pathlib import Path

from geo.services import auth_service
from geo.settings import settings


class PasswordTest(unittest.TestCase):
    def test_roundtrip(self):
        stored = auth_service.hash_password("correct horse battery staple")
        self.assertTrue(auth_service.verify_password("correct horse battery staple", stored))

    def test_wrong_password_rejected(self):
        stored = auth_service.hash_password("right")
        self.assertFalse(auth_service.verify_password("wrong", stored))
        self.assertFalse(auth_service.verify_password("", stored))

    def test_same_password_gets_different_salt(self):
        first = auth_service.hash_password("same")
        second = auth_service.hash_password("same")
        self.assertNotEqual(first, second)
        self.assertTrue(auth_service.verify_password("same", first))
        self.assertTrue(auth_service.verify_password("same", second))

    def test_stored_format_is_self_describing(self):
        stored = auth_service.hash_password("x")
        parts = stored.split("$")
        self.assertEqual(len(parts), 6)
        self.assertEqual(parts[0], "scrypt")
        self.assertEqual(int(parts[1]), auth_service.SCRYPT_N)

    def test_malformed_or_missing_hash_is_rejected_not_crashed(self):
        for bad in (None, "", "plaintext", "bcrypt$1$2$3$4$5", "scrypt$bad$$$$"):
            self.assertFalse(auth_service.verify_password("x", bad), bad)

    def test_tampered_digest_is_rejected(self):
        stored = auth_service.hash_password("x")
        head, digest = stored.rsplit("$", 1)
        tampered = head + "$" + ("0" * len(digest))
        self.assertFalse(auth_service.verify_password("x", tampered))

    def test_empty_password_cannot_be_hashed(self):
        with self.assertRaises(ValueError):
            auth_service.hash_password("")


class SessionTest(unittest.TestCase):
    def setUp(self):
        auth_service.clear_sessions()
        self.addCleanup(auth_service.clear_sessions)
        self.user = {
            "id": "u1",
            "email": "a@b.c",
            "role": "admin",
            "tenant_id": "t1",
        }

    def test_create_and_get(self):
        token, ttl = auth_service.create_session(self.user)
        self.assertGreater(ttl, 0)
        session = auth_service.get_session(token)
        self.assertEqual(session["email"], "a@b.c")
        self.assertEqual(session["tenant_id"], "t1")

    def test_unknown_token_returns_none(self):
        self.assertIsNone(auth_service.get_session("nope"))
        self.assertIsNone(auth_service.get_session(None))

    def test_destroy(self):
        token, _ = auth_service.create_session(self.user)
        self.assertTrue(auth_service.destroy_session(token))
        self.assertIsNone(auth_service.get_session(token))
        self.assertFalse(auth_service.destroy_session(token))

    def test_expired_session_is_rejected_and_cleaned(self):
        token, _ = auth_service.create_session(self.user)
        # 直接把过期时间拨到过去：这是唯一需要碰内部状态的用例，
        # 因为 settings 是 frozen dataclass，改不了 session_hours。
        auth_service._SESSIONS[token]["expires_at"] = time.time() - 1

        self.assertIsNone(auth_service.get_session(token))
        self.assertNotIn(token, auth_service._SESSIONS)

    def test_tokens_are_not_guessable(self):
        first, _ = auth_service.create_session(self.user)
        second, _ = auth_service.create_session(self.user)
        self.assertNotEqual(first, second)
        self.assertGreaterEqual(len(first), 32)


class EnsureAdminOnMigratedEmptyDbTest(unittest.TestCase):
    """回归:「迁移过但没 seed」的库上建管理员曾直接 FK 崩。

    pytest 的临时库(tests/__init__.py 把 GEO_DB_PATH 指过去)就是这种状态:
    schema 由迁移链建出来、但没有租户行。ensure_admin_password 插 app_user
    时外键指向 tenant,曾经在这里 IntegrityError,连 create_app/import app
    都起不来,三个集成测试模块在收集阶段一起红。修法是插用户前把默认租户
    自愈出来;租户已存在时必须原样不动(不能重复插)。
    """

    def setUp(self):
        import sqlite3
        import tempfile

        from geo import migrate

        self._tmp = tempfile.TemporaryDirectory()
        conn = sqlite3.connect(Path(self._tmp.name) / "empty.db")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        migrate.apply(conn)
        self.conn = conn

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def test_creates_tenant_and_admin_together(self):
        result = auth_service.ensure_admin_password(self.conn)

        self.assertEqual(result, "created")
        tenant = self.conn.execute(
            "SELECT id, slug FROM tenant WHERE id = ?", (settings.default_tenant_id,)
        ).fetchone()
        self.assertIsNotNone(tenant, "默认租户没有被自愈出来")
        user = self.conn.execute(
            "SELECT tenant_id FROM app_user WHERE email = ?", (settings.admin_email,)
        ).fetchone()
        self.assertIsNotNone(user)
        self.assertEqual(user["tenant_id"], settings.default_tenant_id)

    def test_rerun_neither_duplicates_tenant_nor_touches_password(self):
        first = auth_service.ensure_admin_password(self.conn)
        second = auth_service.ensure_admin_password(self.conn)

        self.assertEqual((first, second), ("created", "unchanged"))
        tenants = self.conn.execute("SELECT COUNT(*) AS n FROM tenant").fetchone()["n"]
        self.assertEqual(tenants, 1)
        users = self.conn.execute("SELECT COUNT(*) AS n FROM app_user").fetchone()["n"]
        self.assertEqual(users, 1)


if __name__ == "__main__":
    unittest.main()
