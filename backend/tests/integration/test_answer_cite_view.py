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
from tests import fixtures


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
    """自建库核对：筛选后每条答案带的引用必须符合视图与类型。

    不读演示库 geo.db —— 那条文件是运行产物、不入库，干净检出里根本不存在；
    测试依赖它就会「工作区绿、检出树红」，等于没有验证。
    """

    def setUp(self):
        import tempfile
        from pathlib import Path

        from geo.collectors.citations import domain_of
        from geo.services.import_service import sha
        from tests import fixtures

        self.tmp = tempfile.TemporaryDirectory()
        self.conn = fixtures.build_minimal_db(Path(self.tmp.name) / "cite.db")
        answer_id = fixtures.add_answer(
            self.conn, "2026-11-10T10:00:00+08:00", question_id=fixtures.QUESTION
        )
        for index, (url, cite_type) in enumerate(
            (
                ("https://zhuanlan.zhihu.com/p/1", "inline"),
                ("https://www.bilibili.com/video/1", "video"),
            )
        ):
            self.conn.execute(
                """INSERT INTO citation(id, answer_id, url, url_hash, domain, media_name,
                                        media_type, owner_type, position, cite_type,
                                        published_at, published_at_unknown)
                   VALUES (?, ?, ?, ?, ?, '来源', 'news', 'media', ?, ?, NULL, 0)""",
                (
                    f"cit-{index}",
                    answer_id,
                    url,
                    sha(url),
                    domain_of(url),
                    index + 1,
                    cite_type,
                ),
            )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _items(self, **kwargs):
        items, _ = repo.list_answers(
            self.conn,
            fixtures.TRACKER,
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
