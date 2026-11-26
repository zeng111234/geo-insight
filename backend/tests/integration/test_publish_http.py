"""发布通道真实化的 HTTP 行为(决策 011)。

TestClient 走真实 ASGI 栈(中间件/鉴权/路由都过一遍),子进程出口
``publish_service._run_cli`` 与登录派发 ``start_login`` 全部换成假实现,
不打真浏览器。要验证的是接口语义,不是平台行为:

- GET /v1/channels 只给诚实初始态(不再有假「已连接」)
- connect:派生登录(假实现同步落库)、not_implemented 400、busy 409
- check:真实检测(假实现)结果落库并回显
- publish:worker 开启时 202 后异步走到终态;关闭时纯入队;
  未连接 400、标题超限 400、busy 409、重试不重复建行
"""

import json
import time
import unittest
from contextlib import closing
from unittest import mock

try:
    from starlette.testclient import TestClient
except ModuleNotFoundError:  # 空 venv 里没有依赖,整模块跳过而不是报错
    raise unittest.SkipTest("环境里没有 starlette,跳过 HTTP 层测试(用系统 python 跑全套)")

from geo import db, seed
from geo.api.app import app
from geo.repositories import sqlite_repository as repo
from geo.services import publish_service
from geo.settings import settings

FORM = {"Content-Type": "application/x-www-form-urlencoded"}
PLACEHOLDER_BODY = "正文占位"


def _fake_cli_result(payload: dict, returncode: int = 0):
    class P:
        pass

    p = P()
    p.stdout = json.dumps(payload, ensure_ascii=False, indent=2)
    p.stderr = ""
    p.returncode = returncode
    return p


