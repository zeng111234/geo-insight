# -*- coding: utf-8 -*-
"""发布器共享基座(Playwright + 系统 Chrome + 持久化登录态)。

sohu.py 先于本文件存在,保持自包含不迁移;新渠道(zhihu/wechat/后续)从这里复用:
- profile_dir(name): 登录态目录,统一在 backend/src/.browser-profiles/<渠道>/
  (与 sohu 现存目录一致,已用 `.browser-profiles/` 模式整目录 gitignore,
  放 src 下是沿用 sohu 的既成事实——用户已登录的 sohu 会话在那里)
- launch(pw, name, headless): 用系统 Chrome 起持久化上下文,首登 headless=False
- paste_html(page, selector, html): 往 contenteditable 编辑器灌富文本

为什么富文本要走「合成 paste 事件」:各家编辑器(搜狐 Quill / 知乎 ProseMirror /
公众号自研编辑器)都不能用 fill() 填 contenteditable,而逐键 type() 又慢又容易
触发风控。paste 事件携带 text/html 是浏览器原生的富文本粘贴通道,由编辑器自己
解析,与真人从剪贴板粘贴的行为一致;失败时退回 execCommand('insertHTML')。

选择器标注约定:各渠道 SELECTORS 里写「实测」的是真实跑过的;写「公开资料」的
是根据平台公开页面/通用 DOM 推的,首次发布建议 --show 人工盯跑一次再转无头。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

PROFILE_ROOT = Path(__file__).resolve().parents[2] / ".browser-profiles"
# 与 baijiahao.local.json 同目录、同一安全等级:明文本地文件,已 gitignore
CHANNEL_CREDENTIALS_PATH = Path(__file__).resolve().parents[3] / "data" / "channels.local.json"


def find_chrome() -> str | None:
    """找这台机器上能用的 Chrome/Edge,找不到返回 None。

    为什么不再写死路径:同一个仓库会在不同机器上跑,而 Chrome 的安装位置因机器而异
    (系统装 / 用户目录装 / 只有 Edge / macOS / Linux)。写死 ``C:\\Program Files\\...``
    的后果是那台机器上发布功能直接崩,而且崩在启动浏览器那一步,报错信息完全看不出原因。

    顺序:环境变量 ``GEO_CHROME`` → PATH → 常见安装位置。全都没有时返回 None,
    由调用方决定是退回 Playwright 自带的 chromium 还是直接报错。
    """
    override = os.getenv("GEO_CHROME")
    if override and Path(override).is_file():
        return override

    for name in ("chrome", "google-chrome", "chromium", "chromium-browser", "msedge", "microsoft-edge"):
        found = shutil.which(name)
        if found:
            return found

    local_app = os.getenv("LOCALAPPDATA") or ""
    candidates = [
        # Windows:系统安装
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        # Windows:当前用户安装(不需要管理员权限的那种安装包会装到这里)
        str(Path(local_app) / "Google/Chrome/Application/chrome.exe") if local_app else "",
        # Windows:只有 Edge 的机器
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        # macOS
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        # Linux
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ]
    for path in candidates:
        if path and Path(path).is_file():
            return path
    return None


def ensure_utf8() -> None:
    """Windows 控制台可能是 cp1252/cp936,强制 UTF-8,中文提示才不会炸。"""
    for stream in (sys.stdout, sys.stderr):
        if stream.encoding and stream.encoding.lower() not in ("utf-8", "utf8"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def profile_dir(name: str) -> Path:
    d = PROFILE_ROOT / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def chrome_args() -> list[str]:
    """按平台生成 Chrome 启动参数。Windows/macOS 本机不需要,返回空。

    Linux(尤其 Docker/root)下两个参数是硬性的:
    - --no-sandbox:root 用户跑 Chrome,不带这个直接"Running as root without
      --no-sandbox is not supported"启动失败。设 GEO_CHROME_SANDBOX=1 可保留
      沙箱(非 root 容器里更安全),代价是那些环境可能起不来,由部署者自己权衡。
    - --disable-dev-shm-usage:Docker 默认 /dev/shm 只有 64MB,Chrome 渲染大页面
      会直接崩;加了改用 /tmp,几乎无副作用。
    """
    if not sys.platform.startswith("linux"):
        return []
    args = ["--disable-dev-shm-usage"]
    if os.getenv("GEO_CHROME_SANDBOX", "0") != "1":
        args.insert(0, "--no-sandbox")
    return args


def launch(pw: sync_playwright, name: str, headless: bool = False):
    """用系统 Chrome 启动持久化上下文。首登需要 headless=False(要扫码)。

    找不到系统 Chrome/Edge 时退回 Playwright 自带的 chromium——它跟项目一起装,
    哪台机器都有。用系统浏览器的理由是登录态和风控表现更接近真人,
    但没有它也不该让功能整个不可用。
    """
    kwargs = {
        "user_data_dir": str(profile_dir(name)),
        "headless": headless,
        "viewport": {"width": 1440, "height": 900},
    }
    args = chrome_args()
    if args:
        kwargs["args"] = args
    chrome = find_chrome()
    if chrome:
        kwargs["executable_path"] = chrome
    return pw.chromium.launch_persistent_context(**kwargs)


# 往 contenteditable 写富文本:两条路都不依赖编辑器内部 API。
# 返回 'pasted' / 'no-element' / 'no-effect',由调用方决定是否算失败。
_PASTE_JS = """
([html, selector]) => {
  const el = document.querySelector(selector);
  if (!el) return 'no-element';
  el.focus();
  try {
    const dt = new DataTransfer();
    dt.setData('text/html', html);
    const ev = new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true});
    el.dispatchEvent(ev);
    if (el.textContent && el.textContent.trim()) return 'pasted';
  } catch (e) { /* 落到下面的退路 */ }
  try {
    document.execCommand('insertHTML', false, html);
    if (el.textContent && el.textContent.trim()) return 'pasted';
  } catch (e) { /* 两条路都失败 */ }
  return 'no-effect';
}
"""


def paste_html(page: Page, selector: str, html: str) -> bool:
    """写入成功返回 True。注意:判定依据是编辑器出现非空文本,重灌同一编辑器
    时(编辑器里已有内容)无法区分新旧,调用方应在空编辑器上调用。"""
    return page.evaluate(_PASTE_JS, [html, selector]) == "pasted"


def credentials(channel: str) -> dict:
    """读取 data/channels.local.json 里该渠道的账密,返回 {username, password} 或 {}。

    与 baijiahao.local.json 同一安全等级:明文本地文件、已 gitignore、
    单用户工具不引密钥管理。文件不存在/坏 JSON 时返回空,调用方转人工扫码。
    """
    try:
        data = json.loads(CHANNEL_CREDENTIALS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    cfg = data.get(channel)
    return cfg if isinstance(cfg, dict) else {}


def switch_to_password_login(page: Page) -> bool:
    """各平台默认扫码,账密入口是个小链接/标签,先尝试点开它(尽力而为)。"""
    for text in ("使用账号登录", "密码登录", "账号密码登录", "账号密码"):
        try:
            loc = page.get_by_text(text, exact=False)
            if loc.count() > 0:
                loc.first.click(timeout=2000)
                page.wait_for_timeout(1000)
                return True
        except Exception:  # noqa: BLE001  # 文案/形态不匹配就试下一个
            continue
    return False


# 账密自动登录(尽力而为):找到密码框 → 找它前面的文本框当账号框 → 原生 setter
# 填值(React/Vue 都认)→ 点「登录」按钮。验证码/滑块/短信不在处理范围——
# 出现时由调用方的人工轮询兜底,本工具不做任何绕过(roadmap 范围守卫)。
_LOGIN_JS = """
([username, password]) => {
  const visible = (el) => el.offsetParent !== null && !el.disabled;
  const inputs = Array.from(document.querySelectorAll('input'));
  const pwd = inputs.find((i) => (i.type || '').toLowerCase() === 'password' && visible(i));
  if (!pwd) return 'no-password-field';
  const idx = inputs.indexOf(pwd);
  const acct = inputs.slice(0, idx).reverse().find((i) =>
    ['text', 'tel', 'email', ''].includes((i.type || 'text').toLowerCase()) && visible(i));
  const nativeSet = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  const setVal = (el, v) => {
    el.focus();
    nativeSet.call(el, v);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  };
  if (acct) setVal(acct, username);
  setVal(pwd, password);
  const scope = pwd.closest('form') || document;
  const submit = Array.from(scope.querySelectorAll("button, input[type='submit'], a, [role='button']"))
    .filter((el) => el.offsetParent !== null && /登\\s*录/.test((el.innerText || '') + (el.value || '')))[0];
  if (submit) { submit.click(); return 'submitted'; }
  return 'filled-no-submit';
}
"""


def try_password_login(page: Page, username: str, password: str) -> str:
    """在当前页面尝试账密登录,返回 'submitted' / 'filled-no-submit' /
    'no-password-field'。真正登录成功与否由调用方的轮询判据决定(提交后可能
    弹验证码,人工完成即走)。
    """
    return page.evaluate(_LOGIN_JS, [username, password])
