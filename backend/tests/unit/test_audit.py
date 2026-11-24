"""审计原语：写入、查询、动作白名单、JSON 快照往返。

这一层不碰 HTTP，只保证 audit_log 的读写在语义上是对的：
快照能被原样读回来、actor 能 join 出邮箱、未知动作立刻报错而不是悄悄写进去。
"""

import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from geo.repositories import config_repository as config_repo
from geo.services import audit_service
from tests.fixtures import BRAND, NOW, TENANT, add_plan, build_minimal_db


class AuditRecordTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.conn = build_minimal_db(Path(self.tmp.name) / "audit.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def record(self, **kwargs):
        payload = {
            "tenant_id": TENANT,
            "action": "alias.delete",
            "object_type": "brand_alias",
            "object_id": "a1",
            "now": NOW,
        }
        payload.update(kwargs)
        return audit_service.record(self.conn, **payload)

    def test_before_snapshot_round_trips(self):
        before = {"id": "a1", "alias": "辽征律所", "alias_type": "short", "is_active": 1}
        self.record(before=before, after=None)
        self.conn.commit()

        [item] = audit_service.recent(self.conn, TENANT)
        # 中文不能被转义成 \uXXXX，否则查日志的人得自己解码
        self.assertEqual(item["before"], before)
        self.assertIsNone(item["after"])

    def test_unknown_action_is_rejected_before_insert(self):
        # 拼错动作名应该当场报错，而不是悄悄多出一个没人认得的分类
        with self.assertRaises(ValueError):
            self.record(action="alias.deleted")

        self.assertEqual(audit_service.count(self.conn, TENANT), 0)

    def test_recent_filters_by_action_and_respects_order(self):
        self.record(now="2026-11-24T10:00:00+08:00")
        self.record(action="competitor.delete", object_type="competitor", object_id="cp1",
                    now="2026-11-24T11:00:00+08:00")
        self.conn.commit()

        everything = audit_service.recent(self.conn, TENANT)
        self.assertEqual([i["action"] for i in everything],
                         ["competitor.delete", "alias.delete"])
        only_alias = audit_service.recent(self.conn, TENANT, action="alias.delete")
        self.assertEqual(len(only_alias), 1)
        self.assertEqual(only_alias[0]["object_id"], "a1")

    def test_actor_email_comes_from_join_not_duplicated(self):
        self.conn.execute(
            "INSERT INTO app_user(id, tenant_id, email, display_name, role, created_at)"
            " VALUES ('u1', ?, 'a@b.com', '演示账号', 'admin', ?)",
            (TENANT, NOW),
        )
        self.record(actor_id="u1")
        self.conn.commit()

        [item] = audit_service.recent(self.conn, TENANT)
        self.assertEqual(item["actor_id"], "u1")
        self.assertEqual(item["actor_email"], "a@b.com")

    def test_other_tenant_cannot_see_records(self):
        self.record()
        self.conn.commit()

        self.assertEqual(audit_service.recent(self.conn, "other-tenant"), [])


class RemoveSnapshotTest(unittest.TestCase):
    """删除函数要返回快照，否则调用方写不出 before。"""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.conn = build_minimal_db(Path(self.tmp.name) / "snap.db")
        add_plan(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_remove_alias_returns_full_row(self):
        removed = config_repo.remove_alias(self.conn, BRAND, "a1")

        self.assertEqual(removed["alias"], "辽征律所")
        self.assertEqual(removed["alias_type"], "short")
        self.assertEqual(removed["brand_id"], BRAND)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM brand_alias").fetchone()[0], 0
        )

    def test_remove_alias_of_wrong_brand_returns_none_and_keeps_row(self):
        # 别名属于别的品牌时不能删，返回 None 让接口回 404
        self.assertIsNone(config_repo.remove_alias(self.conn, "other-brand", "a1"))
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM brand_alias").fetchone()[0], 1
        )

    def test_remove_competitor_bumps_set_version(self):
        from tests.fixtures import TRACKER

        removed = config_repo.remove_competitor(self.conn, TRACKER, "cp1")

        self.assertEqual(removed["name"], "北京盈科（沈阳）律师事务所")
        # 竞品集合是版本化快照，删一个必须 bump，否则报告里对不上"哪一版"
        self.assertEqual(removed["set_version_after"], 2)

    def test_commit_false_leaves_transaction_open(self):
        config_repo.remove_alias(self.conn, BRAND, "a1", commit=False)
        # 没提交就回滚，别名应该还在 —— 这是"删除与审计同事务"的基础
        self.conn.rollback()
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM brand_alias").fetchone()[0], 1
        )


if __name__ == "__main__":
    unittest.main()
