# -*- coding: utf-8 -*-
"""发布全局并发闸门的行为测试(进程内 Semaphore)。

为什么用真线程 + Event 而不是测「信号量的 acquire 能不能调用」:
闸门的价值是「同时最多 N 个浏览器在跑,其余停在 queued」。这条只有让多个
worker 真的并发起来、卡在 _run_cli 的阻塞点上才验得出来。断言打到三件事:
1. 进入 CLI(≈起浏览器)的 worker 数 <= 上限;
2. 没拿到槽的 worker 不写 running,记录停在 queued;
3. 无论成功/失败,槽位都会归还,后续 worker 能继续。
测试缝是 _run_cli(唯一子进程出口),db/repo 用假实现,不打真浏览器也不写真库。
"""
from __future__ import annotations

import pathlib
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

from geo.services import publish_service

ARTICLE = {"title": "一篇测试文章", "body": "正文一段。"}


class _SlotHarness:
    """共享状态:记录谁进了 CLI、库里写了哪些状态。"""

    def __init__(self, slots: int):
        self.entered: list[str] = []               # 进入 _run_cli 的 pub_id
        self.statuses: dict[str, list[str]] = {}   # pub_id -> 写过的状态序列
        self.entered_evt = threading.Event()
        self.release_evt = threading.Event()
        self.lock = threading.Lock()
        self.sem = threading.Semaphore(slots)

    def fake_cli(self, script, *args, **kwargs):
        pub_id = pathlib.Path(args[-1]).stem
        with self.lock:
            self.entered.append(pub_id)
            self.entered_evt.set()
        # 阻塞直到测试放行;放行前槽位一直被占着
        self.release_evt.wait(timeout=5)
        return subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout='{"status":"published","final_url":"http://x"}', stderr="",
        )

    def fake_status(self, conn, pub_id, status, **kwargs):
        with self.lock:
            self.statuses.setdefault(pub_id, []).append(status)

    def patches(self, cli=None):
        # reset 保证每个用例从干净的闸门状态开始;直接注入指定上限的信号量
        publish_service.reset_publish_slots()
        publish_service._publish_slots = self.sem
        # draft.json 写临时目录,绝不能落到仓库(cwd)里留垃圾
        self.drafts = tempfile.TemporaryDirectory()
        return (
            mock.patch.object(publish_service, "_run_cli", cli or self.fake_cli),
            mock.patch.object(publish_service.repo, "update_publication_status", self.fake_status),
            mock.patch.object(publish_service.db, "connect", return_value=mock.MagicMock()),
            mock.patch.object(publish_service, "release_channel_lock"),
            mock.patch.object(publish_service, "DRAFT_DIR", pathlib.Path(self.drafts.name)),
        )

    def wait_entered(self, count: int, timeout: float = 3.0) -> bool:
        """等到至少 count 个 worker 进入 CLI 为止。"""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self.lock:
                if len(self.entered) >= count:
                    return True
            self.entered_evt.wait(0.02)
            self.entered_evt.clear()
        return False


class PublishConcurrencyTest(unittest.TestCase):
    BROWSER_KEYS = ["sohu", "zhihu", "wechat", "douyin"]

    def tearDown(self):
        publish_service.reset_publish_slots()

    @staticmethod
    def _run_workers(keys: list[str]):
        threads = [
            threading.Thread(
                target=publish_service.dispatch_publication,
                args=(f"pub-{k}", k, ARTICLE),
                daemon=True,
            )
            for k in keys
        ]
        for t in threads:
            t.start()
        return threads

    @staticmethod
    def _stop_all(patches, harness):
        for p in reversed(patches):
            p.stop()
        harness.drafts.cleanup()

    def test_browser_workers_capped_and_queued_stay_queued(self):
        """上限=2,4 个浏览器渠道并发:最多 2 个进 CLI,其余停在 queued。"""
        h = _SlotHarness(slots=2)
        patches = h.patches()
        for p in patches:
            p.start()
        try:
            threads = self._run_workers(self.BROWSER_KEYS)
            self.assertTrue(h.wait_entered(2), "两个 worker 应当拿到槽位进入 CLI")
            # 再等一小段,确认没有第 3 个挤进来(槽位确实限住了)
            self.assertFalse(h.wait_entered(3, timeout=0.4), "超过上限的 worker 不应进入 CLI")
            with h.lock:
                self.assertEqual(len(h.entered), 2)
                running = [pid for pid, ss in h.statuses.items() if "running" in ss]
            self.assertEqual(len(running), 2, "没拿到槽的记录不该被写成 running")
            for pid in ("pub-sohu", "pub-zhihu", "pub-wechat", "pub-douyin"):
                if pid not in running:
                    self.assertEqual(h.statuses.get(pid, []), [], f"{pid} 应停在 queued(不写任何状态)")

            h.release_evt.set()
            for t in threads:
                t.join(timeout=5)
            with h.lock:
                self.assertEqual(len(h.entered), 4, "槽位归还后排队的 worker 应继续")
        finally:
            h.release_evt.set()
            self._stop_all(patches, h)

    def test_api_channel_does_not_consume_browser_slot(self):
        """浏览器槽位=1 且已被占时,百家号(API)仍应立即开跑。"""
        h = _SlotHarness(slots=1)
        patches = h.patches()
        for p in patches:
            p.start()
        try:
            threads = self._run_workers(["sohu", "baijiahao"])
            self.assertTrue(h.wait_entered(2), "API 渠道不该被浏览器槽位挡住")
            with h.lock:
                self.assertIn("pub-baijiahao", h.entered)
            h.release_evt.set()
            for t in threads:
                t.join(timeout=5)
        finally:
            h.release_evt.set()
            self._stop_all(patches, h)

    def test_slot_returned_even_when_cli_fails(self):
        """CLI 抛异常时 worker 记 failed,且槽位仍归还。"""
        h = _SlotHarness(slots=1)
        calls = {"n": 0}

        def flaky(script, *args, **kwargs):
            calls["n"] += 1
            raise RuntimeError("模拟 CLI 崩溃")

        patches = h.patches(cli=flaky)
        for p in patches:
            p.start()
        try:
            threads = self._run_workers(["sohu"])
            for t in threads:
                t.join(timeout=5)
            self.assertEqual(h.statuses["pub-sohu"][-1], "failed")
            # 槽位已归还:非阻塞 acquire 应当立刻成功
            self.assertTrue(
                h.sem.acquire(blocking=False),
                "worker 失败后必须归还槽位,否则后续发布会永久卡住",
            )
            h.sem.release()
        finally:
            self._stop_all(patches, h)

    def test_slot_returned_when_status_write_fails(self):
        """写状态就抛异常(连库都坏)时,槽位依然归还——finally 不能漏。"""
        h = _SlotHarness(slots=1)
        patches = h.patches()
        for p in patches:
            p.start()
        try:
            def broken_status(conn, pub_id, status, **kwargs):
                raise RuntimeError("模拟落库失败")

            patches[1].stop()
            patches = (mock.patch.object(
                publish_service.repo, "update_publication_status", broken_status,
            ),) + patches[1:]
            patches[0].start()

            threads = self._run_workers(["sohu"])
            for t in threads:
                t.join(timeout=5)
            self.assertTrue(
                h.sem.acquire(blocking=False),
                "任何异常路径下槽位都必须归还",
            )
            h.sem.release()
        finally:
            self._stop_all(patches, h)


if __name__ == "__main__":
    unittest.main()
