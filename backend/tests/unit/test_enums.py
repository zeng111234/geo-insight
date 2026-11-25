"""枚举与现场的绑定测试。

这几个枚举曾经是纯装饰：定义了一套词，代码里却用另一套，而且从没被引用过。
这类"看着像规范其实是死的"东西比没有更糟——照着它写的人会写出不被识别的值。

所以这里不测"枚举有哪些值"（那是把实现抄一遍），而是测**三处必须一致**：

1. 采集器抛出的错误码 ⊆ ``ErrorCode``
2. 引用分类的结果 ⊆ ``MediaType``，且前端标签表与它一一对应
3. 种子数据用的错误码 ⊆ ``ErrorCode``

任何一处单独改了而别处没跟，这几条会红。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from geo.collectors import citations
from geo.domain.enums import CiteType, ErrorCode, MediaType

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "backend" / "src" / "geo"
MONITOR_HTML = ROOT / "frontend" / "static" / "monitor.html"


def _collector_error_codes() -> dict[str, str]:
    """扫源码里所有 ``CollectorError("XXX", ...)`` 的字面量码，返回 {码: 文件}。

    只抓字面量：变量传进去的抓不到，但那种写法本身就少见，
    而且抓不到只会漏检、不会误报。
    """
    pattern = re.compile(r'CollectorError\(\s*"([A-Z_]+)"')
    found: dict[str, str] = {}
    for path in SRC.rglob("*.py"):
        for match in pattern.finditer(path.read_text(encoding="utf-8")):
            found.setdefault(match.group(1), path.name)
    return found


def _frontend_type_text_keys() -> set[str]:
    """前端 ``TYPE_TEXT`` 的键（中文标签表）。"""
    html = MONITOR_HTML.read_text(encoding="utf-8")
    block = re.search(r"TYPE_TEXT\s*=\s*\{(.*?)\}", html, re.S)
    if block is None:
        raise AssertionError("monitor.html 里找不到 TYPE_TEXT，前端标签表被改名或删了")
    return set(re.findall(r"(\w+)\s*:", block.group(1)))


class ErrorCodeTests(unittest.TestCase):
    def test_every_raised_code_is_registered(self):
        """采集器抛出的每一个码都必须在 ErrorCode 里登记。

        没登记的话，界面上会显示一个没人认得的字符串，
        而按错误码分组的统计会把它单独算成一类。
        """
        raised = _collector_error_codes()
        self.assertTrue(raised, "一个码都没扫到，正则大概失效了")

        registered = {item.value for item in ErrorCode}
        unknown = {code: where for code, where in raised.items() if code not in registered}
        self.assertEqual(
            unknown, {},
            f"这些码在 ErrorCode 里没有登记：{unknown}；新增失败码请先加进 domain/enums.py",
        )

    def test_seed_error_codes_are_registered(self):
        """种子数据里的错误码也要合法——它会出现在采集健康页上。"""
        from geo import seed

        registered = {item.value for item in ErrorCode}
        unknown = [code for code in seed.ERROR_CODES if code not in registered]
        self.assertEqual(unknown, [], f"seed.ERROR_CODES 里有未登记的码：{unknown}")

    def test_retryable_codes_are_platform_side(self):
        """可重试的码必须是"等一会能好"的那类。

        把配置问题（NOT_CONFIGURED）算成可重试，会让调度器在没配 key 时
        反复重试几十次；把内容问题（BAD_RESPONSE）算进去，会在结构变了之后
        空转一整轮。
        """
        from geo.services.collect_service import RETRYABLE_CODES

        self.assertEqual(
            set(RETRYABLE_CODES),
            {
                ErrorCode.RATE_LIMITED.value,
                ErrorCode.TIMEOUT.value,
                ErrorCode.CONNECTION.value,
                ErrorCode.UPSTREAM_ERROR.value,
            },
        )


class MediaTypeTests(unittest.TestCase):
    def test_citations_derive_from_enum(self):
        """``citations.MEDIA_TYPES`` 必须是枚举派生，不能是另写的字面量。"""
        self.assertEqual(citations.MEDIA_TYPES, tuple(item.value for item in MediaType))

    def test_frontend_labels_cover_all_but_other(self):
        """前端中文标签必须覆盖除 other 之外的所有类型。

        少了哪个，页面上就会直接显示英文原词（用户在"媒体类型"列里
        看到 `database` 而不是"数据库"）。
        """
        expected = {item.value for item in MediaType} - {MediaType.OTHER.value}
        self.assertEqual(
            _frontend_type_text_keys(),
            expected,
            "前端 TYPE_TEXT 与 MediaType 不一致；新增媒体类型要同时改 monitor.html",
        )

    def test_classify_media_only_returns_registered_types(self):
        """分类结果必须落在枚举里——数据库没有 CHECK，写错了没人拦。"""
        samples = [
            "https://zhuanlan.zhihu.com/p/1",
            "https://mp.weixin.qq.com/s/x",
            "https://www.bilibili.com/video/BV1",
            "https://baike.baidu.com/item/x",
            "https://wenshu.court.gov.cn/x",
            "https://blog.csdn.net/a",
            "https://news.sina.com.cn/a",
            "https://unknown-site.example/x",
            "",
        ]
        registered = {item.value for item in MediaType}
        for url in samples:
            with self.subTest(url=url):
                self.assertIn(citations.classify_media(url), registered)

    def test_infer_cite_type_only_returns_registered_types(self):
        registered = {item.value for item in CiteType}
        for media in MediaType:
            for platform_default in ("inline", "reference", "bogus"):
                with self.subTest(media=media, default=platform_default):
                    self.assertIn(
                        citations.infer_cite_type(media.value, platform_default), registered
                    )


if __name__ == "__main__":
    unittest.main()
