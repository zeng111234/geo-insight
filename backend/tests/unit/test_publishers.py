"""发布器(002 T019/T020 真实化)的核心断言。

为什么测这三件:
- 标题长度区间是各平台写进 docstring 的硬约束,防呆必须发生在「开浏览器
  之前」——否则错误要到页面报错才发现,浪费一次登录态;
- paste_html 是所有 contenteditable 渠道(知乎/公众号/后续)共用的写入通道,
  它坏了全部渠道跟着坏,所以用无头系统 Chrome 在本地页面上真跑一遍,
  不碰任何平台登录态;
- CLI 包装脚本曾漏加 sys.path(sohu_publish.py 从 backend/ 跑直接
  ModuleNotFoundError),subprocess 跑一次非法子命令,红 = 包装又坏了。
  跑非法子命令只打印 docstring,不会启动浏览器。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from geo.publishers import _base, wechat, zhihu

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _draft(tmp: Path, title: str) -> str:
    p = tmp / "draft.json"
    p.write_text(
        json.dumps({"title": title, "content_html": "<p>测试正文</p>"}, ensure_ascii=False),
        encoding="utf-8",
    )
    return str(p)


class TitleValidationTests(unittest.TestCase):
    """标题校验必须发生在开浏览器之前(返回 rejected,不碰登录态)。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_zhihu_rejects_short_title(self):
        result = zhihu.publish_draft(_draft(self.tmp, "短"))
        self.assertEqual(result["status"], "rejected")
        self.assertIn("2-100", result["reason"])

    def test_zhihu_rejects_long_title(self):
        result = zhihu.publish_draft(_draft(self.tmp, "标" * 101))
        self.assertEqual(result["status"], "rejected")

    def test_wechat_rejects_empty_title(self):
        result = wechat.publish_draft(_draft(self.tmp, ""))
        self.assertEqual(result["status"], "rejected")
        self.assertIn("1-64", result["reason"])

    def test_wechat_rejects_long_title(self):
        result = wechat.publish_draft(_draft(self.tmp, "标" * 65))
        self.assertEqual(result["status"], "rejected")


class PasteHtmlTests(unittest.TestCase):
    """合成 paste 事件在带监听的编辑器(ProseMirror/Quill)上生效;裸
    contenteditable 没有监听,靠 execCommand 退路写入。这里验证的是
    「两条路合起来必然能把 HTML 灌进去且带出文本」这个契约。
    """

    def test_paste_html_writes_contenteditable(self):
        if not Path(_base.CHROME_PATH).exists():
            self.skipTest(f"系统 Chrome 不存在: {_base.CHROME_PATH}")
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=_base.CHROME_PATH, headless=True)
            try:
                page = browser.new_page()
                page.set_content("<div id='ed' contenteditable='true'></div>")
                ok = _base.paste_html(page, "#ed", "<p>hello <b>world</b></p>")
                text = page.locator("#ed").inner_text()
            finally:
                browser.close()
        self.assertTrue(ok, "两条写入路径都失败了")
        self.assertIn("world", text)

    def test_paste_html_reports_missing_element(self):
        if not Path(_base.CHROME_PATH).exists():
            self.skipTest(f"系统 Chrome 不存在: {_base.CHROME_PATH}")
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=_base.CHROME_PATH, headless=True)
            try:
                page = browser.new_page()
                page.set_content("<div id='x'></div>")
                ok = _base.paste_html(page, "#nope", "<p>hi</p>")
            finally:
                browser.close()
        self.assertFalse(ok, "选择器落空时必须报 False,不能假装写入成功")