@unittest.skipUnless(settings.auth_required, "GEO_AUTH_REQUIRED=0,鉴权被显式关闭")
class PublishHttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed.seed()  # 诚实渠道初始态(决策 011)
        with closing(db.connect()) as conn:
            from geo.services import auth_service

            auth_service.ensure_admin_password(conn)
        cls.client = TestClient(app)
        body = f"email={settings.admin_email}&password={settings.admin_password}"
        cls.client.post("/login", content=body, headers=FORM, follow_redirects=False)
        # CSRF(决策 011 之后所有写请求都要带头):登录下发的 geo_csrf 挂成默认头,
        # 单个用例就不用每处都写 headers 了
        cls.client.headers.update({"X-CSRF-Token": cls.client.cookies.get("geo_csrf", "")})

    # ------------------------------------------------------------------
    # 渠道列表:诚实初始态
    # ------------------------------------------------------------------

    def test_channels_list_honest_initial_states(self):
        response = self.client.get("/v1/channels")

        self.assertEqual(response.status_code, 200)
        items = response.json()["items"]
        self.assertEqual(len(items), 7)
        by_key = {c["platform_key"]: c for c in items}
        # 三个浏览器渠道:未连接、从未检测
        for key in ("wechat", "zhihu", "sohu"):
            self.assertEqual(by_key[key]["status"], "disconnected", key)
            self.assertIsNone(by_key[key]["account_name"], f"{key} 不该再挂假账号名")
            self.assertIsNone(by_key[key]["last_checked_at"])
        self.assertEqual(by_key["baijiahao"]["status"], "needs_config")
        # 决策 011 第二批:三渠道发布器上线,初始态 disconnected
        for key in ("xiaohongshu", "toutiao", "douyin"):
            self.assertEqual(by_key[key]["status"], "disconnected", key)
        # 新字段在响应里(前端要显示检测附注)
        self.assertIn("last_check_note", by_key["sohu"])

    # ------------------------------------------------------------------
    # connect:真实派发语义
    # ------------------------------------------------------------------

    def test_connect_unknown_channel_id_is_404(self):
        response = self.client.post("/v1/channels/00000000-0000-0000-0000-0000000000ff/connect")

        self.assertEqual(response.status_code, 404)

    def test_connect_baijiahao_points_to_key_config(self):
        bjh = self._channel("baijiahao")
        response = self.client.post(f"/v1/channels/{bjh['id']}/connect")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "NEEDS_CONFIG")
        self.assertIn("baijiahao.local.json", response.json()["message"])

    def test_connect_spawns_login_and_status_flows_to_connected(self):
        wechat = self._channel("wechat")

        def fake_start_login(channel):
            # 模拟监控线程收尾:扫码成功 → connected,并释放端点持有的锁
            with closing(db.connect()) as conn:
                repo.update_channel_status(
                    conn, channel["id"], "connected",
                    "2026-11-24T12:00:00+08:00",
                    login_mode="browser", note="登录成功(测试模拟)",
                )
            publish_service.release_channel_lock(channel["platform_key"])

        with mock.patch.object(publish_service, "start_login", fake_start_login):
            response = self.client.post(f"/v1/channels/{wechat['id']}/connect")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["status"], "connecting")
        self.assertEqual(response.json()["integration"], "real")
        updated = self._channel("wechat")
        self.assertEqual(updated["status"], "connected")
        self.assertEqual(updated["login_mode"], "browser")
        self.assertIn("登录成功", updated["last_check_note"])

    def test_connect_in_test_mode_twice_does_not_leak_lock(self):
        """测试模式(worker 关闭)下 connect 拿锁后无 monitor 收尾,必须自己归还。

        否则第一次 skipped 后渠道锁永久占用,第二次同渠道 connect/check 全 409。
        """
        wechat = self._channel("wechat")
        old = settings.publish_worker_enabled
        object.__setattr__(settings, "publish_worker_enabled", False)
        try:
            for i in range(2):
                resp = self.client.post(f"/v1/channels/{wechat['id']}/connect")
                self.assertEqual(resp.status_code, 202, f"第 {i+1} 次 connect 不该被锁挡住")
                self.assertEqual(resp.json()["status"], "skipped")
            self.assertFalse(publish_service.channel_busy("wechat"))
        finally:
            object.__setattr__(settings, "publish_worker_enabled", old)

    def test_connect_while_busy_is_409(self):
        self.assertTrue(publish_service.try_channel_lock("zhihu"))
        try:
            zhihu = self._channel("zhihu")
            response = self.client.post(f"/v1/channels/{zhihu['id']}/connect")
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["code"], "CHANNEL_BUSY")
        finally:
            publish_service.release_channel_lock("zhihu")

    # ------------------------------------------------------------------
    # check:真实检测落库
    # ------------------------------------------------------------------

    def test_check_updates_status_and_note(self):
        sohu = self._channel("sohu")

        def fake_check(channel):
            return True, "登录态: 有效 ✓(测试模拟)"

        with mock.patch.object(publish_service, "run_channel_check", fake_check):
            response = self.client.post(f"/v1/channels/{sohu['id']}/check")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["reachable"])
        self.assertEqual(payload["status"], "connected")
        self.assertEqual(payload["login_mode"], "browser")
        updated = self._channel("sohu")
        self.assertEqual(updated["status"], "connected")
        self.assertIsNotNone(updated["last_checked_at"])
        self.assertIn("测试模拟", updated["last_check_note"])

    def test_check_failure_on_never_connected_marks_disconnected(self):
        zhihu = self._channel("zhihu")

        def fake_check(channel):
            return False, "登录态: 已失效(测试模拟)"

        with mock.patch.object(publish_service, "run_channel_check", fake_check):
            response = self.client.post(f"/v1/channels/{zhihu['id']}/check")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["reachable"])
        self.assertEqual(self._channel("zhihu")["status"], "disconnected")

    # ------------------------------------------------------------------
    # publish:派发与终态
    # ------------------------------------------------------------------

    def _connect_channel_in_db(self, key: str) -> dict:
        ch = self._channel(key)
        with closing(db.connect()) as conn:
            repo.update_channel_status(
                conn, ch["id"], "connected", "2026-11-24T12:00:00+08:00",
                login_mode="browser", note="已连接(测试布置)",
            )
        return self._channel(key)

    def test_publish_dispatches_worker_to_terminal_state(self):
        wechat = self._connect_channel_in_db("wechat")

        def fake_run_cli(script, *args, timeout=None):
            return _fake_cli_result({
                "status": "draft_saved",
                "final_url": "https://mp.weixin.qq.com/s/fake-1",
            })

        with mock.patch.object(publish_service, "_run_cli", fake_run_cli):
            response = self.client.post(
                f"/v1/articles/{seed.ART_DRAFT}/publish",
                json={"channel_ids": [wechat["id"]]},
            )
            self.assertEqual(response.status_code, 202)
            payload = response.json()
            self.assertEqual(payload["integration"], "real")
            self.assertTrue(payload["worker"])
            self.assertEqual(len(payload["created"]), 1)

            final = self._wait_publication(seed.ART_DRAFT, "wechat", timeout=10)

        self.assertEqual(final["status"], "draft_saved")
        self.assertEqual(final["published_url"], "https://mp.weixin.qq.com/s/fake-1")

    def test_publish_worker_failure_records_reason(self):
        zhihu = self._connect_channel_in_db("zhihu")

        def fake_run_cli(script, *args, timeout=None):
            return _fake_cli_result({
                "status": "blocked",
                "reason": "账号未实名认证:发文章被锁(测试模拟)",
            })

        with mock.patch.object(publish_service, "_run_cli", fake_run_cli):
            response = self.client.post(
                f"/v1/articles/{seed.ART_DRAFT}/publish",
                json={"channel_ids": [zhihu["id"]]},
            )
            self.assertEqual(response.status_code, 202)
            final = self._wait_publication(seed.ART_DRAFT, "zhihu", timeout=10)

        self.assertEqual(final["status"], "blocked")
        self.assertIn("未实名", final["failure_reason"])

    def test_publish_with_worker_disabled_stays_queued(self):
        sohu = self._connect_channel_in_db("sohu")
        old = settings.publish_worker_enabled
        object.__setattr__(settings, "publish_worker_enabled", False)
        try:
            response = self.client.post(
                f"/v1/articles/{seed.ART_REVIEW}/publish",
                json={"channel_ids": [sohu["id"]]},
            )
            self.assertEqual(response.status_code, 202)
            payload = response.json()
            self.assertFalse(payload["worker"])
            self.assertIn("测试模式", payload["note"])
        finally:
            object.__setattr__(settings, "publish_worker_enabled", old)
        final = self._wait_publication(seed.ART_REVIEW, "sohu", timeout=5, expect_terminal=False)
        self.assertEqual(final["status"], "queued")

    def test_repeated_publish_of_existing_does_not_leak_channel_lock(self):
        """重复发布(existing)时端点拿了锁但没有 worker 释放——必须自己归还。

        否则用户以为没生效、再点一次发布,返回 202 却让该渠道永久 409。
        """
        wechat = self._connect_channel_in_db("wechat")

        try:
            # 先清掉可能残留的同文章+渠道记录,再手动建一条 published,
            # 构造"全 existing"场景——完全自包含,不依赖用例执行顺序。
            with closing(db.connect()) as conn:
                conn.execute(
                    "DELETE FROM publication WHERE article_id = ? AND channel_id = ?",
                    (seed.ART_DRAFT, wechat["id"]),
                )
                conn.commit()
                created, _, _ = repo.create_publications(
                    conn, seed.ART_DRAFT, [wechat], "2026-11-26T12:00:00+08:00"
                )
                repo.update_publication_status(conn, created[0], "published")

            # POST:create_publications 把这条归入 existing,不派 worker。
            # 端点拿了锁却没有 worker 收尾,必须自己归还。
            second = self.client.post(
                f"/v1/articles/{seed.ART_DRAFT}/publish",
                json={"channel_ids": [wechat["id"]]},
            )
            self.assertEqual(second.status_code, 202)
            self.assertEqual(len(second.json()["existing"]), 1)
            self.assertEqual(len(second.json()["created"]), 0)
            self.assertFalse(
                publish_service.channel_busy("wechat"),
                "全 existing 的重复发布不该让渠道锁永久占用",
            )
            # 后续同渠道请求仍能正常发起(不会被 409)
            third = self.client.post(
                f"/v1/articles/{seed.ART_DRAFT}/publish",
                json={"channel_ids": [wechat["id"]]},
            )
            self.assertEqual(third.status_code, 202)
        finally:
            with closing(db.connect()) as conn:
                conn.execute(
                    "DELETE FROM publication WHERE article_id = ? AND channel_id = ?",
                    (seed.ART_DRAFT, wechat["id"]),
                )
                conn.commit()

    def test_publish_to_disconnected_channel_is_400(self):
        xhs = self._channel("xiaohongshu")
        response = self.client.post(
            f"/v1/articles/{seed.ART_DRAFT}/publish",
            json={"channel_ids": [xhs["id"]]},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "CHANNEL_NOT_CONNECTED")

    def test_publish_title_out_of_range_is_400_before_any_browser(self):
        wechat = self._connect_channel_in_db("wechat")
        # 把草稿标题改超公众号 64 字上限:必须在派发前被拒,而不是浏览器跑一半
        long_title = "标" * 70
        response = self.client.patch(
            f"/v1/articles/{seed.ART_DRAFT}",
            json={"title": long_title, "body": PLACEHOLDER_BODY},
        )

        self.assertEqual(response.status_code, 200)
        response = self.client.post(
            f"/v1/articles/{seed.ART_DRAFT}/publish",
            json={"channel_ids": [wechat["id"]]},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("1-64", response.json()["blocked"][0]["reason"])

        # 还原,别污染同库的其他用例
        self.client.patch(
            f"/v1/articles/{seed.ART_DRAFT}",
            json={"title": "取保候审的适用条件与常见误区", "body": "正文"},
        )

    def test_publish_while_channel_busy_is_409(self):
        wechat = self._connect_channel_in_db("wechat")
        self.assertTrue(publish_service.try_channel_lock("wechat"))
        try:
            response = self.client.post(
                f"/v1/articles/{seed.ART_REVIEW}/publish",
                json={"channel_ids": [wechat["id"]]},
            )
            self.assertEqual(response.status_code, 409)
        finally:
            publish_service.release_channel_lock("wechat")

    # ------------------------------------------------------------------
    # 启动清扫(repo 级;create_app 时的调用在 app.py 里)
    # ------------------------------------------------------------------

    def test_sweep_marks_stale_running_as_failed(self):
        wechat = self._connect_channel_in_db("wechat")
        with closing(db.connect()) as conn:
            # ART_REVIEW × wechat 在本模块还没有发布记录,可安全新建
            created, _, _ = repo.create_publications(
                conn, seed.ART_REVIEW, [wechat], "2026-11-24T13:00:00+08:00"
            )
            repo.update_publication_status(conn, created[0], "running")
            swept = repo.sweep_stale_publications(conn, "服务重启,发布中断;请重新发布")

            row = conn.execute(
                "SELECT status, failure_reason FROM publication WHERE id = ?", (created[0],)
            ).fetchone()
        self.assertEqual(swept, 1)
        self.assertEqual(row["status"], "failed")
        self.assertIn("服务重启", row["failure_reason"])

    def test_sweep_marks_stale_queued_as_failed(self):
        """闸门让记录可能在 queued 排队很久,重启后没人推进,同样要说清是中断。"""
        with closing(db.connect()) as conn:
            # 用 zhihu 渠道新建一条,与上面的 running 用例不共用同一行
            zhihu = self._connect_channel_in_db("zhihu")
            created, _, _ = repo.create_publications(
                conn, seed.ART_REVIEW, [zhihu], "2026-11-24T13:05:00+08:00"
            )
            new_id = created[0]
            # 建出来就是 queued。不清精确条数:前面的用例可能留下别的 queued,
            # 那些也该被同一场清扫带走——断言只盯自己新建的这一行。
            repo.sweep_stale_publications(conn, "服务重启,排队中断;请重新发布")

            row = conn.execute(
                "SELECT status, failure_reason FROM publication WHERE id = ?", (new_id,)
            ).fetchone()
        self.assertEqual(row["status"], "failed")
        self.assertIn("排队中断", row["failure_reason"])

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------

    def _channel(self, key: str) -> dict:
        response = self.client.get("/v1/channels")
        for c in response.json()["items"]:
            if c["platform_key"] == key:
                return c
        raise AssertionError(f"渠道 {key} 不在列表里")

    def _wait_publication(self, article_id: str, platform_key: str,
                          timeout: float, expect_terminal: bool = True) -> dict:
        """轮询文章详情直到该渠道的 publication 到终态(或确认停在非终态)。"""
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            article = self.client.get(f"/v1/articles/{article_id}").json()
            for p in article["publications"]:
                if p["platform_key"] == platform_key:
                    last = p
                    if not expect_terminal or p["status"] not in ("queued", "running"):
                        return p
            time.sleep(0.2)
        self.fail(f"等待 {platform_key} 的发布记录终态超时:最后状态 {last}")


if __name__ == "__main__":
    unittest.main()
