"""业务时区（北京时间 UTC+8）的“今天/明天”。

决策 007（docs/decisions/007-local-day-boundary.md）规定：按天口径走业务时区自然日，
不按 UTC，也不按服务器本地时区。采集端已经硬编码 +08:00（DEFAULT_TZ），
但 `date.today()` 取的是**服务器本地时区**——本机开发是 +08 看不出问题，
部署到 UTC 服务器后，北京时间 00:00–07:59 会算成前一天。

模块提供一个固定偏移的时区，而不是 `ZoneInfo("Asia/Shanghai")`：
- 不依赖系统的 tzdata（精简容器里常没装）；
- 中国自 1991 年后无夏令时，固定 +8 与真实时区等价；
- 与采集端 DEFAULT_TZ 的写法保持一致，口径只有一种。

`now` 参数用于测试注入任意时刻；生产调用不传，取当前时刻。
"""

from datetime import datetime, timedelta, timezone

# 与 geo.collectors.* / collect_service 的 DEFAULT_TZ 保持同一个偏移。
BUSINESS_TZ = timezone(timedelta(hours=8))


def business_today(now: datetime | None = None) -> str:
    """业务时区的今天（YYYY-MM-DD）。"""
    moment = now.astimezone(BUSINESS_TZ) if now is not None else datetime.now(BUSINESS_TZ)
    return moment.date().isoformat()


def business_tomorrow(now: datetime | None = None) -> str:
    """业务时区的明天（YYYY-MM-DD）。"""
    moment = now.astimezone(BUSINESS_TZ) if now is not None else datetime.now(BUSINESS_TZ)
    return (moment.date() + timedelta(days=1)).isoformat()
