# -*- coding: utf-8 -*-
"""抖音创作者中心浏览器发布器(Playwright 驱动系统 Chrome,持久化登录态)。

路线与取舍(同 sohu.py / 决策 011):真实页面交互,不做任何风控绕过。

形态说明(重要,如实):
- 抖音是视频/图文平台,没有「纯文字文章」。本工具走「图文」通道:
  draft.json 必须带 images(本地图片路径列表,至少一张),没有图在开浏览器
  之前就拒绝。文章正文进图文的文字部分;标题以平台当前限制为准(约 ≤ 55 字)。
  工作室的纯文字文章要发抖音,需要先配封面图——这是内容素材问题,不是代码问题。

入口(2026-11-24 实测):
- https://creator.douyin.com/ 可达,未登录显示二维码(实测)
- 发布页与编辑器选择器为「公开资料」,登录后需 --show 校准

用法:
    python scripts/douyin_publish.py login
    python scripts/douyin_publish.py check
    python scripts/douyin_publish.py publish draft.json [--show | --headless]
    draft.json: {"title": "...", "content_html": "...",
                 "images": ["D:/pics/cover.jpg", ...], "wait_seconds": 30}
    账密可选:data/channels.local.json 写
    {"douyin": {"username": "...", "password": "..."}},login 时自动填表,验证转人工
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

LOGIN_URL = "https://creator.douyin.com/"
# 公开资料:图文发布页;登录后首次发布建议 --show 校准
PUBLISH_URL = "https://creator.douyin.com/creator-micro/content/publish"

SELECTORS = {
    # 「公开资料」:图文编辑器,登录墙后无法离线核实,--show 校准
    "title_input": "input[placeholder*='标题'], .editor-comp-title-input, textarea[placeholder*='标题']",
    "editor": "div[contenteditable='true'], textarea[placeholder*='正文'], .ql-editor",
    "file_input": "input[type='file']",
    "publish_button": "button:has-text('发布')",
    "success_toast": "text=发布成功, text=已发布",
}


def login_flow() -> bool:
    """打开创作者中心,等用户扫码完成,会话持久化在 profile。

    登录成功判据:URL 离开根路径进入 creator-micro 后台。轮询检测,
    一跳转立刻关窗存会话,最长等 5 分钟——扫完即走。
    """
    with sync_playwright() as pw:
        ctx = launch(pw, "douyin", headless=False)
        page = ctx.new_page()
        page.goto(LOGIN_URL)
        print("请在打开的浏览器窗口用抖音 App 扫码登录。")
        print("登录成功后窗口会自动关闭并验证(最长等 5 分钟)。")
        creds = credentials("douyin")
        if creds.get("username") and creds.get("password"):
            try:
                switch_to_password_login(page)
                result = try_password_login(page, creds["username"], creds["password"])
                print(f"已尝试自动登录(表单结果: {result});弹验证时请在窗口里人工完成。")
            except Exception:  # noqa: BLE001  # 自动登录走不通不阻塞,转人工扫码
                print("自动登录未走通,请在窗口里扫码登录。")
        deadline = time.time() + 300
        try:
            while time.time() < deadline:
                page.wait_for_timeout(5000)
                if "creator-micro" in page.url:
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
    """打开创作者中心:已登录会进入 creator-micro 后台并出现「发布」入口。

    判据是「URL 进入后台 + 页面有发布入口」的组合(登录页本身也停在根路径,
    单看 URL 不够)。选择器待登录后校准,宁可误报失效也不误报有效。
    """
    with sync_playwright() as pw:
        ctx = launch(pw, "douyin", headless=True)
        page = ctx.new_page()
        try:
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(6000)
            url = page.url
            title = page.title()
            has_publish = page.locator("text=发布").count() > 0
            ok = "creator-micro" in url and has_publish
            print(f"URL: {url}")
            print(f"标题: {title}")
            print("登录态: " + ("有效 ✓(后台可达)" if ok else "已失效(停在登录页),请重新运行 login"))
            return ok
        finally:
            ctx.close()


def publish_draft(draft_path: str, headless: bool = False) -> dict[str, Any]:
    """发布一篇图文。draft.json 结构见模块 docstring。

    抖图文 = 图片 + 标题 + 文字:images 缺失或文件不存在在开浏览器前拒绝。
    选择器未校准前默认显示浏览器(--show 同义),校准后可 --headless。
    """
    draft = json.loads(Path(draft_path).read_text(encoding="utf-8"))
    title = draft.get("title", "")
    if not 1 <= len(title) <= 55:
        return {"status": "rejected", "reason": f"标题长度 {len(title)} 不在 1-55 字区间(抖音图文)"}
    images = draft.get("images") or []
    if not images:
        return {
            "status": "rejected",
            "reason": "抖音是图文/视频平台:draft.json 需要 images(至少一张本地图片路径)",
        }
    missing = [p for p in images if not Path(p).exists()]
    if missing:
        return {"status": "rejected", "reason": f"图片不存在: {'; '.join(missing[:3])}"}

    with sync_playwright() as pw:
        ctx = launch(pw, "douyin", headless=headless)
        page = ctx.new_page()
        try:
            page.goto(PUBLISH_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)
            if "creator-micro" not in page.url:
                return {"status": "not_logged_in", "reason": "登录态失效,先运行 login"}

            # 1. 图片(标准 file input,可多选)
            try:
                page.set_input_files(SELECTORS["file_input"], images, timeout=15000)
                page.wait_for_timeout(5000)  # 等上传与预览
            except Exception as e:  # noqa: BLE001
                return {"status": "rejected", "reason": f"图片上传入口未找到(选择器待校准): {e}"}

            # 2. 标题 + 文字
            try:
                page.wait_for_selector(SELECTORS["title_input"], timeout=10000)
                page.fill(SELECTORS["title_input"], title)
            except Exception as e:  # noqa: BLE001
                return {"status": "rejected", "reason": f"标题输入框未找到(选择器待校准): {e}"}
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
            page.wait_for_timeout(int(draft.get("wait_seconds", 45)) * 1000)

            final_url = page.url
            ok = page.locator(SELECTORS["success_toast"]).count() > 0
            return {
                "status": "published" if ok else "unknown",
                "final_url": final_url,
                "note": "unknown 时请到创作者中心确认;宁可不报成功,不伪造成功",
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
            print("用法: python scripts/douyin_publish.py publish draft.json [--show | --headless]")
            return 1
        headless = "--headless" in sys.argv[3:]
        result = publish_draft(sys.argv[2], headless=headless)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "published" else 1
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
