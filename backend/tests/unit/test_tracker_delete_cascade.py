"""删除监控对象的级联顺序：用真实库那种"带满外键"的数据守门。

为什么单独放 unit 层、不塞 HTTP 集成测试：那些文件 import app.py，
隔壁正在同时改 app.py 做 P3，中途的语法错误会让本测试连带跑不了。
这里只 import repo 层（config_repository 只依赖 sqlutil），互不干扰。

三个都曾是真 bug，且老 fixture 一个都没造出来：
1. answer.run_item_id → run_item：answer 必须先删（真实库 1510/1510 条带此 ID）；
2. publication → article：publication 无 ON DELETE CASCADE，不先删就删不掉 article；
3. answer_correction → answer：020 新表，删对象后不能留孤儿。

老测试的 answer 全是 run_item_id=NULL、没有文章/发布记录，所以全绿而生产必炸。
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from geo.repositories import config_repository as config_repo
from tests import fixtures
from tests.fixtures import TENANT, TRACKER


class DeleteCascadeWithRealForeignKeysTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "cascade.db"
        self.conn = fixtures.build_minimal_db(self.path)
        self.addCleanup(self.conn.close)
        fixtures.add_plan(self.conn)
        answer_id = fixtures.add_answer(self.conn, "2026-11-11T09:00:00+08:00")

        # 1. answer.run_item_id → run_item
        self.conn.execute(
            """INSERT INTO run(id, tenant_id, tracker_id, trigger, scheduled_for, stats)
               VALUES ('r-linked', ?, ?, 'manual', '2026-11-10T00:00:00+08:00', '{}')""",
            (TENANT, TRACKER),
        )
        self.conn.execute(
            """INSERT INTO run_item(id, run_id, question_id, model_key, channel, sample_idx)
               VALUES ('ri-linked', 'r-linked', ?, 'deepseek', 'api', 1)""",
            (fixtures.QUESTION,),
        )
        self.conn.execute(
            "UPDATE answer SET run_item_id = 'ri-linked' WHERE id = ?", (answer_id,)
        )

        # 2. answer_correction → answer（020 新表）
        self.conn.execute(
            """INSERT INTO answer_correction(answer_id, tenant_id, note, created_at, updated_at)
               VALUES (?, ?, '删对象前的纠正', '2026-11-11T10:00:00+08:00', '2026-11-11T10:00:00+08:00')""",
            (answer_id, TENANT),
        )

        # 3. publication → article（无 ON DELETE CASCADE）+ article_stage
        self.conn.execute(
            """INSERT INTO channel_account(id, tenant_id, platform_key, platform_name, status, login_mode)
               VALUES ('ch-linked', ?, 'zhihu', '知乎', 'connected', 'scan_qr')""",
            (TENANT,),
        )
        self.conn.execute(
            """INSERT INTO article(id, tenant_id, tracker_id, question_id, title, body,
                                   status, version, created_at, updated_at)
               VALUES ('art-linked', ?, ?, ?, '标题', '正文', 'published', 1, ?, ?)""",
            (TENANT, TRACKER, fixtures.QUESTION, fixtures.NOW, fixtures.NOW),
        )
        self.conn.execute(
            """INSERT INTO publication(id, article_id, channel_id, status, created_at)
               VALUES ('pub-linked', 'art-linked', 'ch-linked', 'published', ?)""",
            (fixtures.NOW,),
        )
        for i in range(6):
            self.conn.execute(
                """INSERT INTO article_stage(article_id, stage, status, updated_at)
                   VALUES ('art-linked', ?, 'done', ?)""",
                (f"stage-{i}", fixtures.NOW),
            )
        self.conn.commit()

    def count(self, table: str) -> int:
        return self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_delete_succeeds_and_leaves_no_orphans(self):
        result = config_repo.delete_tracker(self.conn, TRACKER, TENANT)
        self.conn.commit()
        self.assertIsNotNone(result)

        for table in (
            "answer", "run_item", "answer_correction",
            "article", "publication", "article_stage", "article_fact",
        ):
            self.assertEqual(self.count(table), 0, f"{table} 还有残留行")

    def test_deleted_rows_accounts_for_new_child_tables(self):
        # 审计要能回答"删了多少行"，新增的两类子表也要在账上
        result = config_repo.delete_tracker(self.conn, TRACKER, TENANT)
        self.conn.commit()
        self.assertEqual(result["deleted"]["answer_correction"], 1)
        self.assertEqual(result["deleted"]["publication"], 1)
        self.assertEqual(result["deleted"]["article_stage"], 6)


if __name__ == "__main__":
    unittest.main()
