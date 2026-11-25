"""E1b 的筛选口径：全部 / 已监控 + 引用类型。

守两件事：
1. 「已监控」绝不拿全部引用冒充——信源名单没接入就是空，并明确告诉页面；
2. 引用类型筛选与 H2-7 共用 domain.citation 的同一份合法值定义，
   不能两套枚举各自漂移。
"""

from __future__ import annotations

import unittest

from geo.domain import citation as citation_domain
from geo.repositories import sqlite_repository as repo


class CitationViewFilterTest(unittest.TestCase):
    def test_default_and_valid_values(self):
        self.assertEqual(citation_domain.citation_view_filter(None), "all")
        self.assertEqual(citation_domain.citation_view_filter(""), "all")
        self.assertEqual(citation_domain.citation_view_filter("monitored"), "monitored")

    def test_invalid_view_is_rejected(self):
        for bad in ("watched", "ALL", "all,monitored"):
            with self.assertRaises(ValueError):
                citation_domain.citation_view_filter(bad)

    def test_monitoring_is_not_available_yet(self):
        self.assertFalse(citation_domain.MONITORING_AVAILABLE)


class AnswerCiteViewTest(unittest.TestCase):
    """用演示库核对：筛选后每条答案带的引用必须符合视图与类型。"""

    @classmethod
    def setUpClass(cls):
        import sqlite3
        from pathlib import Path

        db_path = Path(__file__).resolve().parents[3] / "geo.db"
        cls.conn = sqlite3.connect(db_path)
        cls.conn.row_factory = sqlite3.Row
        cls.tracker = "11111111-1111-1111-1111-111111111111"

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def _items(self, **kwargs):
        items, _ = repo.list_answers(
            self.conn,
            self.tracker,
            "2026-11-01",
            "2026-11-30",
            None,
            None,
            "all",
            1,
            20,
            **kwargs,
        )
        return items

    def test_all_view_keeps_citations(self):
        items = self._items()
        self.assertTrue(items)
        self.assertTrue(any(item["citations"] for item in items))

    def test_monitored_view_returns_no_citations(self):
        items = self._items(cite_view="monitored")
        self.assertEqual(len(items), len(self._items()))
        self.assertTrue(all(item["citations"] == [] for item in items))

    def test_cite_type_filter_uses_shared_values(self):
        items = self._items(cite_type="video")
        self.assertTrue(any(item["citations"] for item in items))
        for item in items:
            self.assertTrue(all(c["cite_type"] == "video" for c in item["citations"]))

    def test_type_and_view_combined(self):
        items = self._items(cite_type="video", cite_view="monitored")
        self.assertTrue(all(item["citations"] == [] for item in items))


if __name__ == "__main__":
    unittest.main()
