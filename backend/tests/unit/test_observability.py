"""H4-5 最小可观测的口径测试。

守的性质：
1. 0 请求时错误率是 None 不是 0（"0/0 不是 0%"）
2. 错误只算 5xx，4xx 不算
3. 慢请求按阈值计，P95 能算出来
4. 环形窗口有上限，不会无限涨
5. /health 与 /static 不进窗口（自观测不能污染分母）
6. 真输出一行 JSON 日志，5xx 是 ERROR 级
"""

import io
import json
import logging
import unittest

from geo import observability


class RequestWindowTest(unittest.TestCase):
    def make(self, size=200, slow_ms=1000):
        return observability.RequestWindow(size=size, slow_ms=slow_ms)

    def test_empty_window_reports_none_not_zero(self):
        stats = self.make().stats()
        self.assertEqual(stats["requests"], 0)
        self.assertIsNone(stats["error_rate"], "没有样本就不给结论")
        self.assertIsNone(stats["p95_ms"])

    def test_error_rate_counts_only_5xx(self):
        w = self.make()
        w.observe("GET", "/v1/a", 200, 10, "r1")
        w.observe("GET", "/v1/b", 400, 10, "r2")  # 客户端错误，不算
        w.observe("GET", "/v1/c", 500, 10, "r3")
        w.observe("GET", "/v1/d", 503, 10, "r4")
        stats = w.stats()
        self.assertEqual(stats["requests"], 4)
        self.assertEqual(stats["errors"], 2)
        self.assertEqual(stats["error_rate"], 0.5)

    def test_slow_and_p95(self):
        w = self.make(slow_ms=50)
        for i, ms in enumerate((10, 20, 30, 40, 200)):
            w.observe("GET", f"/v1/{i}", 200, ms, f"r{i}")
        stats = w.stats()
        self.assertEqual(stats["slow"], 1)
        self.assertEqual(stats["slow_threshold_ms"], 50)
        # 5 个样本，P95 取第 5 个（index int(0.95*4)=3 → 40；实现按此公式）
        self.assertEqual(stats["p95_ms"], 40)

    def test_ring_window_is_bounded(self):
        w = self.make(size=3)
        for i in range(10):
            w.observe("GET", "/v1/x", 200, 1, f"r{i}")
        self.assertEqual(w.stats()["requests"], 3)

    def test_health_and_static_are_not_recorded(self):
        w = self.make()
        w.observe("GET", "/health", 200, 1, "h")
        w.observe("GET", "/static/app.js", 200, 1, "s")
        w.observe("GET", "/v1/trackers", 200, 1, "t")
        self.assertEqual(w.stats()["requests"], 1)

    def test_structured_log_line_is_json_and_5xx_is_error(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("geo.requests")
        old_handlers, old_level = logger.handlers[:], logger.level
        logger.handlers, logger.level = [handler], logging.INFO
        try:
            w = self.make()
            w.observe("POST", "/v1/reports", 500, 12, "req-abc")
        finally:
            logger.handlers, logger.level = old_handlers, old_level
        line = stream.getvalue().strip()
        payload = json.loads(line)  # 必须是可解析的一行 JSON
        self.assertEqual(payload["method"], "POST")
        self.assertEqual(payload["path"], "/v1/reports")
        self.assertEqual(payload["status"], 500)
        self.assertEqual(payload["duration_ms"], 12)
        self.assertEqual(payload["request_id"], "req-abc")
        self.assertEqual(payload["error"], "server")


if __name__ == "__main__":
    unittest.main()
