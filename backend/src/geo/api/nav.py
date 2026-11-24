"""统一侧边栏：服务端注入，所有页面共用唯一一份定义。

页面 HTML 里的 <aside class="side">...</aside> 会被整体替换；
新页面可以不写 aside，自动插入到 .shell 里。
分组与命名按参考系统逐字对齐（决策 009）：工作台 / 优化执行 / 分析报告 / 品牌资产 / 探索工具。
`/settings` 不在菜单里——参考系统也没有这一项，它是对象头「问题设置」的目标页，
路由里 active 传 "monitor"，这样进来时侧栏高亮在「问题监控」，用户知道自己在哪。
"""

from __future__ import annotations

import re
from html import escape
from pathlib import Path

# 16x16 线性图标，颜色继承 currentColor
_ICONS = {
    "home": '<path d="M2 7l6-5 6 5v6a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1z"/>',
    "monitor": '<rect x="2" y="2.5" width="12" height="11" rx="1.5"/><path d="M2 6h12M5.5 9.5h5"/>',
    "insight": '<path d="M2 13V8m4 5V4m4 9V9m4 4V6"/>',
    "settings": '<path d="M2 4h12M2 8h12M2 12h12"/><circle cx="6" cy="4" r="1.4" fill="currentColor" stroke="none"/><circle cx="10" cy="8" r="1.4" fill="currentColor" stroke="none"/><circle cx="5" cy="12" r="1.4" fill="currentColor" stroke="none"/>',
    "idea": '<path d="M8 2a4 4 0 0 0-2.3 7.3c.5.4.8 1 .8 1.7h3c0-.7.3-1.3.8-1.7A4 4 0 0 0 8 2z"/><path d="M6.5 13h3M7 14.5h2"/>',
    "task": '<rect x="2.5" y="2.5" width="11" height="11" rx="1.5"/><path d="M5 8l2 2 4-4"/>',
    "studio": '<path d="M3 11.5L11.5 3l1.5 1.5L4.5 13H3z"/><path d="M2 14h12"/>',
    "publish": '<path d="M14 2L7 9M14 2l-4.5 12-2.5-5L2 6.5z"/>',
    "report": '<path d="M4 2h6l2 2v10H4z"/><path d="M10 2v2h2M6 7h4M6 10h4"/>',
    "assets": '<path d="M8 2l6 3.5v5L8 14l-6-3.5v-5z"/><path d="M2 5.5l6 3.5 6-3.5M8 9v5"/>',
    "sources": '<circle cx="8" cy="8" r="6"/><path d="M8 2v6l4.5 3"/>',
}


def _icon(key: str) -> str:
    return (
        '<svg class="ico" viewBox="0 0 16 16" width="15" height="15" fill="none" '
        'stroke="currentColor" stroke-width="1.25" stroke-linecap="round" '
        f'stroke-linejoin="round" aria-hidden="true">{_ICONS.get(key, "")}</svg>'
    )


# (分组名, [(key, 文案, 路径, 图标)])
NAV_GROUPS: list[tuple[str, list[tuple[str, str, str, str]]]] = [
    (
        "工作台",
        [
            ("home", "首页", "/", "home"),
            ("monitor", "问题监控", "/monitor", "monitor"),
            ("insight", "AI 舆情", "/insight", "insight"),
        ],
    ),
    (
        "优化执行",
        [
            ("opportunities", "优化机会", "/opportunities", "idea"),
            ("tasks", "优化任务", "/tasks", "task"),
            ("studio", "内容工作室", "/studio", "studio"),
            ("publish", "任务发布", "/publish", "publish"),
        ],
    ),
    (
        "分析报告",
        [
            ("reports", "报告中心", "/reports", "report"),
        ],
    ),
    (
        "品牌资产",
        [
            ("assets", "品牌资产", "/assets", "assets"),
        ],
    ),
    (
        "探索工具",
        [
            ("sources", "行业信源分布", "/sources", "sources"),
        ],
    ),
]

# 详情页归属由路由显式指定（见 app.py 的 _page 调用），不在这里再维护一份映射。
# 曾经有过 DETAIL_ACTIVE，但从没被读过——留着只会让人以为改了它就生效。


