"""导入通道的行为测试。

用独立的最小库，不复用 seed 的演示数据：导入测试要能精确数出"新增了几条"，
而共享库会被其他测试的 seed 覆盖掉。

重点断言的是"会不会写错"，不是"能不能写进去"：
重复导入不翻倍、dry-run 真的没写、异常行不会拖累好行。
"""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from geo.services import import_service
from tests.fixtures import QUESTION_TEXT, TENANT, build_minimal_db

NOW = "2026-11-24T12:00:00+08:00"


class ImportTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.conn = build_minimal_db(self.root / "geo.db")
        self.addCleanup(self.conn.close)

    # --- 工具 ---
    def write(self, name: str, text: str, encoding: str = "utf-8") -> Path:
        path = self.root / name
        path.write_text(text, encoding=encoding)
        return path

    def run_import(self, path, **kwargs):
        kwargs.setdefault("tenant_id", TENANT)
        kwargs.setdefault("report_dir", self.root / "reports")
        kwargs.setdefault("now", NOW)
        return import_service.import_file(self.conn, path=path, **kwargs)

    def scalar(self, sql, params=()):
        return self.conn.execute(sql, params).fetchone()[0]

    def csv_two_rows(self) -> Path:
        return self.write(
            "ok.csv",
            "问题,答案,模型,采集时间,渠道\n"
            f"{QUESTION_TEXT},第一家推荐的答案,kimi,2026/11/20 10:00,接口\n"
            f"{QUESTION_TEXT},第二家提到的答案,deepseek,2026-11-20T10:30:00+08:00,api\n",
        )

    # --- 正常路径 ---
    def test_csv_with_chinese_headers(self):
        report = self.run_import(self.csv_two_rows())

        self.assertEqual(report.status, "completed")
        self.assertEqual((report.total, report.inserted, report.failed), (2, 2, 0))
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM answer"), 2)
        # 中文表头 + 斜杠日期 + 中文渠道都要落到规范值
        row = self.conn.execute(
            "SELECT collected_at, channel FROM answer ORDER BY collected_at LIMIT 1"
        ).fetchone()
        self.assertEqual(row["collected_at"], "2026-11-20T10:00:00+08:00")
        self.assertEqual(row["channel"], "api")

    def test_reimport_is_idempotent(self):
        path = self.csv_two_rows()
        self.run_import(path)
        again = self.run_import(path)

        self.assertEqual((again.inserted, again.skipped), (0, 2))
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM answer"), 2)

    def test_dry_run_writes_nothing(self):
        report = self.run_import(self.csv_two_rows(), dry_run=True)

        self.assertEqual(report.status, "dry_run")
        self.assertEqual(report.inserted, 2)  # 演练报告的是"将会新增几条"
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM answer"), 0)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM import_batch"), 0)

    def test_mentions_and_citations_persist(self):
        path = self.write(
            "rich.jsonl",
            json.dumps(
                {
                    "question": QUESTION_TEXT,
                    "answer": "推荐辽征律所，也提到北京盈科。",
                    "model": "doubao",
                    "collected_at": "2026-11-20 09:00:00",
                    "mentions": [
                        {"brand": "辽征律所", "sentiment": "正面", "position": 2},
                        {"brand": "北京盈科（沈阳）律师事务所", "sentiment": "neg", "position": 3},
                    ],
                    "citations": [
                        {
                            "url": "https://example-news.com/a/1",
                            "media_name": "示例新闻",
                            "media_type": "news",
                            "cite_type": "inline",
                        }
                    ],
                },
                ensure_ascii=False,
            )
            + "\n",
        )
        report = self.run_import(path, create_questions=True)
        self.assertEqual(report.inserted, 1, report.errors)

        mentions = self.conn.execute(
            "SELECT subject_type, sentiment, position, position_type FROM mention ORDER BY position"
        ).fetchall()
        self.assertEqual([m["subject_type"] for m in mentions], ["own", "competitor"])
        self.assertEqual(mentions[0]["sentiment"], "pos")  # 中文情绪归一
        self.assertEqual(mentions[0]["position_type"], "list_index")
        # 竞品提及需要 brand 行满足外键，导入时按需创建
        self.assertEqual(
            self.scalar("SELECT COUNT(*) FROM brand WHERE name = '北京盈科（沈阳）律师事务所'"), 1
        )

        citation = self.conn.execute(
            "SELECT domain, media_type, cite_type, published_at_unknown FROM citation"
        ).fetchone()
        self.assertEqual(citation["domain"], "example-news.com")
        self.assertEqual(citation["published_at_unknown"], 1)

    def test_competitor_mention_stays_competitor_on_reimport(self):
        """回归：竞品为满足外键也有一行 brand，第二批导入不能被认成本方。"""
        line = json.dumps(
            {
                "question": QUESTION_TEXT,
                "answer": "只推荐了北京盈科。",
                "model": "kimi",
                "collected_at": "2026-11-22 09:00:00",
                "mentions": [{"brand": "北京盈科（沈阳）律师事务所", "sentiment": "pos", "position": 1}],
            },
            ensure_ascii=False,
        )
        path = self.write("comp.jsonl", line + "\n")
        first = self.run_import(path)
        self.assertEqual(first.inserted, 1, first.errors)
        # 换个时间再导一条，确保走的是"重新读取主体清单"的路径
        second_path = self.write("comp2.jsonl", line.replace("09:00:00", "10:00:00") + "\n")
        second = self.run_import(second_path)
        self.assertEqual(second.inserted, 1, second.errors)

        types = [
            r["subject_type"]
            for r in self.conn.execute("SELECT subject_type FROM mention ORDER BY rowid")
        ]
        self.assertEqual(types, ["competitor", "competitor"])
        self.assertEqual(
            self.scalar("SELECT COUNT(*) FROM brand WHERE kind = 'competitor'"), 1
        )

    def test_create_questions_toggle(self):
        path = self.write(
            "new-q.csv",
            "question,answer,model,collected_at\n"
            "沈阳哪家律所擅长涉外仲裁,涉外仲裁建议找有国际业务团队的所,doubao,2026-11-20 09:00:00\n",
        )
        blocked = self.run_import(path)
        self.assertEqual(blocked.failed, 1)
        self.assertEqual(blocked.errors[0].code, "UNKNOWN_QUESTION")

        allowed = self.run_import(path, create_questions=True)
        self.assertEqual(allowed.inserted, 1, allowed.errors)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM question"), 2)

    def test_gbk_encoded_file(self):
        path = self.write(
            "gbk.csv",
            "问题,答案,模型,采集时间\n"
            f"{QUESTION_TEXT},GBK 编码也要能读,doubao,2026-11-20 09:00:00\n",
            encoding="gbk",
        )
        report = self.run_import(path)
        self.assertEqual(report.inserted, 1, report.errors)

    # --- 错误处理 ---
    def test_row_errors_do_not_block_good_rows(self):
        path = self.write(
            "bad.csv",
            "问题,答案,模型,采集时间,渠道,提及\n"
            f"{QUESTION_TEXT},答案A,doubao,昨天下午,api,\n"
            f"{QUESTION_TEXT},答案A,doubao,2026-11-21 09:00:00,微信,\n"
            f"{QUESTION_TEXT},,doubao,2026-11-21 09:00:00,api,\n"
            f"{QUESTION_TEXT},答案B,doubao,2026-11-21 09:00:00,api,"
            '"[{""brand"":""不存在的品牌"",""sentiment"":""pos""}]"\n'
            f"{QUESTION_TEXT},答案C,doubao,2026-11-21 09:00:00,api,\n"
            f"{QUESTION_TEXT},答案C,doubao,2026-11-21 09:00:00,api,\n",
        )
        report = self.run_import(path)

        codes = {error.code for error in report.errors}
        self.assertEqual(
            codes,
            {"BAD_TIMESTAMP", "BAD_CHANNEL", "MISSING_FIELD", "UNKNOWN_BRAND", "DUPLICATE_IN_FILE"},
        )
        self.assertEqual((report.inserted, report.failed), (1, 5))
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM answer"), 1)

    def test_abort_mode_writes_nothing(self):
        path = self.write(
            "bad.csv",
            "question,answer,model,collected_at\n"
            f"{QUESTION_TEXT},好行,doubao,2026-11-20 09:00:00\n"
            f"{QUESTION_TEXT},坏行,doubao,昨天下午\n",
        )
        report = self.run_import(path, on_error="abort")

        self.assertEqual(report.status, "aborted")
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM answer"), 0)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM import_batch"), 0)

    def test_bad_jsonl_line_is_reported_but_others_import(self):
        path = self.write(
            "mixed.jsonl",
            json.dumps(
                {
                    "question": QUESTION_TEXT,
                    "answer": "好行",
                    "model": "kimi",
                    "collected_at": "2026-11-20 09:00:00",
                },
                ensure_ascii=False,
            )
            + "\n{ 这不是 json }\n",
        )
        report = self.run_import(path)

        self.assertEqual(report.inserted, 1)
        self.assertEqual([error.code for error in report.errors], ["BAD_JSON_LINE"])

    def test_report_file_is_written(self):
        path = self.write(
            "bad.csv",
            "question,answer,model,collected_at\n" f"{QUESTION_TEXT},坏行,doubao,昨天下午\n",
        )
        report = self.run_import(path)

        self.assertIsNotNone(report.report_path)
        lines = Path(report.report_path).read_text(encoding="utf-8-sig").splitlines()
        self.assertEqual(lines[0].split(",")[:3], ["行号", "字段", "错误码"])
        # 行号是独立一列，方便在 Excel 里排序筛选，不是"第 N 行"这种散文
        self.assertEqual(lines[1].split(",")[:3], ["2", "collected_at", "BAD_TIMESTAMP"])

    def test_question_id_from_other_tracker_is_rejected(self):
        path = self.write(
            "bad-qid.csv",
            "question_id,answer,model,collected_at\n"
            "not-my-question,答案,doubao,2026-11-20 09:00:00\n",
        )
        report = self.run_import(path)
        self.assertEqual([error.code for error in report.errors], ["UNKNOWN_QUESTION"])

    def test_missing_file_and_unknown_headers_raise(self):
        with self.assertRaises(import_service.ImportFailure):
            self.run_import(self.root / "nope.csv")

        path = self.write("unknown.csv", "a,b,c\n1,2,3\n")
        with self.assertRaises(import_service.ImportFailure):
            self.run_import(path)


