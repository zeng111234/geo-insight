"""核心词的解析/校验/跑题判定，以及 006 迁移的加列行为。"""

import sqlite3
import unittest

from geo.domain import relevance


class ParseKeywordsTest(unittest.TestCase):
    def test_empty_inputs(self):
        for raw in (None, "", "  ", "[]", [], (), 123, {"a": 1}):
            self.assertEqual(relevance.parse_keywords(raw), [], f"{raw!r} 应解析为空")

    def test_json_string_from_db(self):
        self.assertEqual(
            relevance.parse_keywords('["律所", "律师"]'), ["律所", "律师"]
        )

    def test_comma_string_including_chinese_comma(self):
        self.assertEqual(
            relevance.parse_keywords("律所，律师, 刑事辩护 "), ["律所", "律师", "刑事辩护"]
        )

    def test_dedupes_and_keeps_order(self):
        self.assertEqual(
            relevance.parse_keywords(["律师", " 律所 ", "律师", ""]), ["律师", "律所"]
        )

    def test_broken_json_falls_back_to_empty(self):
        """配置写坏了不能让页面打不开，当没配处理。"""
        self.assertEqual(relevance.parse_keywords('["律所",'), [])

    def test_drops_non_string_items(self):
        self.assertEqual(relevance.parse_keywords(["律所", 1, None, "律师"]), ["律所", "律师"])


class ValidateKeywordsTest(unittest.TestCase):
    def test_accepts_normal_list(self):
        self.assertIsNone(relevance.validate_keywords(["律所", "律师"]))

    def test_rejects_too_many(self):
        error = relevance.validate_keywords([f"词{i}" for i in range(relevance.MAX_KEYWORDS + 1)])
        self.assertIn("最多", error)

    def test_rejects_too_long(self):
        error = relevance.validate_keywords(["长" * (relevance.MAX_KEYWORD_LEN + 1)])
        self.assertIn("超过", error)


class OffTopicTest(unittest.TestCase):
    def test_no_keywords_means_undetermined(self):
        """没配核心词时返回 None，不能当成「跑题」或「不跑题」。"""
        self.assertIsNone(relevance.is_off_topic("随便一段话", []))
        self.assertIsNone(relevance.is_off_topic(None, []))

    def test_hit_is_not_off_topic(self):
        self.assertFalse(relevance.is_off_topic("沈阳比较好的律所有几家", ["律所", "律师"]))

    def test_miss_is_off_topic(self):
        self.assertTrue(relevance.is_off_topic("今天天气不错", ["律所", "律师"]))

    def test_empty_text_is_off_topic(self):
        self.assertTrue(relevance.is_off_topic("", ["律所"]))
        self.assertTrue(relevance.is_off_topic(None, ["律所"]))

    def test_english_is_case_insensitive(self):
        self.assertFalse(relevance.is_off_topic("Try Law Firm A", ["law firm"]))


class MigrationTest(unittest.TestCase):
    def test_adds_column_and_is_rerunnable(self):
        from pathlib import Path

        import importlib.util

        path = Path(__file__).resolve().parents[2] / "migrations" / "006_tracker_keywords.py"
        spec = importlib.util.spec_from_file_location("m006", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        conn = sqlite3.connect(":memory:")
        # 模拟老库：只有 tracker 表，没有 keywords 列
        conn.execute("CREATE TABLE tracker (id TEXT PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO tracker(id, name) VALUES ('t1', '老对象')")

        module.apply(conn)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(tracker)")}
        self.assertIn("keywords", columns)
        # 默认值必须是合法 JSON，否则 parse_keywords 会走异常分支
        value = conn.execute("SELECT keywords FROM tracker WHERE id = 't1'").fetchone()[0]
        self.assertEqual(relevance.parse_keywords(value), [])

        module.apply(conn)  # 重跑不应报错
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM tracker").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
