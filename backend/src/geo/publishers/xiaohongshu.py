# -*- coding: utf-8 -*-
"""小红书创作服务平台浏览器发布器(Playwright 驱动系统 Chrome,持久化登录态)。

路线与取舍(同 sohu.py / 决策 011):真实页面交互,不做任何风控绕过。

⚠️ 网络状态(2026-11-24 实测):本机对 xiaohongshu.com 全域 TLS 被重置
(主站与创作平台一致;抖音/搜狐/知乎等正常)。模块按公开资料写好,登录与
发布要等网络通了才能校准——check 会如实把连接错误报出来,不装有效。

形态说明(重要,如实):
- 小红书是「笔记」平台:图文笔记 = 图片 + 标题 + 正文,没有纯文字形态。
  draft.json 必须带 images(本地图片路径,至少一张),没有图在开浏览器之前
  就拒绝。标题 ≤ 20 字,正文 ≤ 1000 字(以平台当前规则为准)。
- 小红书风控是七个渠道里最重的(登录与发布都可能弹滑块/验证码):本工具
  不做任何绕过,弹验证就停下来等人,在窗口里人工完成后继续。

入口与形态(公开资料,待网络恢复后实测校准):
- 创作服务平台 https://creator.xiaohongshu.com/ (App 扫码登录)
- 发布笔记页 https://creator.xiaohongshu.com/publish/publish

用法:
    python scripts/xiaohongshu_publish.py login
    python scripts/xiaohongshu_publish.py check
    python scripts/xiaohongshu_publish.py publish draft.json [--show | --headless]
    draft.json: {"title": "...", "content_html": "...",
                 "images": ["D:/pics/cover.jpg", ...], "wait_seconds": 45}
    账密可选:data/channels.local.json 写
    {"xiaohongshu": {"username": "...", "password": "..."}},login 时自动填表,验证转人工
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

LOGIN_URL = "https://creator.xiaohongshu.com/"
# 公开资料:发布笔记页;网络通了之后登录实测校准
PUBLISH_URL = "https://creator.xiaohongshu.com/publish/publish"

SELECTORS = {
    # 「公开资料」:笔记发布页,登录墙后无法离线核实,--show 校准
    "title_input": "input[placeholder*='标题'], #title-input",
    "editor": "div[contenteditable='true'], textarea[placeholder*='正文'], .ql-editor",
    "file_input": "input[type='file']",
    "publish_button": "button:has-text('发布')",
    "success_toast": "text=发布成功",
}


def login_flow() -> bool:
    """打开创作服务平台,等用户扫码完成,会话持久化在 profile。

    登录成功判据:URL 进入 publish/home 等后台路径。轮询检测,一跳转立刻
    关窗存会话,最长等 5 分钟——扫完即走。小红书可能弹滑块:请在窗口里
    人工完成,工具不做任何绕过。
    """
    with sync_playwright() as pw:
        ctx = launch(pw, "xiaohongshu", headless=False)
        page = ctx.new_page()
        page.goto(LOGIN_URL)
        print("请在打开的浏览器窗口用小红书 App 扫码登录(弹滑块请人工完成)。")
        print("登录成功后窗口会自动关闭并验证(最长等 5 分钟)。")
        creds = credentials("xiaohongshu")
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
                if any(k in page.url for k in ("publish", "home", "creator")) and "login" not in page.url:
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
    """打开创作服务平台:已登录 URL 会进入后台路径,登录页停在 login/根路径。

    2026-11-24 实测:本机对 xiaohongshu.com TLS 被重置,这里会把连接错误
    如实报出来,不装有效。网络恢复后此判据需登录实测校准。
    """
    with sync_playwright() as pw:
        ctx = launch(pw, "xiaohongshu", headless=True)
        page = ctx.new_page()
        try:
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(6000)
            url = page.url
            title = page.title()
            ok = "login" not in url and url.rstrip("/") != LOGIN_URL.rstrip("/")
            print(f"URL: {url}")
            print(f"标题: {title}")
            print("登录态: " + ("有效 ✓(后台可达)" if ok else "已失效或不可达(见上),请先确认网络再 login"))
            return ok
        except Exception as e:  # noqa: BLE001  # 网络被重置会走到这里,如实报告
            print(f"访问失败: {type(e).__name__}: {e}")
            print("登录态: 不可判断(本机到 xiaohongshu.com 的连接被重置,先修网络)")
            return False
        finally:
            ctx.close()


def publish_draft(draft_path: str, headless: bool = False) -> dict[str, Any]:
    """发布一篇图文笔记。draft.json 结构见模块 docstring。

    笔记 = 图片 + 标题 + 正文:images 缺失或文件不存在在开浏览器前拒绝。
    选择器未校准前默认显示浏览器,校准后可 --headless。
    """
    draft = json.loads(Path(draft_path).read_text(encoding="utf-8"))
    title = draft.get("title", "")
    if not 1 <= len(title) <= 20:
        return {"status": "rejected", "reason": f"标题长度 {len(title)} 不在 1-20 字区间(小红书笔记)"}
    images = draft.get("images") or []
    if not images:
        return {
            "status": "rejected",
            "reason": "小红书是图文笔记平台:draft.json 需要 images(至少一张本地图片路径)",
        }
    missing = [p for p in images if not Path(p).exists()]
    if missing:
        return {"status": "rejected", "reason": f"图片不存在: {'; '.join(missing[:3])}"}

    with sync_playwright() as pw:
        ctx = launch(pw, "xiaohongshu", headless=headless)
        page = ctx.new_page()
        try:
            page.goto(PUBLISH_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)
            if "login" in page.url:
                return {"status": "not_logged_in", "reason": "登录态失效,先运行 login"}

            # 1. 图片(标准 file input,可多选)
            try:
                page.set_input_files(SELECTORS["file_input"], images, timeout=15000)
                page.wait_for_timeout(5000)  # 等上传与预览
            except Exception as e:  # noqa: BLE001
                return {"status": "rejected", "reason": f"图片上传入口未找到(选择器待校准): {e}"}

            # 2. 标题 + 正文
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
                "note": "unknown 时请到创作服务平台确认;宁可不报成功,不伪造成功",
            }
        except Exception as e:  # noqa: BLE001  # 网络被重置会走到这里
            return {"status": "failed", "reason": f"访问失败(本机到 xiaohongshu.com 被重置?): {type(e).__name__}: {e}"}
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
            print("用法: python scripts/xiaohongshu_publish.py publish draft.json [--show | --headless]")
            return 1
        headless = "--headless" in sys.argv[3:]
        result = publish_draft(sys.argv[2], headless=headless)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "published" else 1
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
