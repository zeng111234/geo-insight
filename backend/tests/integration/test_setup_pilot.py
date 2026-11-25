"""试点建对象脚本的验收。

这条脚本守的是 D1 的第一步：照着协议建出来的对象，必须立刻能采样。
四个必备步骤里少任意一个，现象都是"对象建好了但没数据"，很难从界面上看出少在哪：
  1. 独立 tracker         —— 混演示数据，前后对比没意义
  2. 活动问题集指针       —— 不设，所有指标 SQL 按 NULL 过滤，问题等于不存在
  3. sampling_plan        —— 不写，compute_gaps 直接报"去 /settings 配"（而那里是只读的）
  4. 目标与对照都标 core  —— D1 判据要求各 3 次/天

变异检查见文件末尾的说明：故意去掉第 2 或第 3 步，对应断言必须红。
"""

from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path

from geo.settings import settings
from geo.services import sampling_service
from scripts import setup_pilot


def _args(**overrides) -> argparse.Namespace:
    base = dict(
        name="试点-辽征",
        brand="辽宁辽征",
        target="沈阳离婚律师收费标准是多少",
        control="沈阳合同纠纷律师怎么找",
        aliases="辽征所",
        competitors="华辰教育",
        keywords="离婚,律师收费",
        model="doubao",
        core_times=3,
        long_tail_times=1,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


class SetupPilotTests(unittest.TestCase):
    def setUp(self):
        from tests import fixtures

        self._dir = tempfile.TemporaryDirectory()
        self.conn = fixtures.build_minimal_db(Path(self._dir.name) / "pilot.db")
        self.tenant = settings.default_tenant_id

    def tearDown(self):
        self.conn.close()
        self._dir.cleanup()

    def test_builds_a_samplable_pilot(self):
        result = setup_pilot.setup(self.conn, _args())

        tracker_id = result["tracker_id"]
        row = self.conn.execute(
            "SELECT active_question_set_id FROM tracker WHERE id = ?", (tracker_id,)
        ).fetchone()
        self.assertEqual(row["active_question_set_id"], result["question_set_id"])

        plan = self.conn.execute(
            "SELECT model_keys, core_times FROM sampling_plan WHERE tracker_id = ?", (tracker_id,)
        ).fetchone()
        self.assertIsNotNone(plan, "采样计划必须写下，否则一采就报去 /settings 配")
        self.assertEqual(plan["core_times"], 3)
        self.assertEqual(plan["model_keys"], '["doubao"]')

        questions = self.conn.execute(
            "SELECT text, tier FROM question WHERE question_set_id = ?", (result["question_set_id"],)
        ).fetchall()
        self.assertEqual(len(questions), 2)
        self.assertEqual({q["tier"] for q in questions}, {"core"})

        gaps = sampling_service.compute_gaps(
            self.conn, tracker_id, "2026-11-25", "2026-11-25", model="doubao"
        )
        self.assertEqual(gaps["totals"]["expected"], 6)

    def test_active_pointer_is_visible_to_metric_queries(self):
        """活动指针必须对指标查询可见——这是第 2 步存在的唯一理由。"""
        result = setup_pilot.setup(self.conn, _args())
        rows = self.conn.execute(
            """SELECT q.text FROM question q
               JOIN tracker t ON t.active_question_set_id = q.question_set_id
               WHERE t.id = ?""",
            (result["tracker_id"],),
        ).fetchall()
        self.assertEqual(len(rows), 2)

    def test_same_name_is_refused(self):
        setup_pilot.setup(self.conn, _args())
        with self.assertRaises(setup_pilot.SetupError) as caught:
            setup_pilot.setup(self.conn, _args(target="另一句", control="再一句"))
        self.assertEqual(caught.exception.code, "ALREADY_EXISTS")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM tracker WHERE name = '试点-辽征'").fetchone()[0],
            1,
        )

    def test_target_and_control_cannot_be_same_text(self):
        with self.assertRaises(setup_pilot.SetupError) as caught:
            setup_pilot.validate(_args(control="沈阳离婚律师收费标准是多少"))
        self.assertEqual(caught.exception.code, "SAME_QUESTION")

    def test_bad_times_are_rejected_before_writing(self):
        before = self.conn.execute("SELECT COUNT(*) FROM tracker").fetchone()[0]
        with self.assertRaises(setup_pilot.SetupError):
            setup_pilot.validate(_args(core_times=0))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM tracker").fetchone()[0], before)


if __name__ == "__main__":
    unittest.main()
