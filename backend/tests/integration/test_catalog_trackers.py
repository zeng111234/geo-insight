"""P7:演示库左栏补到 19 个对象，且不污染任何已验收口径。

守两件事：
1. 对象数从 2 → 19(17 个空对象)，且新对象能被搜索筛到、点进去是诚实空态；
2. 黄金窗口 9 样本 / 22.22%、1516/214、报告 5 份——全部绑定 TRACKER，
   加空对象后分毫不动。这是 P7 的前置硬约束。

空对象只写 brand + tracker + keywords，不造 answer/mention/run/report，
所以它不会挤进任何按 TRACKER 过滤的聚合查询。
"""

import unittest

from geo import db, seed
from geo.repositories import report_repository, sqlite_repository as repo
from geo.services import config_service, metric_service


class CatalogTrackersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed.seed()
        cls.conn = db.connect()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_nineteen_trackers_total(self):
        items = repo.list_trackers(self.conn, seed.TENANT)
        self.assertEqual(len(items), 19)

    def test_new_tracker_is_brand_backed_and_searchable(self):
        items = repo.list_trackers(self.conn, seed.TENANT)
        names = [t["brand_name"] or t["name"] for t in items]
        # 搜索靠品牌/对象名，新对象必须真在列表里且品牌名非空
        self.assertTrue(any("启明法律" in n for n in names))
        self.assertTrue(any("悦途出行" in n for n in names))
        new = next(t for t in items if t["name"].startswith("启明法律"))
        self.assertTrue(new["brand_name"])

    def test_new_tracker_has_honest_empty_metrics(self):
        new_id = "c0000000-0000-0000-0000-000000000001"
        payload = metric_service.build_overview(
            self.conn, new_id, "2026-11-01", "2026-11-30", None, None
        )
        self.assertEqual(payload["valid_total"], 0, "空对象不该凭空有样本")
        questions = repo.question_summary(self.conn, new_id)
        self.assertEqual(questions["total"], 0)

    def test_golden_window_untouched(self):
        payload = metric_service.build_overview(
            self.conn, seed.TRACKER, "2026-11-01", "2026-11-03", None, None
        )
        self.assertEqual(payload["valid_total"], 9)
        self.assertEqual(payload["cards"]["mention_count"], 2)
        self.assertEqual(payload["cards"]["mention_rate"]["value"], 0.2222)

    def test_report_count_untouched(self):
        reports = report_repository.list_reports(self.conn, seed.TRACKER, seed.TENANT)
        self.assertEqual(len(reports), 5)

    def test_sampling_baseline_untouched(self):
        plan = config_service.sampling_overview(self.conn, seed.TRACKER)
        self.assertEqual(plan["failure"]["attempted"], 1516)
        self.assertEqual(plan["failure"]["failed"], 214)


if __name__ == "__main__":
    unittest.main()
