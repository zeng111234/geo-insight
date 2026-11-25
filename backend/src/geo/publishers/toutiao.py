# -*- coding: utf-8 -*-
"""头条号浏览器发布器(Playwright 驱动系统 Chrome,持久化登录态)。

路线与取舍(同 sohu.py / 决策 011):真实页面交互,不做任何风控绕过。

⚠️ 网络状态(2026-11-24 实测):本机对 toutiao.com 全域 TLS 被重置(主站与
mp 后台一致,换 IP 无效;同为字节的抖音正常)。模块按公开资料写好,登录与
发布要等网络通了才能校准——check 会如实把连接错误当附注报出来,不装有效。

入口与形态(公开资料,待网络恢复后实测校准):
- 头条号后台 https://mp.toutiao.com/ (扫码或手机号登录)
- 发文走后台的「文章」入口,编辑器为富文本(头条自研)
- 标题 5-30 字;封面非必填;正文富文本

用法:
    python scripts/toutiao_publish.py login
    python scripts/toutiao_publish.py check
    python scripts/toutiao_publish.py publish draft.json [--show | --headless]
    账密可选:data/channels.local.json 写
    {"toutiao": {"username": "...", "password": "..."}},login 时自动填表,验证转人工
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from geo.publishers._base import (
    credentials,
    ensure_utf8,
    launch,
    paste_html,
    switch_to_password_login,
    try_password_login,
)

ensure_utf8()

LOGIN_URL = "https://mp.toutiao.com/"

SELECTORS = {
    # 「公开资料」:头条号编辑器,网络通了之后登录实测校准
    "title_input": "textarea[placeholder*='标题'], input[placeholder*='标题']",
    "editor": "div[contenteditable='true'], .ql-editor, .ProseMirror",
    "publish_button": "button:has-text('发布')",
    "confirm_dialog": ".modal, .dialog",
    "confirm_ok": "button:has-text('确')",
}


def login_flow() -> bool:
    """打开头条号后台,等用户登录完成,会话持久化在 profile。

    登录成功判据:URL 出现 profile/后台路径(登录页停在根路径)。轮询检测,
    一跳转立刻关窗存会话,最长等 5 分钟——扫完即走。
    """
    with sync_playwright() as pw:
        ctx = launch(pw, "toutiao", headless=False)
        page = ctx.new_page()
        page.goto(LOGIN_URL)
        print("请在打开的浏览器窗口完成登录(扫码或手机号)。")
        print("登录成功后窗口会自动关闭并验证(最长等 5 分钟)。")
        creds = credentials("toutiao")
        if creds.get("username") and creds.get("password"):
            try:
                switch_to_password_login(page)
                result = try_password_login(page, creds["username"], creds["password"])
                print(f"已尝试自动登录(表单结果: {result});弹验证码时请在窗口里人工完成。")
            except Exception:  # noqa: BLE001  # 自动登录走不通不阻塞,转人工扫码
                print("自动登录未走通,请在窗口里扫码登录。")
        deadline = time.time() + 300
        try:
            while time.time() < deadline:
                page.wait_for_timeout(5000)
                # 登录后进入后台(home/profile 路径);登录页停在根路径
                if any(k in page.url for k in ("profile", "home", "main")):
                    print(f"检测到登录跳转: {page.url}")
                    page.wait_for_timeout(3000)  # 等会话 cookie 落定
                    break
            else:
                print("等待超时(5 分钟),按当前会话状态收尾。")
        except Exception:  # noqa: BLE001  # 用户手动关窗 → TargetClosedError,不崩
            print(">> 浏览器窗口被关闭;若登录已完成,会话已存,继续验证。")
        finally:
            ctx.close()
    return check_login()


def check_login() -> bool:
    """打开头条号后台:已登录会进入 profile/后台路径并出现「发文」入口。

    2026-11-24 实测:本机对 toutiao.com TLS 被重置,这里会把连接错误如实
    报出来(附注里带原因),不装有效。网络恢复后此判据需登录实测校准。
    """
    with sync_playwright() as pw:
        ctx = launch(pw, "toutiao", headless=True)
        page = ctx.new_page()
        try:
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(6000)
            url = page.url
            title = page.title()
            has_backend = page.locator("text=发布, text=发文, text=创作").count() > 0
            ok = any(k in url for k in ("profile", "home", "main")) and has_backend
            print(f"URL: {url}")
            print(f"标题: {title}")
            print("登录态: " + ("有效 ✓(后台可达)" if ok else "已失效或不可达(见上),请先确认网络再 login"))
            return ok
        except Exception as e:  # noqa: BLE001  # 网络被重置会走到这里,如实报告
            print(f"访问失败: {type(e).__name__}: {e}")
            print("登录态: 不可判断(本机到 toutiao.com 的连接被重置,先修网络)")
            return False
        finally:
            ctx.close()


def publish_draft(draft_path: str, headless: bool = False) -> dict[str, Any]:
    """发布一篇文章。draft.json 结构:
    {"title": "5-30 字标题", "content_html": "...", "wait_seconds": 45}

    选择器按公开资料编写,网络通了之后首跑 --show 校准。
    """
    draft = json.loads(Path(draft_path).read_text(encoding="utf-8"))
    title = draft.get("title", "")
    if not 5 <= len(title) <= 30:
        return {"status": "rejected", "reason": f"标题长度 {len(title)} 不在 5-30 字区间(头条号)"}

    with sync_playwright() as pw:
        ctx = launch(pw, "toutiao", headless=headless)
        page = ctx.new_page()
        try:
            # 从后台首页走「文章」入口(编辑器直连 URL 未验证,按 UI 导航更稳)
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)
            if not any(k in page.url for k in ("profile", "home", "main")):
                return {"status": "not_logged_in", "reason": "登录态失效或网络不可达,先运行 login"}
            try:
                page.click("text=文章, text=发文, text=写文章", timeout=10000)
                page.wait_for_timeout(5000)
            except Exception as e:  # noqa: BLE001
                return {"status": "rejected", "reason": f"「文章」入口未找到(选择器待校准): {e}"}

            # 1. 标题
            try:
                page.wait_for_selector(SELECTORS["title_input"], timeout=15000)
                page.fill(SELECTORS["title_input"], title)
            except Exception as e:  # noqa: BLE001
                return {"status": "rejected", "reason": f"标题输入框未找到(选择器待校准): {e}"}

            # 2. 正文
            if not paste_html(page, SELECTORS["editor"], draft.get("content_html", "")):
                return {
                    "status": "rejected",
                    "reason": "正文写入失败:编辑器选择器疑似不匹配,请 --show 核对 SELECTORS",
                }

            # 3. 发布
            try:
                page.click(SELECTORS["publish_button"], timeout=10000)
            except Exception as e:  # noqa: BLE001
                return {"status": "rejected", "reason": f"发布按钮未找到(选择器待校准): {e}"}
            confirm = page.locator(SELECTORS["confirm_dialog"])
            if confirm.count() > 0:
                page.click(SELECTORS["confirm_ok"])
            page.wait_for_timeout(int(draft.get("wait_seconds", 45)) * 1000)

            final_url = page.url
            published = any(k in final_url for k in ("article", "post", "detail"))
            return {
                "status": "published" if published else "unknown",
                "final_url": final_url,
                "note": "unknown 时请到头条号后台确认;宁可不报成功,不伪造成功",
            }
        except Exception as e:  # noqa: BLE001  # 网络被重置会走到这里
            return {"status": "failed", "reason": f"访问失败(本机到 toutiao.com 被重置?): {type(e).__name__}: {e}"}
        finally:
            ctx.close()


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "login":
        ok = login_flow()
        return 0 if ok else 1
    if cmd == "check":
        return 0 if check_login() else 1
    if cmd == "publish":
        if len(sys.argv) < 3:
            print("用法: python scripts/toutiao_publish.py publish draft.json [--show | --headless]")
            return 1
        headless = "--headless" in sys.argv[3:]
        result = publish_draft(sys.argv[2], headless=headless)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "published" else 1
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