class CliWrapperTests(unittest.TestCase):
    """包装脚本必须能从 backend/ 直接跑:非法子命令打印 docstring、退出码 1。"""

    def _run(self, script: str):
        return subprocess.run(
            [sys.executable, str(BACKEND_DIR / "scripts" / script), "badcmd"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=BACKEND_DIR,
        )

    def test_sohu_wrapper_resolves_geo_package(self):
        proc = self._run("sohu_publish.py")
        self.assertNotIn("ModuleNotFoundError", proc.stderr + proc.stdout)
        self.assertIn("搜狐号浏览器发布器", proc.stdout)

    def test_zhihu_wrapper_resolves_geo_package(self):
        proc = self._run("zhihu_publish.py")
        self.assertNotIn("ModuleNotFoundError", proc.stderr + proc.stdout)
        self.assertIn("知乎浏览器发布器", proc.stdout)

    def test_wechat_wrapper_resolves_geo_package(self):
        proc = self._run("wechat_publish.py")
        self.assertNotIn("ModuleNotFoundError", proc.stderr + proc.stdout)
        self.assertIn("微信公众号浏览器发布器", proc.stdout)

    def test_douyin_wrapper_resolves_geo_package(self):
        proc = self._run("douyin_publish.py")
        self.assertNotIn("ModuleNotFoundError", proc.stderr + proc.stdout)
        self.assertIn("抖音创作者中心浏览器发布器", proc.stdout)

    def test_toutiao_wrapper_resolves_geo_package(self):
        proc = self._run("toutiao_publish.py")
        self.assertNotIn("ModuleNotFoundError", proc.stderr + proc.stdout)
        self.assertIn("头条号浏览器发布器", proc.stdout)

    def test_xiaohongshu_wrapper_resolves_geo_package(self):
        proc = self._run("xiaohongshu_publish.py")
        self.assertNotIn("ModuleNotFoundError", proc.stderr + proc.stdout)
        self.assertIn("小红书创作服务平台浏览器发布器", proc.stdout)


class CredentialsTests(unittest.TestCase):
    """channels.local.json 读取:缺文件/坏 JSON/坏结构都必须安全返回空并转
    人工扫码——账密文件是用户手写的,不能因为写错格式把登录流程搞崩。
    """

    def test_reads_channel_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "channels.local.json"
            f.write_text(
                json.dumps({"sohu": {"username": "u", "password": "p"}}, ensure_ascii=False),
                encoding="utf-8",
            )
            old = _base.CHANNEL_CREDENTIALS_PATH
            _base.CHANNEL_CREDENTIALS_PATH = f
            try:
                self.assertEqual(_base.credentials("sohu"), {"username": "u", "password": "p"})
                self.assertEqual(_base.credentials("zhihu"), {})
            finally:
                _base.CHANNEL_CREDENTIALS_PATH = old

    def test_missing_or_broken_file_returns_empty(self):
        old = _base.CHANNEL_CREDENTIALS_PATH
        try:
            _base.CHANNEL_CREDENTIALS_PATH = Path("Z:/不存在的目录/channels.local.json")
            self.assertEqual(_base.credentials("sohu"), {})
            with tempfile.TemporaryDirectory() as tmp:
                f = Path(tmp) / "bad.json"
                f.write_text("{broken json", encoding="utf-8")
                _base.CHANNEL_CREDENTIALS_PATH = f
                self.assertEqual(_base.credentials("sohu"), {})
        finally:
            _base.CHANNEL_CREDENTIALS_PATH = old


class PasswordLoginTests(unittest.TestCase):
    """try_password_login:真实无头 Chrome 上验证「找到密码框 → 填账密 →
    点登录」;没有密码框时如实报告,不假装提交。
    """

    FORM = """
    <form id="f">
      <input type="tel" id="acct" placeholder="手机号" />
      <input type="password" id="pwd" />
      <button type="button" id="btn" onclick="window.__clicked=true">登录</button>
    </form>
    """

    def test_fills_and_submits(self):
        if not Path(_base.CHROME_PATH).exists():
            self.skipTest(f"系统 Chrome 不存在: {_base.CHROME_PATH}")
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=_base.CHROME_PATH, headless=True)
            try:
                page = browser.new_page()
                page.set_content(self.FORM)
                result = _base.try_password_login(page, "13800000000", "s3cret!")
                clicked = page.evaluate("window.__clicked === true")
                values = page.evaluate(
                    "({a: document.querySelector('#acct').value, p: document.querySelector('#pwd').value})"
                )
            finally:
                browser.close()
        self.assertEqual(result, "submitted")
        self.assertTrue(clicked, "登录按钮没有被点击")
        self.assertEqual(values["a"], "13800000000")
        self.assertEqual(values["p"], "s3cret!")

    def test_reports_missing_password_field(self):
        if not Path(_base.CHROME_PATH).exists():
            self.skipTest(f"系统 Chrome 不存在: {_base.CHROME_PATH}")
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=_base.CHROME_PATH, headless=True)
            try:
                page = browser.new_page()
                page.set_content("<input type='text' />")
                result = _base.try_password_login(page, "u", "p")
            finally:
                browser.close()
        self.assertEqual(result, "no-password-field")


if __name__ == "__main__":
    unittest.main()
