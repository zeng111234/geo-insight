# -*- coding: utf-8 -*-
"""微信公众号浏览器发布器(Playwright 驱动系统 Chrome,持久化登录态)。

路线与取舍(同 sohu.py):真实页面交互,不走私有接口。

为什么 publish 默认只「保存为草稿」而不是直接群发:
- 订阅号群发(1 次/天)点「发表」后要管理员在手机上做安全验证,
  这是平台设计的外部步骤,自动化到这一步必须人来;
- 保存草稿是纯网页操作,稳定可验。要走到群发,draft.json 加 "send": true
  (强烈建议 --show,盯着管理员验证弹窗走完)。

API 备选(本工具未走):认证公众号可用官方「草稿箱 draft/add + 发布
freepublish」接口,免浏览器;个人订阅号没有这两个接口的权限。需要时另立模块,
别往这里塞——两条路线的密钥管理与失败语义完全不同。

入口(2026-11-24 实测登录页):
- 登录页 https://mp.weixin.qq.com/ (扫码或账号密码;登录后跳 /cgi-bin/homepage)
- 写文路径:后台 → 草稿箱 →「新的创作」→「写新图文」,编辑器在新标签页打开

已知约束(公开资料,首次发布用 --show 校准):
- 标题 ≤ 64 字;摘要 ≤ 120 字(digest 字段暂不自动填,发布后在后台补)
- 群发必须有封面图(可从正文首图裁);保存草稿不强制
- 订阅号 1 次/天,服务号 4 次/天

用法:
    python scripts/wechat_publish.py login
    python scripts/wechat_publish.py check
    python scripts/wechat_publish.py publish draft.json [--show | --headless]
    draft.json: { "title": "...", "content_html": "...",
                  "send": false, "wait_seconds": 30 }
    账密可选:data/channels.local.json 写
    {"wechat": {"username": "...", "password": "..."}},login 时自动填表,验证转人工
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

LOGIN_URL = "https://mp.weixin.qq.com/"

# 登录态判据:登录页就是根 URL 本身,登录成功必然落在 /cgi-bin/ 下(实测登录页
# 的跳转行为)。编辑器 DOM 为「公开资料」(登录墙后无法离线核实),首跑 --show。
SELECTORS = {
    "draft_box_menu": "a:has-text('草稿箱'), li:has-text('草稿箱')",
    "new_creation": "button:has-text('新的创作'), a:has-text('新的创作')",
    "write_article": "text=写新图文, text=图文",
    "title_input": "textarea[placeholder*='标题'], input[placeholder*='标题']",
    "editor": "div[contenteditable='true']",
    "save_draft_button": "button:has-text('保存为草稿'), a:has-text('保存为草稿')",
    "send_button": "button:has-text('发表')",
    # 群发确认链路上的弹窗(管理员扫码验证在手机上,不在页面里)
    "send_confirm": "button:has-text('群发'), a:has-text('群发')",
    "success_toast": "text=保存成功, text=已保存",
}


def login_flow() -> bool:
    """打开公众号登录页,等用户扫码完成,会话持久化在 profile。

    登录成功的判据是 URL 进入 /cgi-bin/(扫码确认后跳后台首页)。轮询检测,
    一跳转立刻关窗存会话,最长等 5 分钟——不在终端里按回车,扫完即走。
    """
    with sync_playwright() as pw:
        ctx = launch(pw, "wechat", headless=False)
        page = ctx.new_page()
        page.goto(LOGIN_URL)
        print("请在打开的浏览器窗口用微信扫码登录(可能需要手机确认)。")
        print("登录成功后窗口会自动关闭并验证(最长等 5 分钟)。")
        # 有本地账密就先自动填表(「使用账号登录」;验证/短信转人工)
        creds = credentials("wechat")
        if creds.get("username") and creds.get("password"):
            try:
                switch_to_password_login(page)
                result = try_password_login(page, creds["username"], creds["password"])
                print(f"已尝试自动登录(表单结果: {result});弹验证时请在窗口里人工完成。")
            except Exception:  # noqa: BLE001  # 自动登录走不通不阻塞,转人工扫码
                print("自动登录未走通,请在窗口里扫码登录。")
        else:
            print('提示:配置 data/channels.local.json {"wechat": {"username": ..., "password": ...}} 可自动登录。')
        deadline = time.time() + 300
        try:
            while time.time() < deadline:
                page.wait_for_timeout(5000)
                if "/cgi-bin/" in page.url:
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
    """打开首页:登录页就是根 URL,登录态有效则必然已跳到 /cgi-bin/ 下。"""
    with sync_playwright() as pw:
        ctx = launch(pw, "wechat", headless=True)
        page = ctx.new_page()
        try:
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(4000)
            url = page.url
            ok = "/cgi-bin/" in url
            print(f"URL: {url}")
            print("登录态: " + ("有效 ✓" if ok else "已失效(停在登录页),请重新运行 login"))
            return ok
        finally:
            ctx.close()


def _open_editor(ctx) -> Any:
    """后台首页 → 草稿箱 → 新的创作 → 写新图文。编辑器在新标签页打开,
    返回编辑器所在的 page;打不开返回 None(选择器疑似改版)。"""
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(4000)
    if "/cgi-bin/" not in page.url:
        return None  # 登录失效(停在登录页);调用方用首页 URL 区分两种失败
    try:
        page.click(SELECTORS["draft_box_menu"], timeout=10000)
        page.wait_for_timeout(2000)
        page.click(SELECTORS["new_creation"], timeout=10000)
        page.click(SELECTORS["write_article"], timeout=10000)
    except Exception as e:  # noqa: BLE001
        print(f"导航到编辑器失败(菜单选择器可能改版): {e}")
        return None
    page.wait_for_timeout(4000)  # 等新标签页
    return ctx.pages[-1] if len(ctx.pages) > 1 else page


def publish_draft(draft_path: str, headless: bool = False) -> dict[str, Any]:
    """把一篇图文写进公众号。draft.json 结构见模块 docstring。
    默认保存为草稿(send=false);send=true 走发表链路,管理员验证在手机上,
    必须人工完成。选择器未实测校准前默认显示浏览器。
    """
    draft = json.loads(Path(draft_path).read_text(encoding="utf-8"))
    title = draft.get("title", "")
    if not 1 <= len(title) <= 64:
        return {"status": "rejected", "reason": f"标题长度 {len(title)} 不在 1-64 字区间"}

    send = bool(draft.get("send", False))
    with sync_playwright() as pw:
        ctx = launch(pw, "wechat", headless=headless)
        try:
            editor = _open_editor(ctx)
            if editor is None:
                # 区分两种失败:登录失效 vs 菜单/编辑器选择器改版
                first = ctx.pages[0] if ctx.pages else ctx.new_page()
                if "/cgi-bin/" not in first.url:
                    return {"status": "not_logged_in", "reason": "登录态失效,先运行 login"}
                return {
                    "status": "rejected",
                    "reason": "未能打开图文编辑器:菜单选择器疑似改版,请 --show 打开浏览器核对 SELECTORS",
                }
            editor.wait_for_load_state("domcontentloaded")
            editor.wait_for_timeout(3000)

            # 1. 标题 + 正文
            editor.fill(SELECTORS["title_input"], title)
            if not paste_html(editor, SELECTORS["editor"], draft.get("content_html", "")):
                return {
                    "status": "rejected",
                    "reason": "正文写入失败:编辑器选择器疑似改版,请 --show 打开浏览器核对 SELECTORS",
                }

            # 2. 默认保存草稿;send=true 时走发表(群发前可能弹确认,逐个点掉)
            if not send:
                editor.click(SELECTORS["save_draft_button"])
                editor.wait_for_timeout(int(draft.get("wait_seconds", 30)) * 1000)
                saved = editor.locator(SELECTORS["success_toast"]).count() > 0
                return {
                    "status": "draft_saved" if saved else "unknown",
                    "final_url": editor.url,
                    "note": "unknown 时请到草稿箱确认;宁可不报成功,不伪造成功",
                }

            editor.click(SELECTORS["send_button"])
            try:
                editor.click(SELECTORS["send_confirm"], timeout=8000)
            except Exception:  # noqa: BLE001  # 无确认弹窗就直接等结果
                pass
            print(">> 若弹出管理员安全验证,请在手机上完成确认;工具不做任何绕过。")
            editor.wait_for_timeout(int(draft.get("wait_seconds", 60)) * 1000)
            final_url = editor.url
            # 群发结果的页面形态未经实测,拿不准就报 unknown,人工到后台确认
            return {
                "status": "unknown",
                "final_url": final_url,
                "note": "群发是否成功请到公众号后台「发表记录」人工确认;宁可不报成功,不伪造成功",
            }
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
            print("用法: python scripts/wechat_publish.py publish draft.json [--show | --headless]")
            return 1
        headless = "--headless" in sys.argv[3:]
        result = publish_draft(sys.argv[2], headless=headless)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] in ("draft_saved", "published") else 1
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
