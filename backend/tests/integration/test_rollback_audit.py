"""导入批次回滚的审计行为。

和 ``test_import.py`` 的 RollbackTest 分工：那边验证「回滚本身对不对」，
这边只验证「回滚有没有留痕、留得对不对」。破坏性操作删了没记账，
比不让删更糟——事后回答不了「谁在什么时候撤掉了多少条」。

四条要性质（其余是这四条的推论）：
1. 真回滚 → 写一条 audit，before 是删前的批次快照，after 是实删计数
2. dry_run / 幂等重跑 → 没删数据，就不该写审计
3. 审计写失败 → 整批回滚（删除与记账同一事务）
4. 跨租户 → 拒绝，且不写审计
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from geo.services import audit_service, import_service
from tests.fixtures import QUESTION_TEXT, TENANT, build_minimal_db

NOW = "2026-11-24T12:00:00+08:00"
OTHER_TENANT = "00000000-0000-0000-0000-0000000000ff"


class RollbackAuditTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.conn = build_minimal_db(self.root / "geo.db")
        self.addCleanup(self.conn.close)

    def import_one(self, name: str = "rich.jsonl") -> str:
        """导一批含 2 提及 / 1 引用的答案，返回 batch_id。"""
        line = json.dumps(
            {
                "question": QUESTION_TEXT,
                "answer": f"带提及和引用的答案 {name}",
                "model": "doubao",
                "collected_at": "2026-11-20 09:00:00",
                "mentions": [
                    {"brand": "辽宁辽征律师事务所", "sentiment": "pos", "position": 2},
                    {"brand": "北京盈科（沈阳）律师事务所", "sentiment": "neg", "position": 3},
                ],
                "citations": [{"url": "https://example-news.com/a/1", "media_type": "news"}],
            },
            ensure_ascii=False,
        )
        path = self.root / name
        path.write_text(line + "\n", encoding="utf-8")
        report = import_service.import_file(
            self.conn,
            path=path,
            tenant_id=TENANT,
            report_dir=self.root / "reports",
            now=NOW,
        )
        self.assertEqual(report.inserted, 1, report.errors)
        return report.batch_id

    def rollback(self, batch_id: str, **kwargs):
        kwargs.setdefault("tenant_id", TENANT)
        kwargs.setdefault("now", NOW)
        return import_service.rollback_batch(self.conn, batch_id, **kwargs)

    def audit_rows(self):
        return audit_service.recent(self.conn, TENANT)

    def counts(self) -> tuple[int, int, int]:
        return tuple(
            self.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("answer", "mention", "citation")
        )

    # --- 1. 真回滚写审计，before/after 都要能回答「撤了多少」 ---
    def test_rollback_writes_audit_with_before_snapshot_and_deleted_counts(self):
        batch_id = self.import_one()
        self.assertEqual(self.counts(), (1, 2, 1))

        result = self.rollback(batch_id)

        self.assertFalse(result["already_rolled_back"])
        rows = self.audit_rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["action"], "import.rollback")
        self.assertEqual(row["object_type"], "import_batch")
        self.assertEqual(row["object_id"], batch_id)
        self.assertEqual(row["created_at"], NOW)
        # CLI 没有登录会话，不能伪造一个 actor
        self.assertIsNone(row["actor_id"])
        # before 是删之前那一行 + 删前计数：日志读者能看到"当时这个批次是什么状态、有多少行"
        self.assertEqual(row["before"]["id"], batch_id)
        self.assertEqual(row["before"]["status"], "completed")
        self.assertEqual(row["before"]["source_name"], "rich.jsonl")
        self.assertEqual(
            row["before"]["deleted_rows"],
            {"answers": 1, "mentions": 2, "citations": 1, "blocks": 0},
        )
        # after 说清改成什么状态、实际删了多少
        self.assertEqual(row["after"]["status"], "rolled_back")
        self.assertEqual(
            row["after"]["deleted_rows"],
            {"citations": 1, "mentions": 2, "blocks": 0, "answers": 1},
        )

    # --- 2. 没删数据就不该留痕 ---
    def test_dry_run_writes_no_audit(self):
        batch_id = self.import_one()

        result = self.rollback(batch_id, dry_run=True)

        self.assertTrue(result["dry_run"])
        self.assertEqual(self.audit_rows(), [])
        self.assertEqual(self.counts(), (1, 2, 1))

    def test_second_rollback_writes_no_second_audit(self):
        batch_id = self.import_one()
        self.rollback(batch_id)

        again = self.rollback(batch_id)

        self.assertTrue(again["already_rolled_back"])
        self.assertEqual(len(self.audit_rows()), 1)

    # --- 3. 事务性：记账失败，删除必须一起失败 ---
    def test_audit_failure_rolls_back_the_delete(self):
        batch_id = self.import_one()

        with patch.object(
            audit_service, "record", side_effect=RuntimeError("审计库炸了")
        ):
            with self.assertRaises(RuntimeError):
                self.rollback(batch_id)

        # 异常抛出时 conn.commit() 还没执行；显式回滚后再查，避免读到事务内的未提交状态
        self.conn.rollback()
        self.assertEqual(self.counts(), (1, 2, 1))
        status = self.conn.execute(
            "SELECT status FROM import_batch WHERE id = ?", (batch_id,)
        ).fetchone()[0]
        self.assertEqual(status, "completed")
        self.assertEqual(self.audit_rows(), [])

    # --- 4. 别人的批次动不了，也不留痕 ---
    def test_wrong_tenant_is_rejected_without_audit(self):
        batch_id = self.import_one()

        with self.assertRaises(import_service.ImportFailure):
            self.rollback(batch_id, tenant_id=OTHER_TENANT)

        self.assertEqual(self.counts(), (1, 2, 1))
        self.assertEqual(self.audit_rows(), [])


if __name__ == "__main__":
    unittest.main()
