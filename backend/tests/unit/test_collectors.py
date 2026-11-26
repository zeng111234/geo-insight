"""采集器与采集服务的行为测试。

断言重点是"取不到的时候有没有说清楚"：静默返回空答案会把脏数据写进库，
比直接报错难查得多。
"""

import json
import tempfile
import unittest
from pathlib import Path

from geo.collectors import registry
from geo.collectors.base import CollectRequest, CollectorError
from geo.collectors.csv_bank import CsvBankCollector
from geo.services import collect_service
from tests.fixtures import QUESTION_TEXT, TENANT, TRACKER, build_minimal_db

NOW = "2026-11-24T12:00:00+08:00"


def preserve_registry(test):
    """谁往全局注册表塞临时采集器,谁负责还原。

    run_once() 会把指向临时目录的实例注册成 csv_bank。临时目录删除后该实例
    available()=False,不还原就会污染后续用 registry 的测试(典型症状:
    test_collect_slot 单独跑全绿、全量跑 10 条红)。注册表是进程级单例,
    所以每个会 register 的测试类都要在 setUp 里调这个。
    """
    original = registry.get("csv_bank")
    test.addCleanup(registry.register, original)


class CsvBankCollectorTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        preserve_registry(self)

    def bank(self, rows: list[dict], name: str = "bank.jsonl") -> CsvBankCollector:
        path = self.root / name
        path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        return CsvBankCollector(path)

    def test_available_reflects_path(self):
        self.assertFalse(CsvBankCollector(self.root / "nope.jsonl").available())
        collector = self.bank([{"question": QUESTION_TEXT, "model": "doubao", "answer": "答案"}])
        self.assertTrue(collector.available())

    def test_supports_only_models_in_file(self):
        collector = self.bank([{"question": QUESTION_TEXT, "model": "doubao", "answer": "答案"}])
        self.assertTrue(collector.supports("doubao"))
        self.assertFalse(collector.supports("kimi"))

    def test_collect_returns_text_and_now(self):
        collector = self.bank([{"question": QUESTION_TEXT, "model": "doubao", "answer": "  正文  "}])
        result = collector.collect(CollectRequest(question_text=QUESTION_TEXT, model_key="doubao"))

        self.assertEqual(result.text, "正文")
        # 采集时间是"这次取样"的时间，必须带时区
        self.assertRegex(result.collected_at, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+08:00$")
        self.assertEqual(result.raw_uri, "collect://csv_bank/bank.jsonl")

    def test_not_found_raises_instead_of_empty_text(self):
        collector = self.bank([{"question": QUESTION_TEXT, "model": "doubao", "answer": "答案"}])
        with self.assertRaises(CollectorError) as ctx:
            collector.collect(CollectRequest(question_text="不存在的问句", model_key="doubao"))
        self.assertEqual(ctx.exception.code, "NOT_FOUND")

    def test_blank_answer_raises(self):
        collector = self.bank([{"question": QUESTION_TEXT, "model": "doubao", "answer": "   "}])
        with self.assertRaises(CollectorError) as ctx:
            collector.collect(CollectRequest(question_text=QUESTION_TEXT, model_key="doubao"))
        self.assertEqual(ctx.exception.code, "EMPTY_TEXT")

    def test_csv_bank_is_supported(self):
        path = self.root / "bank.csv"
        path.write_text(
            "question,model,answer\n" f"{QUESTION_TEXT},kimi,CSV 里的答案\n", encoding="utf-8"
        )
        result = CsvBankCollector(path).collect(
            CollectRequest(question_text=QUESTION_TEXT, model_key="kimi")
        )
        self.assertEqual(result.text, "CSV 里的答案")

    def test_registry_has_offline_collector(self):
        keys = [c.key for c in registry.all_collectors()]
        self.assertIn("csv_bank", keys)
        with self.assertRaises(CollectorError) as ctx:
            registry.get("nope")
        self.assertEqual(ctx.exception.code, "UNKNOWN_COLLECTOR")


class CollectServiceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        preserve_registry(self)
        self.conn = build_minimal_db(self.root / "geo.db")
        self.addCleanup(self.conn.close)

        self.bank_path = self.root / "bank.jsonl"
        self.bank_path.write_text(
            json.dumps(
                {"question": QUESTION_TEXT, "model": "doubao", "answer": "采集到的正文"},
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        self.collector = CsvBankCollector(self.bank_path)

    def run_once(self, **kwargs):
        kwargs.setdefault("tracker_id", TRACKER)
        kwargs.setdefault("question_text", QUESTION_TEXT)
        kwargs.setdefault("model_key", "doubao")
        kwargs.setdefault("tenant_id", TENANT)
        kwargs.setdefault("collector_key", self.collector.key)
        # 注册一个指向临时文件的采集器，避免依赖环境变量
        registry.register(self.collector)
        return collect_service.run_once(self.conn, **kwargs)

    def scalar(self, sql):
        return self.conn.execute(sql).fetchone()[0]

    def test_collect_writes_answer(self):
        result = self.run_once()

        self.assertTrue(result["inserted"])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM answer"), 1)
        row = self.conn.execute("SELECT text, collected_at, raw_uri FROM answer").fetchone()
        self.assertEqual(row["text"], "采集到的正文")
        self.assertTrue(row["raw_uri"].startswith("collect://csv_bank/"))

    def test_same_day_second_collect_is_a_second_sample(self):
        """同一天再采一次是第 2 条样本，不再按"同一秒"去重。

        早期实现用 row_hash（含到达秒）判重：同一秒内采两次会被吞掉一条。
        但平台对同一个问题经常返回逐字相同的答案，吞掉之后样本数永远
        够不到计划量，提及率的分母就是错的。
        幂等改由槽位保证（见 tests/integration/test_collect_slot.py）。
        """
        first = self.run_once()
        again = self.run_once()

        self.assertTrue(first["inserted"])
        self.assertTrue(again["inserted"])
        self.assertEqual(first["sample_idx"], 1)
        self.assertEqual(again["sample_idx"], 2)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM answer"), 2)

    def test_unknown_question_is_refused(self):
        with self.assertRaises(CollectorError) as ctx:
            self.run_once(question_text="没有这个问题")
        self.assertEqual(ctx.exception.code, "UNKNOWN_QUESTION")

    def test_unknown_tracker_is_refused(self):
        with self.assertRaises(CollectorError) as ctx:
            self.run_once(tracker_id="no-such-tracker")
        self.assertEqual(ctx.exception.code, "UNKNOWN_TRACKER")

    def test_unsupported_model_is_refused(self):
        with self.assertRaises(CollectorError) as ctx:
            self.run_once(model_key="kimi")
        self.assertEqual(ctx.exception.code, "UNSUPPORTED_MODEL")


if __name__ == "__main__":
    unittest.main()
