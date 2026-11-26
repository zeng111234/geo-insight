"""默认日期范围必须按业务时区，不随服务器时区漂移。

`_period()` 被 16 个接口共用：请求没带 from/to 时，它决定"默认看哪几天"。
部署到 UTC 服务器后，北京时间 00:00–07:59 的服务器本地日期还是前一天。
如果这里用 date.today()，这些接口的默认范围会一起错一天——
用户早上打开看板看不到当天数据，而且不会报错（静默错误，最难发现）。

判据方式：同时冻结"服务器本地日期"和"业务日期"为不同的两天，
断言 _period 采用业务日期。修复前的 date.today() 实现会拿到服务器日期而红。
"""

import unittest
from datetime import date
from unittest.mock import patch

from starlette.datastructures import QueryParams
from starlette.requests import Request

from geo.api import app as app_module

UTC_SERVER_DATE = date(2026, 11, 26)  # UTC 服务器此刻"以为"的今天
BUSINESS_DATE = "2026-11-27"  # 北京时间已跨日，业务上真正的今天


def _request(query: str = "") -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/v1/metrics/overview",
        "query_string": query.encode(),
        "headers": [],
    }
    return Request(scope)


class PeriodTimezoneTest(unittest.TestCase):
    def test_default_range_uses_business_date_not_server_date(self):
        # 冻结"服务器本地日期"为一个假 date 类（今日=UTC 的 11-26），
        # 同时冻结 business_today（业务真日期 11-27）。
        # 修复前 _period 走 fake_date.today() → 拿到 11-26 → 断言红；
        # 修复后走 business_today() → 11-27 → 绿。fromisoformat 仍需真实现参与运算。
        class FakeServerDate:
            today = staticmethod(lambda: UTC_SERVER_DATE)
            fromisoformat = staticmethod(date.fromisoformat)

        with patch.object(app_module, "business_today", return_value=BUSINESS_DATE), patch.object(
            app_module, "date", FakeServerDate
        ):
            start, end = app_module._period(_request())

        self.assertEqual(end, BUSINESS_DATE)
        # 默认窗口是最近 7 天（含今天）：11-27 往前 6 天 = 11-21
        self.assertEqual(start, "2026-11-21")

    def test_explicit_params_override_business_default(self):
        # 显式传 from/to 时不受默认时区影响——反向保证收口没有改变显式查询语义。
        with patch.object(app_module, "business_today", return_value=BUSINESS_DATE):
            start, end = app_module._period(_request("from=2026-11-01&to=2026-11-05"))
        self.assertEqual((start, end), ("2026-11-01", "2026-11-05"))

    def test_no_plain_date_today_in_period(self):
        # 静态守门：_period 函数体（不含注释）不该再调用 date.today()。
        import inspect

        src = inspect.getsource(app_module._period)
        code_lines = [ln for ln in src.splitlines() if not ln.lstrip().startswith("#")]
        self.assertFalse(
            any("date.today()" in ln for ln in code_lines),
            "_period 又用回 date.today()，UTC 服务器默认范围会错一天",
        )


if __name__ == "__main__":
    unittest.main()
