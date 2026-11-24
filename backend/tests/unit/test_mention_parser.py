"""提及识别 v1 的口径测试（003 FR-007/FR-008）。

两层：``DetectTest`` 锁单条识别语义（精确/归一化/错写/词边界/否定句/位次），
``EvaluateGateTest`` 锁 FR-008 的过线口径——30 条人工标注样本上精确率与
召回率 ≥ 80%，否定句样本不得进漏检清单（spec Edge：提及但负面 ≠ 未提及）。
"""

import json
import unittest
from pathlib import Path

from geo.domain import mention_parser

ALIASES = ["辽宁辽征律师事务所", "辽征", "辽征律所"]
LABELED_FILE = Path(__file__).resolve().parents[1] / "labeled_mentions.json"


class DetectTest(unittest.TestCase):
    def test_exact_full_name_in_list_gets_position(self):
        text = "沈阳较受关注的律所包括：\n1. 辽宁辽征律师事务所\n2. 某某律师事务所（示例 2）"
        hit = mention_parser.detect(text, ALIASES)
        self.assertIsNotNone(hit)
        self.assertEqual(hit.match_type, "exact")
        self.assertEqual(hit.alias, "辽宁辽征律师事务所")
        self.assertEqual(hit.position, 1)
        self.assertEqual(hit.sentiment, "neu")

    def test_short_alias_in_prose_has_no_position(self):
        hit = mention_parser.detect("本地人遇到刑事案子，常推荐辽征。", ALIASES)
        self.assertIsNotNone(hit)
        self.assertEqual(hit.alias, "辽征")
        self.assertIsNone(hit.position)

    def test_position_parsed_from_numbered_line_with_chinese_delimiter(self):
        hit = mention_parser.detect("口碑榜：\n1、某某律师事务所\n2、辽征", ALIASES)
        self.assertEqual(hit.position, 2)

    def test_casefolded_latin_is_normalized_match(self):
        hit = mention_parser.detect("liaozheng has good reviews.", ["LiaoZheng"])
        self.assertEqual(hit.match_type, "normalized")
        self.assertEqual(hit.alias, "LiaoZheng")

    def test_fullwidth_latin_is_normalized_match(self):
        hit = mention_parser.detect("ＬＩＡＯＺＨＥＮＧ 处理劳动纠纷。", ["LiaoZheng"])
        self.assertEqual(hit.match_type, "normalized")

    def test_exact_latin_alias_stays_exact(self):
        hit = mention_parser.detect("咨询 LiaoZheng 律所。", ["LiaoZheng"])
        self.assertEqual(hit.match_type, "exact")

    def test_latin_alias_respects_word_boundary(self):
        # 前缀相同多一个字母不算命中；空格拆开也不算
        self.assertIsNone(mention_parser.detect("LIAOZHENGX 是另一家机构。", ["LiaoZheng"]))
        self.assertIsNone(mention_parser.detect("Liao Zheng is a person's name.", ["LiaoZheng"]))

    def test_earliest_occurrence_wins_and_longer_wins_ties(self):
        # 主命中 = 答案里第一次出现的写法；同一起点上更长的别名赢（辽征律所 > 辽征）
        hit = mention_parser.detect("辽征（全称辽宁辽征律师事务所）在浑南有分所。", ALIASES)
        self.assertEqual(hit.alias, "辽征")
        hit = mention_parser.detect("辽征律所不错", ALIASES)
        self.assertEqual(hit.alias, "辽征律所")

    def test_negated_mention_still_counts_and_marks_negative(self):
        hit = mention_parser.detect("不建议选辽征，差评不少。", ALIASES)
        self.assertIsNotNone(hit)  # 否定句 = 提及但负面，不是未提及
        self.assertEqual(hit.sentiment, "neg")
        self.assertEqual(hit.sentiment_confidence, 0.6)

    def test_no_match_returns_none(self):
        # 「辽」与「征」不相邻的陷阱文本
        self.assertIsNone(mention_parser.detect("辽宁征兵办公室电话可在官网查询。", ALIASES))
        self.assertIsNone(mention_parser.detect("沈阳有多家律所，建议先电话咨询。", ALIASES))

    def test_empty_inputs_return_none(self):
        self.assertIsNone(mention_parser.detect(None, ALIASES))
        self.assertIsNone(mention_parser.detect("", ALIASES))
        self.assertIsNone(mention_parser.detect("辽征不错", []))
        self.assertIsNone(mention_parser.detect("辽征不错", ["", "  "]))

    def test_duplicate_and_whitespace_aliases_are_deduped(self):
        hit = mention_parser.detect("辽征不错", ["辽征", "辽征", " 辽征 "])
        self.assertIsNotNone(hit)
        self.assertEqual(hit.alias, "辽征")

    def test_snippet_carries_context_without_newlines(self):
        filler = "前面很多字" * 10
        hit = mention_parser.detect(filler + "辽征" + filler, ALIASES)
        self.assertIn("辽征", hit.snippet)
        self.assertNotIn("\n", hit.snippet)


class EvaluateGateTest(unittest.TestCase):
    """FR-008：30 条人工标注样本，精确率/召回率 ≥ 80%，漏检清单可导出。"""

    @classmethod
    def setUpClass(cls):
        data = json.loads(LABELED_FILE.read_text(encoding="utf-8"))
        cls.aliases = [item["alias"] for item in data["aliases"]]
        cls.samples = data["samples"]
        cls.result = mention_parser.evaluate(cls.samples, cls.aliases)

    def test_sample_count_is_thirty(self):
        self.assertEqual(len(self.samples), 30)

    def test_precision_and_recall_meet_threshold(self):
        self.assertGreaterEqual(self.result["precision"], 0.8)
        self.assertGreaterEqual(self.result["recall"], 0.8)

    def test_negated_samples_are_not_misses(self):
        # 否定句样本标注为「提及」，漏检清单里不该出现它们
        negated = [
            s for s in self.samples if "不建议" in s["text"] or "不推荐" in s["text"]
        ]
        self.assertTrue(negated)
        missed_texts = {m["text"] for m in self.result["misses"]}
        for sample in negated:
            self.assertNotIn(sample["text"], missed_texts)

    def test_miss_list_structure_is_exportable(self):
        # 评估脚本按这个结构导 CSV；键集变了脚本要跟着改
        for miss in self.result["misses"]:
            self.assertEqual(set(miss), {"index", "text", "note"})
        for alarm in self.result["false_alarms"]:
            self.assertEqual(set(alarm), {"index", "text", "note"})

    def test_evaluate_counts_add_up(self):
        r = self.result
        # 标注为提及的样本数 = 命中 + 漏检，召回的分母要对得上标注基数
        labeled_positive = sum(1 for s in self.samples if s["mentioned"])
        self.assertEqual(labeled_positive, r["true_positive"] + len(r["misses"]))
