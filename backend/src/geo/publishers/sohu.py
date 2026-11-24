# -*- coding: utf-8 -*-
"""搜狐号浏览器发布器(Playwright 驱动系统 Chrome,持久化登录态)。

设计取舍(对应 002 spec「渠道登录与一键发布」的真实化第一步):
- 不走「Cookie + 私有接口」:无文档、改版即挂、Cookie 数日过期,比真实 UI 更脆。
- 走真实页面交互:行为与人工一致,风控面上最稳;Quill.js 富文本用 insertHTML 写入。
- 登录态持久化在 backend/.browser-profiles/sohu/(已 gitignore),首次扫码,之后复用。

已知坑(2026-11-24 登录态实测,逐条标注证据强度):
- 老地址 mp/post/createArticle 已 404;新后台在 mpfe/v4,编辑器 URL 直连会被
  弹回,必须从内容管理页点「发布内容」按钮进(SPA 路由,实测)
- 未登录:被静默弹回门户首页(不再跳 404 页),check_login 以「停在后台 URL」为准(实测)
- **未实名认证的账号发不了文章**:点「发布内容」只进发动态(addmoment),
  publish 返回 blocked,不硬闯(实测)
- 编辑器仍是 Quill(.ql-editor,实测);动态页标题选填 ≤72 字(实测,文章页同族)
- 发布按钮/确认弹窗/频道选择:文章页被实名锁着没探到,选择器待实名后校准
- 外链图片被转存搜狐图床;编辑器过滤 script/iframe/base64 图(老资料,沿用)
- 滥用原创声明会被永久关权限(本工具不碰原创开关)

用法:
    python scripts/sohu_publish.py login              # 首次登录(扫码,保存会话)
    python scripts/sohu_publish.py check              # 检查登录态是否有效
    python scripts/sohu_publish.py publish draft.json # 发布(draft.json 见 docstring)
    账密可选:data/channels.local.json 写
    {"sohu": {"username": "...", "password": "..."}},login 时自动填表,验证码转人工
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from geo.publishers._base import credentials, paste_html, switch_to_password_login, try_password_login

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROFILE_DIR = Path(__file__).resolve().parents[2] / ".browser-profiles" / "sohu"
CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
# 2026-11-24 实测(登录态下逐页探测):
# - 老地址 mp/post/createArticle 已 404(改版),新后台在 mpfe/v4 下
# - 编辑器 URL 直连一律被弹回内容管理页,必须从「发布内容」按钮点进去(SPA 路由)
# - 未登录:被静默弹回门户首页;未实名:发文章(addarticle)被锁,只进得了发动态(addmoment)
HOME_URL = "https://mp.sohu.com/mpfe/v4/contentManagement/first/page"

# 搜狐编辑器与表单选择器。2026-11-24 登录态实测一半:入口按钮/标题框/Quill
# 编辑器是实的;发布按钮与确认弹窗在「发文章」页上(未实名锁着,待实名后校准)
SELECTORS = {
    "entry_button": "button.publish-btn",               # 实测:内容管理页「发布内容」
    "title_input": "input[placeholder*='请输入标题']",   # 实测(动态页);文章页同族
    "editor": ".ql-editor",                             # 实测:新编辑器仍是 Quill
    "publish_button": "li.publish-btn, button:has-text('发布')",  # 待实名后校准
    "confirm_dialog": ".layui-layer, .el-dialog__wrapper",
    "confirm_ok": ".layui-layer-btn a.layui-layer-btn0, .el-dialog__footer button",
}


def _dismiss_dialogs(page) -> None:
    """关掉挡路的弹窗(2026-11-24 实测:首次进后台弹「欢迎入驻」引导向导,
    编辑器上还会弹「实名认证」提示,都要让路才能操作页面)。Element UI 弹窗
    优先点「暂不认证」这类明确按钮,退而求其次点右上角 X。
    """
    for _ in range(2):
        try:
            if page.locator(".el-dialog__wrapper:visible, .layui-layer:visible").count() == 0:
                return
            for sel in ("text=暂不认证", "text=知道了", ".el-dialog__headerbtn"):
                loc = page.locator(sel)
                if loc.count() > 0:
                    try:
                        loc.first.click(timeout=3000)
                        page.wait_for_timeout(1500)
                        break
                    except Exception:  # noqa: BLE001  # 这个关不掉就试下一个
                        continue
        except Exception:  # noqa: BLE001  # 页面跳转中查询可能抛错,重试一次
            page.wait_for_timeout(1000)


def _launch(pw: sync_playwright, headless: bool = False):
    """用系统 Chrome 启动持久化上下文。首登需要 headless=False(要扫码)。"""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return pw.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        executable_path=CHROME_PATH,
        headless=headless,
        viewport={"width": 1440, "height": 900},
    )


def login_flow() -> bool:
    """打开搜狐门户页,等用户登录完成,会话持久化在 profile。

    2026-11-24 实测:改版后未登录的 mp.sohu.com 是内容门户,创作者区块渲染
    成 .no-login(带「登录/注册」按钮);登录成功后该块消失。轮询检测,最长
    等 5 分钟,扫完即走,不用回终端按回车。
    """
    with sync_playwright() as pw:
        ctx = _launch(pw, headless=False)
        page = ctx.new_page()
        page.goto("https://mp.sohu.com/")
        print("请在打开的浏览器窗口完成登录(点「登录」,扫码或账号密码)。")
        print("登录成功后窗口会自动关闭并验证(最长等 5 分钟)。")
        # 有本地账密就先自动填表(弹验证码转人工);没有就提示配置位置
        creds = credentials("sohu")
        if creds.get("username") and creds.get("password"):
            try:
                page.locator(".login-btn").first.click(timeout=5000)  # 先打开登录框
                switch_to_password_login(page)
                result = try_password_login(page, creds["username"], creds["password"])
                print(f"已尝试自动登录(表单结果: {result});弹验证码时请在窗口里人工完成。")
            except Exception:  # noqa: BLE001  # 自动登录走不通不阻塞,转人工
                print("自动登录未走通(登录框形态可能不同),请在窗口里人工登录。")
        else:
            print('提示:配置 data/channels.local.json {"sohu": {"username": ..., "password": ...}} 可自动登录。')
        deadline = time.time() + 300
        try:
            while time.time() < deadline:
                page.wait_for_timeout(5000)
                # 误报教训(2026-11-24 实测):点「登录」会先跳微信扫码授权页
                # (open.weixin.qq.com),那页没有登录按钮,光看「登录块消失」会在
                # 用户扫码前就关窗。必须同时满足:回到 mp.sohu.com 域 + 登录块消失。
                if "mp.sohu.com" in page.url and page.locator(".no-login, .login-btn").count() == 0:
                    print(f"检测到登录(创作者区块已切换): {page.url}")
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
    """直接访问内容管理页:未登录会被静默弹回门户首页(URL 变化)。

    老判据(标题含 404 / URL 含 login)已失效:改版后未登录不再跳 404 页,
    而是重定向回 mp.sohu.com 门户,老判据会误报「有效」。「停在后台 URL」才是
    登录态有效的硬证据。未实名账号也能进(实名只锁发文章,不锁登录)。
    """
    with sync_playwright() as pw:
        ctx = _launch(pw, headless=True)
        page = ctx.new_page()
        try:
            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(4000)
            url = page.url
            title = page.title()
            ok = "contentManagement" in url or "mpfe/v4" in url
            print(f"URL: {url}")
            print(f"标题: {title}")
            print("登录态: " + ("有效 ✓(后台可达)" if ok else "已失效(被弹回门户页),请重新运行 login"))
            return ok
        finally:
            ctx.close()


def publish_draft(draft_path: str, headless: bool = True) -> dict[str, Any]:
    """发布一篇图文。draft.json 结构:
    {
      "title": "5-72 字标题",
      "content_html": "<p>富文本正文...</p>",
      "channel": "频道(老编辑器资料;新编辑器是否必填待实名后校准。留空 + --show 可手动选)",
      "wait_seconds": 30  # 等发布跳转的秒数
    }
    返回 status: published / rejected / not_logged_in / blocked / unknown。
    blocked = 账号未实名,发文章被搜狐锁,去后台完成实名再跑。
    """
    draft = json.loads(Path(draft_path).read_text(encoding="utf-8"))
    title = draft.get("title", "")
    if not 5 <= len(title) <= 72:
        return {"status": "rejected", "reason": f"标题长度 {len(title)} 不在 5-72 字区间"}

    with sync_playwright() as pw:
        ctx = _launch(pw, headless=headless)
        page = ctx.new_page()
        try:
            # 导航(2026-11-24 实测):编辑器 URL 直连会被弹回,必须
            # 内容管理页 → 关掉引导弹窗 → 点「发布内容」进编辑器
            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)
            if "contentManagement" not in page.url and "mpfe/v4" not in page.url:
                return {"status": "not_logged_in", "reason": "登录态失效(被弹回门户页),先运行 login"}
            _dismiss_dialogs(page)
            try:
                page.click(SELECTORS["entry_button"], timeout=10000)
            except Exception as e:  # noqa: BLE001
                return {"status": "rejected", "reason": f"「发布内容」按钮未找到(改版?--show 核对): {e}"}
            page.wait_for_timeout(5000)
            editor_page = ctx.pages[-1] if len(ctx.pages) > 1 else page
            editor_page.wait_for_timeout(2000)
            _dismiss_dialogs(editor_page)

            # 实名闸门(2026-11-24 实测):未实名账号点「发布内容」只进得了
            # 发动态(addmoment),发文章(addarticle)被锁。如实报告,不硬闯。
            if "addmoment" in editor_page.url and "addarticle" not in editor_page.url:
                return {
                    "status": "blocked",
                    "reason": "账号未实名认证:搜狐锁了「发文章」。请到 mp.sohu.com 后台完成实名认证后再运行 publish。",
                    "final_url": editor_page.url,
                }
            page = editor_page

            # 1. 标题(选择器对新编辑器未校准:匹配不到给明确错误,不抛裸异常)
            try:
                page.wait_for_selector(SELECTORS["title_input"], timeout=15000)
                page.fill(SELECTORS["title_input"], title)
            except Exception as e:  # noqa: BLE001
                return {
                    "status": "rejected",
                    "reason": f"标题输入框未找到(选择器待校准,--show 核对 SELECTORS): {e}",
                }

            # 2. 正文:合成 paste 事件(通用 contenteditable 通道,老代码硬编码
            #    Quill 的 .ql-editor 内部 API,新编辑器不一定是 Quill,弃用)
            if not paste_html(page, SELECTORS["editor"], draft.get("content_html", "")):
                return {
                    "status": "rejected",
                    "reason": "正文写入失败:编辑器选择器疑似与新编辑器不匹配,请 --show 核对 SELECTORS",
                }

            # 3. 频道(必填项,缺失会被服务端拒)
            #    draft 指定了频道就自动选;没指定且是可见浏览器(--show),等用户
            #    手动选——校准期账号能发哪些频道不一定知道,人点一次最稳。
            channel = draft.get("channel", "")
            if channel:
                try:
                    page.click("text=请选择频道", timeout=5000)
                    page.click(f".channel-item:has-text('{channel}')", timeout=5000)
                except Exception as e:  # noqa: BLE001
                    return {"status": "rejected", "reason": f"频道选择失败: {e}"}
            elif not headless:
                print(">> 频道未指定:请在浏览器窗口手动选择频道(最长等 3 分钟)。")
                deadline = time.time() + 180
                while time.time() < deadline:
                    page.wait_for_timeout(5000)
                    # 「请选择频道」占位文本消失 = 已选中(信号不准时宁可超时也不乱点)
                    if page.locator("text=请选择频道").count() == 0:
                        break
                else:
                    return {
                        "status": "unknown",
                        "reason": "等待手动选择频道超时(3 分钟);若已选好,重跑并在 draft.json 里写 channel",
                    }
            else:
                return {"status": "rejected", "reason": "频道为空(搜狐唯一硬性必填项);无头模式无法手动选"}

            # 4. 发布按钮与两步确认(选择器是老编辑器资料,新编辑器待校准)
            try:
                page.click(SELECTORS["publish_button"], timeout=10000)
            except Exception as e:  # noqa: BLE001
                return {"status": "rejected", "reason": f"发布按钮未找到(选择器待校准,--show 核对): {e}"}
            confirm = page.locator(SELECTORS["confirm_dialog"])
            if confirm.count() > 0:
                page.click(SELECTORS["confirm_ok"])
            page.wait_for_timeout(int(draft.get("wait_seconds", 30)) * 1000)

            final_url = page.url
            # 老编辑器成功跳 post/detail;新编辑器的成功页形态未知,拿不准就报 unknown
            published = "post/detail" in final_url or "post_id" in final_url
            return {
                "status": "published" if published else "unknown",
                "final_url": final_url,
                "note": "unknown 时请人工到后台确认;宁可不报成功,不伪造成功",
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
            print("用法: python scripts/sohu_publish.py publish draft.json [--show]")
            return 1
        result = publish_draft(sys.argv[2], headless="--show" not in sys.argv)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "published" else 1
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