class RollbackTest(unittest.TestCase):
    """整批撤销。真正要保住的性质是：撤完能重导，且不误伤别人。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.conn = build_minimal_db(self.root / "geo.db")
        self.addCleanup(self.conn.close)

    def write(self, name: str, text: str) -> Path:
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return path

    def run_import(self, path, **kwargs):
        kwargs.setdefault("tenant_id", TENANT)
        kwargs.setdefault("report_dir", self.root / "reports")
        kwargs.setdefault("now", NOW)
        return import_service.import_file(self.conn, path=path, **kwargs)

    def rollback(self, batch_id, **kwargs):
        kwargs.setdefault("tenant_id", TENANT)
        kwargs.setdefault("now", NOW)
        return import_service.rollback_batch(self.conn, batch_id, **kwargs)

    def counts(self) -> tuple[int, int, int]:
        return tuple(
            self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("answer", "mention", "citation")
        )

    def rich_file(self, name: str = "rich.jsonl", time: str = "2026-11-20 09:00:00") -> Path:
        line = json.dumps(
            {
                "question": QUESTION_TEXT,
                "answer": f"带提及和引用的答案 {time}",
                "model": "doubao",
                "collected_at": time,
                "mentions": [
                    {"brand": "辽宁辽征律师事务所", "sentiment": "pos", "position": 2},
                    {"brand": "北京盈科（沈阳）律师事务所", "sentiment": "neg", "position": 3},
                ],
                "citations": [{"url": "https://example-news.com/a/1", "media_type": "news"}],
            },
            ensure_ascii=False,
        )
        return self.write(name, line + "\n")

    def test_rollback_removes_everything_the_batch_wrote(self):
        path = self.rich_file()
        report = self.run_import(path)
        self.assertEqual(self.counts(), (1, 2, 1))

        result = self.rollback(report.batch_id)

        self.assertEqual(result["status"], "rolled_back")
        self.assertEqual((result["answers"], result["mentions"], result["citations"]), (1, 2, 1))
        self.assertEqual(self.counts(), (0, 0, 0))
        status = self.conn.execute(
            "SELECT status FROM import_batch WHERE id = ?", (report.batch_id,)
        ).fetchone()[0]
        self.assertEqual(status, "rolled_back")

    def test_reimport_after_rollback_is_not_a_duplicate(self):
        """撤销的唯一目的就是这个：改完再导一次，不能被判成重复。"""
        path = self.rich_file()
        first = self.run_import(path)
        self.assertEqual(first.inserted, 1)

        self.rollback(first.batch_id)
        second = self.run_import(path)

        self.assertEqual((second.inserted, second.skipped), (1, 0))
        self.assertEqual(self.counts(), (1, 2, 1))

    def test_rollback_twice_is_idempotent(self):
        report = self.run_import(self.rich_file())
        self.rollback(report.batch_id)

        again = self.rollback(report.batch_id)

        self.assertTrue(again["already_rolled_back"])
        self.assertEqual(again["answers"], 0)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_rollback_only_touches_its_own_batch(self):
        batch_a = self.run_import(self.rich_file("a.jsonl", "2026-11-20 09:00:00"))
        batch_b = self.run_import(self.rich_file("b.jsonl", "2026-11-20 10:00:00"))
        self.assertEqual(self.counts(), (2, 4, 2))

        self.rollback(batch_a.batch_id)

        self.assertEqual(self.counts(), (1, 2, 1))
        remaining = self.conn.execute("SELECT import_batch_id FROM answer").fetchone()[0]
        self.assertEqual(remaining, batch_b.batch_id)

    def test_rollback_dry_run_changes_nothing(self):
        report = self.run_import(self.rich_file())

        result = self.rollback(report.batch_id, dry_run=True)

        self.assertTrue(result["dry_run"])
        self.assertEqual((result["answers"], result["mentions"], result["citations"]), (1, 2, 1))
        self.assertEqual(self.counts(), (1, 2, 1))
        status = self.conn.execute(
            "SELECT status FROM import_batch WHERE id = ?", (report.batch_id,)
        ).fetchone()[0]
        self.assertEqual(status, "completed")

    def test_rollback_keeps_subject_registry(self):
        """竞品的 brand 行是主体注册，可能被别的批次引用，不能跟着批次删。"""
        report = self.run_import(self.rich_file())
        brand_count = self.conn.execute("SELECT COUNT(*) FROM brand").fetchone()[0]
        self.assertEqual(brand_count, 2)  # 本方 + 导入时建的竞品

        self.rollback(report.batch_id)

        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM brand").fetchone()[0], 2)

    def test_unknown_batch_and_wrong_tenant_raise(self):
        with self.assertRaises(import_service.ImportFailure):
            self.rollback("no-such-batch")

        report = self.run_import(self.rich_file())
        with self.assertRaises(import_service.ImportFailure):
            self.rollback(report.batch_id, tenant_id="00000000-0000-0000-0000-0000000000ff")


if __name__ == "__main__":
    unittest.main()
