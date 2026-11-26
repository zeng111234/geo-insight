"""业务时区收口：验证"今天/明天"与服务器时区无关。

判据来自部署场景：服务器是 UTC 时，北京时间 00:00–07:59 对应 UTC 前一天。
用 `date.today()` 会算成前一天；固定 +08:00 的 business_today 才对。
测试注入 UTC 时刻，因此在任何时区的机器上都得到同一结果（可比、可复现）。
"""

import unittest
from datetime import datetime, timedelta, timezone

from geo.business_time import BUSINESS_TZ, business_today, business_tomorrow

UTC = timezone.utc
# 2026-11-26T16:30:00Z = 北京时间 2026-11-27 00:30（已跨日）
UTC_EARLY_BJT_NEXT_DAY = datetime(2026, 11, 26, 16, 30, tzinfo=UTC)
# 2026-11-26T15:59:00Z = 北京时间 2026-11-26 23:59（未跨日）
UTC_LATE_BJT_SAME_DAY = datetime(2026, 11, 26, 15, 59, tzinfo=UTC)


class BusinessTimeTest(unittest.TestCase):
    def test_offset_is_eight_hours(self):
        self.assertEqual(BUSINESS_TZ.utcoffset(None), timedelta(hours=8))

    def test_early_utc_but_next_day_in_beijing(self):
        # 服务器 UTC 日期是 11-26，业务日期已是 11-27。这就是 date.today() 会错的那 8 小时。
        self.assertEqual(business_today(UTC_EARLY_BJT_NEXT_DAY), "2026-11-27")
        self.assertEqual(business_tomorrow(UTC_EARLY_BJT_NEXT_DAY), "2026-11-28")
        # 反证：直接用 UTC 的 date() 会错成前一天
        self.assertEqual(UTC_EARLY_BJT_NEXT_DAY.date().isoformat(), "2026-11-26")

    def test_late_utc_still_same_day_in_beijing(self):
        self.assertEqual(business_today(UTC_LATE_BJT_SAME_DAY), "2026-11-26")
        self.assertEqual(business_tomorrow(UTC_LATE_BJT_SAME_DAY), "2026-11-27")

    def test_cross_year_boundary(self):
        # 2026-12-31T17:00Z = 北京 2027-01-01 01:00
        moment = datetime(2026, 12, 31, 17, 0, tzinfo=UTC)
        self.assertEqual(business_today(moment), "2027-01-01")
        self.assertEqual(business_tomorrow(moment), "2027-01-02")

    def test_naive_datetime_is_treated_as_business_time(self):
        # 生产不注入；若调用方传了不带时区的时间，astimezone 会按系统本地时区解读，
        # 这正是我们要避免的不确定性——这里锁定：naive 的 00:30 被视为业务时区当天凌晨。
        naive = datetime(2026, 11, 27, 0, 30)
        self.assertEqual(business_today(naive), "2026-11-27")


class CallSiteTest(unittest.TestCase):
    """守住两个调用点真的用了业务时区，而不是又退回去写 date.today()。"""

    def test_tomorrow_helper_uses_business_time(self):
        from geo.repositories import config_repository as config
        from geo.business_time import business_tomorrow as bt

        # tomorrow() 不接受注入，用 unittest.mock 固定工具返回值，确认它被真正调用。
        from unittest import mock

        with mock.patch.object(config, "business_tomorrow", return_value="2027-03-01") as m:
            self.assertEqual(config.tomorrow(), "2027-03-01")
        m.assert_called_once_with()

        self.assertIs(config.business_tomorrow, bt)

    def test_no_plain_date_today_in_both_call_sites(self):
        # 静态守门：这两个文件的**代码行**（不含注释）不该再调用 date.today()。
        # 注释里会解释"为什么不用 date.today()"，所以必须跳过注释行，否则假红。
        from pathlib import Path

        src = Path(__file__).resolve().parents[2] / "src" / "geo"
        for rel in ("repositories/config_repository.py", "services/content_service.py"):
            code_lines = [
                line
                for line in (src / rel).read_text(encoding="utf-8").splitlines()
                if not line.lstrip().startswith("#")
            ]
            self.assertFalse(
                any("date.today()" in line for line in code_lines),
                f"{rel} 又用回了 date.today()，部署到 UTC 服务器会错一天",
            )


if __name__ == "__main__":
    unittest.main()
