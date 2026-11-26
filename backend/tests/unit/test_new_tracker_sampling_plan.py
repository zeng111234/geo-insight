"""新建监控对象必须自带采样计划。

真缺口：通过界面/API 新建的对象不会建 sampling_plan 行，
daily_planned=0、models=[]，采集链路永远不覆盖它——
用户建完对象、配完问题，却发现永远没数据，这正是"玩具感"的来源之一。
seed 和 setup_pilot 各自建了计划，唯独 create 路径漏了。

本测试锁定：create() 之后必须有一条可用计划（默认 deepseek、core3/长尾1、
业务时区今天生效），且允许调用方用 model_keys 覆盖。
"""

import tempfile
import unittest
from pathlib import Path

from geo.services import config_service
from tests import fixtures


class NewTrackerSamplingPlanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = fixtures.build_minimal_db(Path(self.tmp.name) / "t.db")
        # addCleanup 后进先出：先注册 tmp.cleanup、后注册 conn.close，
        # 执行时才先关连接再删目录（否则 Windows 上文件被占用删不掉）。
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.conn.close)

    def _create(self, **extra):
        payload = {
            "name": "新对象",
            "brand_name": "新品牌",
            **extra,
        }
        return config_service.create(self.conn, fixtures.TENANT, payload, fixtures.NOW)

    def test_new_tracker_has_sampling_plan(self):
        created = self._create()
        tid = created["tracker_id"]
        row = self.conn.execute(
            "SELECT * FROM sampling_plan WHERE tracker_id = ?", (tid,)
        ).fetchone()
        self.assertIsNotNone(row, "新建对象必须有采样计划，否则永远采不到数据")
        self.assertEqual(row["channel"], "api")
        self.assertEqual(row["core_times"], 3)
        self.assertEqual(row["long_tail_times"], 1)
        self.assertEqual(row["timezone"], "Asia/Shanghai")
        self.assertIn("deepseek", row["model_keys"])

    def test_effective_from_is_business_today(self):
        # 生效日按业务时区，不是服务器本地 date.today()（UTC 服务器凌晨会错一天）。
        from geo.business_time import business_today

        created = self._create()
        row = self.conn.execute(
            "SELECT effective_from FROM sampling_plan WHERE tracker_id = ?",
            (created["tracker_id"],),
        ).fetchone()
        self.assertEqual(row["effective_from"], business_today())

    def test_model_keys_can_be_overridden(self):
        created = self._create(model_keys=["doubao", "deepseek"])
        row = self.conn.execute(
            "SELECT model_keys FROM sampling_plan WHERE tracker_id = ?",
            (created["tracker_id"],),
        ).fetchone()
        self.assertEqual(sorted(__import__("json").loads(row["model_keys"])), ["deepseek", "doubao"])

    def test_sampling_overview_shows_positive_plan(self):
        # 端到端：建完对象，看板应该显示每天有计划量，而不是 0。
        from geo.services import config_service as svc

        created = self._create()
        plan = svc.sampling_overview(self.conn, created["tracker_id"])
        self.assertEqual(plan["daily_planned"], 0)  # 没有问题时计划量是 0，但模型不为 0
        self.assertIn("deepseek", plan["models"])
        self.assertNotIn("采样计划未配置模型，按 0 计", plan["notes"])


if __name__ == "__main__":
    unittest.main()
