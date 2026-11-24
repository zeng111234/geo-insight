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
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PROFILE_ROOT = Path(__file__).resolve().parents[2] / ".browser-profiles"
# 与 baijiahao.local.json 同目录、同一安全等级:明文本地文件,已 gitignore
CHANNEL_CREDENTIALS_PATH = Path(__file__).resolve().parents[3] / "data" / "channels.local.json"


def ensure_utf8() -> None:
    """Windows 控制台可能是 cp1252/cp936,强制 UTF-8,中文提示才不会炸。"""
    for stream in (sys.stdout, sys.stderr):
        if stream.encoding and stream.encoding.lower() not in ("utf-8", "utf8"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def profile_dir(name: str) -> Path:
    d = PROFILE_ROOT / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def launch(pw: sync_playwright, name: str, headless: bool = False):
    """用系统 Chrome 启动持久化上下文。首登需要 headless=False(要扫码)。"""
    return pw.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir(name)),
        executable_path=CHROME_PATH,
        headless=headless,
        viewport={"width": 1440, "height": 900},
    )


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
