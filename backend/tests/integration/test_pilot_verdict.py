"""试点判定规则的验收：阈值固定在代码里，测试守的是「不许事后口头解释」。

effective / negative / inconclusive / insufficient 四种结论各一条，
再加一条「全对象口径不判定」——防止有人拿混合口径的数字当效果。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from geo.services import content_service
from tests import fixtures

ARTICLE = "a2000000-0000-0000-0000-000000000002"
CHANNEL = "ch000000-0000-0000-0000-000000000009"
PUB = "p2000000-0000-0000-0000-000000000002"
CONTROL_Q = "q2000000-0000-0000-0000-000000000007"
CONTROL_TEXT = "沈阳合同纠纷律师怎么找"

BEFORE_DAYS = ("2026-11-01", "2026-11-02", "2026-11-03")
AFTER_DAYS = ("2026-11-04", "2026-11-05", "2026-11-06")
SHORT_AFTER_DAYS = ("2026-11-04", "2026-11-05")  # 2 天 x 7 = 14 条，低于 21 条下限
PUBLISHED_AT = "2026-11-04T10:00:00+08:00"
PER_DAY = 7  # 3 天 x 7 = 21 条，正好等于判定下限


def _fill(conn, question_id: str, days: tuple[str, ...], mentions: int) -> list[str]:
    """按天铺满 PER_DAY 条答案，前 mentions 条加提及。"""
    answer_ids: list[str] = []
    for day in days:
        for index in range(PER_DAY):
            answer_ids.append(
                fixtures.add_answer(
                    conn,
                    f"{day}T{9 + index % 12:02d}:{index:02d}:00+08:00",
                    model_key="doubao",
                    question_id=question_id,
                )
            )
    for answer_id in answer_ids[:mentions]:
        fixtures.add_mention(conn, answer_id)
    return answer_ids


class PilotVerdictTest(unittest.TestCase):
    def _run(
        self,
        *,
        target_after_mentions: int,
        target_before_mentions: int = 0,
        control_after_mentions: int = 0,
        control_before_mentions: int = 0,
        with_control: bool = True,
        after_days: tuple[str, ...] = AFTER_DAYS,
        scope: str = "question",
    ) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            conn = fixtures.build_minimal_db(Path(tmp) / "verdict.db")
            try:
                conn.execute(
                    """INSERT INTO channel_account(id, tenant_id, platform_key, platform_name,
                                                   status, login_mode)
                       VALUES (?, ?, 'zhihu', '知乎', 'connected', 'scan_qr')""",
                    (CHANNEL, fixtures.TENANT),
                )
                conn.execute(
                    """INSERT INTO article(id, tenant_id, tracker_id, question_id, title, body,
                                           status, version, created_at, updated_at)
                       VALUES (?, ?, ?, ?, '沈阳律所怎么选', '正文', 'published', 1, ?, ?)""",
                    (
                        ARTICLE,
                        fixtures.TENANT,
                        fixtures.TRACKER,
                        fixtures.QUESTION,
                        fixtures.NOW,
                        fixtures.NOW,
                    ),
                )
                conn.execute(
                    """INSERT INTO publication(id, article_id, channel_id, status,
                                               published_url, published_at, created_at)
                       VALUES (?, ?, ?, 'published', 'https://zhuanlan.zhihu.com/p/777', ?, ?)""",
                    (PUB, ARTICLE, CHANNEL, PUBLISHED_AT, fixtures.NOW),
                )
                _fill(conn, fixtures.QUESTION, BEFORE_DAYS, target_before_mentions)
                _fill(conn, fixtures.QUESTION, after_days, target_after_mentions)
                if with_control:
                    fixtures.add_question(conn, CONTROL_TEXT, question_id=CONTROL_Q)
                    _fill(conn, CONTROL_Q, BEFORE_DAYS, control_before_mentions)
                    _fill(conn, CONTROL_Q, after_days, control_after_mentions)
                conn.commit()
                return content_service.publish_impact(
                    conn,
                    ARTICLE,
                    len(AFTER_DAYS),
                    scope=scope,
                    control_question_id=CONTROL_Q if with_control else None,
                )
            finally:
                conn.close()

    def test_effective_when_target_reaches_threshold_and_control_stays_flat(self):
        payload = self._run(target_after_mentions=7)
        verdict = payload["verdict"]
        self.assertEqual(verdict["status"], "effective")
        self.assertAlmostEqual(payload["delta_pp"], 33.3, places=1)
        self.assertEqual(verdict["observed"]["control_delta_pp"], 0.0)
        self.assertEqual(verdict["rule"], content_service.PILOT_RULE)

    def test_negative_when_target_does_not_move(self):
        payload = self._run(target_after_mentions=0, target_before_mentions=0)
        self.assertEqual(payload["verdict"]["status"], "negative")
        self.assertEqual(payload["verdict"]["message"], "未观察到提升")

    def test_inconclusive_when_lift_below_threshold(self):
        # 2/21 = 9.5pp：涨了，但没到 20pp
        payload = self._run(target_after_mentions=2)
        self.assertEqual(payload["verdict"]["status"], "inconclusive")
        self.assertAlmostEqual(payload["delta_pp"], 9.5, places=1)

    def test_inconclusive_when_control_moves_with_target(self):
        # 目标涨 33.3pp，但对照同期也涨 19pp：排除不了平台整体变化
        payload = self._run(target_after_mentions=7, control_after_mentions=4)
        verdict = payload["verdict"]
        self.assertEqual(verdict["status"], "inconclusive")
        self.assertGreaterEqual(
            abs(verdict["observed"]["control_delta_pp"]), verdict["rule"]["control_max_pp"]
        )
        self.assertTrue(any("对照问题" in reason for reason in verdict["reasons"]))

    def test_insufficient_when_window_below_sample_floor(self):
        # 发布后只有 14 条，低于 21 条：即使提及率看着涨了也不下结论
        payload = self._run(target_after_mentions=7, after_days=SHORT_AFTER_DAYS)
        verdict = payload["verdict"]
        self.assertEqual(verdict["status"], "insufficient")
        self.assertEqual(verdict["observed"]["after_samples"], 14)

    def test_inconclusive_without_control_question(self):
        # 目标问题涨到门槛但没设对照：不判「有效」，否则平台整体变化会被记成自己的功劳
        payload = self._run(target_after_mentions=7, with_control=False)
        verdict = payload["verdict"]
        self.assertEqual(verdict["status"], "inconclusive")
        self.assertTrue(any("对照" in reason for reason in verdict["reasons"]))

    def test_tracker_scope_is_not_judged(self):
        # 全对象口径混了别的题，判定不适用——避免拿它当效果对外讲
        payload = self._run(target_after_mentions=7, scope="tracker")
        self.assertEqual(payload["verdict"]["status"], "not_applicable")


if __name__ == "__main__":
    unittest.main()
