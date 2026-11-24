"""密码哈希与内存 session。

这些是安全相关的最小单元，断言要具体到"哪种坏输入必须被拒绝"，
而不是"能登录就行"。
"""

import time
import unittest

from geo.services import auth_service


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


if __name__ == "__main__":
    unittest.main()
