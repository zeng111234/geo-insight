# -*- coding: utf-8 -*-
"""知乎浏览器发布器(Playwright 驱动系统 Chrome,持久化登录态)。

路线与取舍(同 sohu.py):不走私有接口,走真实页面交互,行为与人工一致。
知乎没有对个人开放的发布 API,浏览器自动化是唯一可行路线。

入口(2026-11-24 实测重定向):
- 创作中心 https://www.zhihu.com/creator ,未登录 302 到 /signin?next=/creator
- 文章编辑器 https://zhuanlan.zhihu.com/write (未登录同样 302 到 /signin)
- 老入口 mp.zhihu.com 域名已不存在(DNS NXDOMAIN),资料里出现都别信

已知约束(公开资料,首次发布用 --show 校准):
- 标题 2~100 字;发布成功跳 https://www.zhihu.com/p/<id>
- 发布时可能弹「推荐话题」层,话题可不选(有「跳过/直接发布」入口);
  话题能带流量,正式运营建议发布后到创作中心手动补
- 正文过短会有提示但通常可发

用法:
    python scripts/zhihu_publish.py login              # 首次登录(扫码/密码,保存会话)
    python scripts/zhihu_publish.py check              # 检查登录态是否有效
    python scripts/zhihu_publish.py publish draft.json [--show | --headless]
    账密可选:data/channels.local.json 写
    {"zhihu": {"username": "...", "password": "..."}},login 时自动填表,验证码转人工
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

CREATOR_URL = "https://www.zhihu.com/creator"
EDITOR_URL = "https://zhuanlan.zhihu.com/write"

# 知乎专栏编辑器是 ProseMirror 系(知乎自研 Editable)。以下选择器:
# 编辑器 DOM 为「公开资料」(登录墙后无法离线核实),首次发布 --show 盯一次。
SELECTORS = {
    "title_input": "textarea[placeholder*='标题'], input[placeholder*='标题']",
    "editor": "div[contenteditable='true']",
    "publish_button": "button:has-text('发布')",
    # 发布过程中可能出现的「推荐话题」浮层,有跳过就走跳过
    "topic_skip": "button:has-text('跳过'), button:has-text('直接发布')",
}


def login_flow() -> bool:
    """打开知乎登录页,等用户完成登录,会话持久化在 profile。

    登录成功的判据是页面离开 /signin(扫码确认后自动跳转)。轮询检测,
    一跳转立刻关窗存会话,最长等 5 分钟——不在终端里按回车,扫完即走。
    """
    with sync_playwright() as pw:
        ctx = launch(pw, "zhihu", headless=False)
        page = ctx.new_page()
        page.goto("https://www.zhihu.com/signin?next=%2Fcreator")
        print("请在打开的浏览器窗口完成登录(扫码或账号密码)。")
        print("登录成功后窗口会自动关闭并验证(最长等 5 分钟)。")
        # 有本地账密就先自动填表(弹验证码转人工);没有就提示配置位置
        creds = credentials("zhihu")
        if creds.get("username") and creds.get("password"):
            try:
                switch_to_password_login(page)
                result = try_password_login(page, creds["username"], creds["password"])
                print(f"已尝试自动登录(表单结果: {result});弹验证码时请在窗口里人工完成。")
            except Exception:  # noqa: BLE001  # 自动登录走不通不阻塞,转人工
                print("自动登录未走通,请在窗口里人工登录。")
        else:
            print('提示:配置 data/channels.local.json {"zhihu": {"username": ..., "password": ...}} 可自动登录。')
        deadline = time.time() + 300
        try:
            while time.time() < deadline:
                page.wait_for_timeout(5000)
                if "signin" not in page.url:
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
    """打开创作中心,未登录会被 302 到 /signin,以 URL 为准(标题辅助)。"""
    with sync_playwright() as pw:
        ctx = launch(pw, "zhihu", headless=True)
        page = ctx.new_page()
        try:
            page.goto(CREATOR_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(4000)
            url = page.url
            title = page.title()
            ok = "signin" not in url and "404" not in title
            print(f"URL: {url}")
            print(f"标题: {title}")
            print("登录态: " + ("有效 ✓" if ok else "已失效(被导回登录页),请重新运行 login"))
            return ok
        finally:
            ctx.close()


def publish_draft(draft_path: str, headless: bool = False) -> dict[str, Any]:
    """发布一篇专栏文章。draft.json 结构:
    {
      "title": "2-100 字标题",
      "content_html": "<p>富文本正文...</p>",
      "wait_seconds": 30   # 等发布跳转的秒数
    }
    选择器未实测校准前,publish 默认显示浏览器(headless=False),--show 显式传同值;
    跑通后可传 --headless 转后台。
    """
    draft = json.loads(Path(draft_path).read_text(encoding="utf-8"))
    title = draft.get("title", "")
    if not 2 <= len(title) <= 100:
        return {"status": "rejected", "reason": f"标题长度 {len(title)} 不在 2-100 字区间"}

    with sync_playwright() as pw:
        ctx = launch(pw, "zhihu", headless=headless)
        page = ctx.new_page()
        try:
            page.goto(EDITOR_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)
            if "signin" in page.url:
                return {"status": "not_logged_in", "reason": "登录态失效,先运行 login"}

            # 1. 标题
            page.fill(SELECTORS["title_input"], title)

            # 2. 正文(ProseMirror:合成 paste 事件写入富文本)
            if not paste_html(page, SELECTORS["editor"], draft.get("content_html", "")):
                return {
                    "status": "rejected",
                    "reason": "正文写入失败:编辑器选择器疑似改版,请 --show 打开浏览器核对 SELECTORS",
                }

            # 3. 发布;中途可能弹「推荐话题」浮层,能跳过就跳过
            page.click(SELECTORS["publish_button"])
            try:
                page.click(SELECTORS["topic_skip"], timeout=4000)
            except Exception:  # noqa: BLE001  # 没弹话题层是常态,吞掉
                pass
            page.wait_for_timeout(int(draft.get("wait_seconds", 30)) * 1000)

            final_url = page.url
            published = "/p/" in final_url
            return {
                "status": "published" if published else "unknown",
                "final_url": final_url,
                "note": "unknown 时请到创作中心确认草稿/发布状态;宁可不报成功,不伪造成功",
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
            print("用法: python scripts/zhihu_publish.py publish draft.json [--show | --headless]")
            return 1
        headless = "--headless" in sys.argv[3:]
        result = publish_draft(sys.argv[2], headless=headless)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "published" else 1
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