def render_sidebar(active: str) -> str:
    parts = [
        '<aside class="side">',
        '<div class="brand"><span class="dot"></span><div>GEO Insight'
        "<small>国内 AI 品牌可见度</small></div></div>",
        "<nav>",
    ]
    for group, items in NAV_GROUPS:
        parts.append(f'<div class="group">{escape(group)}</div>')
        for key, label, href, icon in items:
            cls = "active" if key == active else ""
            parts.append(
                f'<a href="{escape(href)}" class="{cls}"'
                + (f' aria-current="page"' if cls else "")
                + f">{_icon(icon)}<span>{escape(label)}</span></a>"
            )
    parts.append("</nav>")
    foot = [
        '<div class="side-foot"><div class="k">数据范围</div>'
        "<div>国内 AI 平台 · 客户工作台</div>"
        '<div class="muted">演示数据 · 单进程本地版</div>'
    ]
    # 关了鉴权（自动化检查）时不留死按钮
    from geo.settings import settings

    if settings.auth_required:
        foot.append(
            '<form method="post" action="/logout" class="side-logout">'
            '<button class="ghost small" type="submit">退出登录</button></form>'
        )
    foot.append("</div>")
    parts.append("".join(foot))
    parts.append("</aside>")
    return "\n".join(parts)


# 兼容历史页面里的三种写法：<aside class="side">、<aside class="side no-print">、<aside>
_SIDE_RE = re.compile(r'<aside\b[^>]*>.*?</aside>', re.S)


def inject_sidebar(html: str, active: str) -> str:
    """把页面里的 aside 替换成统一导航；没有 aside 时插到 .shell 里。"""
    sidebar = render_sidebar(active)
    if _SIDE_RE.search(html):
        return _SIDE_RE.sub(lambda _m: sidebar, html, count=1)
    if '<div class="shell">' in html:
        return html.replace('<div class="shell">', '<div class="shell">\n' + sidebar, 1)
    return html.replace("<body>", "<body>\n" + sidebar, 1)


# 统一页脚：把口径规则固定落在页面底部，同时给页面一个收口。
# 只靠调间距治不了「头重脚轻」——内容少的页面下方就是会空，需要用真实信息压住底边。
PAGE_FOOTER = (
    '<footer class="page-foot">'
    '<div class="k">口径与数据范围</div>'
    "<div>提及率 = 提及样本 / 有效样本 · 平均排名只取推荐列表位次 · 无列表提及不进排名分母 · "
    "样本 &lt; 3 显示「样本不足」。阈值由 <code>/v1/meta/methodology</code> 下发，不是前端写死。</div>"
    '<div class="muted">GEO Insight 演示版 · 单进程本地版（Starlette + SQLite）· 国内 AI 平台 · 演示数据 · '
    "报告为生成时刻的不可变快照，口径或模型集合变化时标注「不可比」且不输出涨跌幅 · 原始答案保留 180 天</div>"
    "</footer>"
)


def inject_footer(html: str) -> str:
    """把统一页脚插到 </main> 前（页面主体末尾）。"""
    if 'class="page-foot"' in html:
        return html
    if "</main>" in html:
        return html.replace("</main>", PAGE_FOOTER + "\n  </main>", 1)
    return html.replace("</body>", PAGE_FOOTER + "\n</body>", 1)


def render_page(static_dir: Path | None, name: str, active: str):
    """读取静态页并注入导航与页脚，返回 HTMLResponse（调用方负责 404 文案）。"""
    from starlette.responses import HTMLResponse, JSONResponse

    if static_dir is None or not (static_dir / name).is_file():
        return JSONResponse(
            {"code": "UI_NOT_FOUND", "message": f"frontend/static/{name} not found"},
            status_code=404,
        )
    html = (static_dir / name).read_text(encoding="utf-8")
    return HTMLResponse(inject_footer(inject_sidebar(html, active)))


def render_login(static_dir: Path | None, next_url: str, email: str, error: bool):
    """登录页：不套侧栏与页脚——没登录的人不该看到导航。"""
    from starlette.responses import HTMLResponse, JSONResponse

    if static_dir is None or not (static_dir / "login.html").is_file():
        return JSONResponse(
            {"code": "UI_NOT_FOUND", "message": "frontend/static/login.html not found"},
            status_code=404,
        )
    html = (static_dir / "login.html").read_text(encoding="utf-8")
    message = '<p class="login-error">邮箱或密码不正确</p>' if error else ""
    return HTMLResponse(
        html.replace("{{error}}", message)
        .replace("{{next}}", escape(next_url, quote=True))
        .replace("{{email}}", escape(email, quote=True))
    )
