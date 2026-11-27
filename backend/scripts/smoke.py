"""End-to-end smoke test: seed a temp database, boot uvicorn, hit real HTTP.

Run from the project root:

    python backend/scripts/smoke.py

It seeds a temp DB and boots its own uvicorn on an auto-picked free port,
so it never tests the dev server by accident. Override with GEO_SMOKE_PORT=n
(an occupied explicit port is a hard error).

Exits non-zero on the first failed assertion, printing the response body.
"""

import http.cookiejar
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# 0 = 自动挑空闲端口。开发服务常占着 8123，若冒烟也去连它，
# 请求会打到开发库而不是本脚本刚 seed 的临时库，断言就成了假绿灯。
PORT = int(os.getenv("GEO_SMOKE_PORT", "0"))
BASE = f"http://127.0.0.1:{PORT}"
TRACKER = "11111111-1111-1111-1111-111111111111"
ART_PUBLISHED = "55555555-5555-5555-5555-555555555551"
ART_DRAFT = "55555555-5555-5555-5555-555555555552"
DB = Path(tempfile.gettempdir()) / "geo_smoke.db"
ADMIN_EMAIL = os.getenv("GEO_ADMIN_EMAIL", "demo@example.com")
ADMIN_PASSWORD = "smoke-pass-123"

# 登录一次，后续所有请求自动带 cookie（install_opener 对所有 urlopen 生效）
_COOKIES = http.cookiejar.CookieJar()
urllib.request.install_opener(urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_COOKIES)))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """要观察 302 本身时用：默认的 opener 会跟着跳走，看不到状态码。"""

    def redirect_request(self, *args, **kwargs):
        return None


_NO_REDIRECT = urllib.request.build_opener(
    _NoRedirect(), urllib.request.HTTPCookieProcessor(_COOKIES)
)


def status_without_redirect(path: str) -> tuple[int, str]:
    try:
        with _NO_REDIRECT.open(BASE + path, timeout=5) as resp:
            return resp.status, resp.headers.get("location", "")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("location", "")


def post_form(path: str, data: dict) -> tuple[int, str]:
    """登录表单：要看到 303 本身与 Location，所以不跟随重定向；cookie 仍会存进罐子。"""
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        BASE + path, data=body, method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    try:
        with _NO_REDIRECT.open(req, timeout=5) as resp:
            return resp.status, resp.headers.get("location", "")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("location", "")


def get(path: str) -> tuple[int, dict]:
    req = urllib.request.Request(BASE + path)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def get_html(path: str) -> tuple[int, str, str]:
    req = urllib.request.Request(BASE + path)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.headers.get("content-type", ""), resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("content-type", ""), exc.read().decode("utf-8", "replace")


def post(path: str, payload: dict) -> tuple[int, dict]:
    return _request(path, payload, "POST")


def patch(path: str, payload: dict) -> tuple[int, dict]:
    return _request(path, payload, "PATCH")


def patch(path: str, payload: dict) -> tuple[int, dict]:
    return _request(path, payload, "PATCH")


def delete(path: str) -> int:
    req = urllib.request.Request(BASE + path, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def _csrf_token() -> str:
    """登录后服务端下发的 geo_csrf(写接口的 X-CSRF-Token 头)。"""
    for c in _COOKIES:
        if c.name == "geo_csrf":
            return c.value
    return ""


def _request(path: str, payload: dict, method: str) -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    if method != "GET":
        headers["X-CSRF-Token"] = _csrf_token()
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def check(name: str, condition: bool, detail) -> None:
    if condition:
        print(f"PASS  {name}")
    else:
        print(f"FAIL  {name}\n      {json.dumps(detail, ensure_ascii=False)[:600]}")
        raise SystemExit(1)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def main() -> None:
    global PORT, BASE
    if PORT == 0:
        PORT = _free_port()
    elif _port_in_use(PORT):
        raise SystemExit(
            f"port {PORT} is occupied (likely the dev server). "
            "Stop it, or unset GEO_SMOKE_PORT to auto-pick a free port."
        )
    BASE = f"http://127.0.0.1:{PORT}"

    if DB.exists():
        DB.unlink()
    env = os.environ.copy()
    env["GEO_DB_PATH"] = str(DB)
    env["PYTHONPATH"] = str(ROOT / "src")
    # 显式指定密码，不依赖默认值：seed 与 uvicorn 用同一份环境，登录才对得上
    env["GEO_ADMIN_PASSWORD"] = ADMIN_PASSWORD
    # 发布通道真实化(决策 011):冒烟里不开 worker,发布走「纯入队」,
    # 否则 uvicorn 子进程会真的去起浏览器自动化
    env["GEO_PUBLISH_WORKER"] = "0"

    subprocess.run([sys.executable, "-m", "geo.seed"], cwd=ROOT, env=env, check=True, capture_output=True)
    # 冒烟要验发布链路,需要一个 connected 渠道;决策 011 后 seed 不再给假
    # 「已连接」,这里直接在临时库里把 wechat 摆成 connected(测试布置,不是假 UI 数据)
    import sqlite3

    with sqlite3.connect(DB) as conn:
        conn.execute(
            """UPDATE channel_account SET status = 'connected', login_mode = 'browser',
                                     last_checked_at = '2026-11-24T12:00:00+08:00'
               WHERE platform_key = 'wechat'"""
        )
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "geo.api.app:app", "--port", str(PORT), "--log-level", "warning"],
        cwd=ROOT,
        env=env,
    )
    try:
        for _ in range(40):
            if proc.poll() is not None:
                raise SystemExit(f"smoke server exited early (code {proc.returncode}), port {PORT}")
            try:
                status, _ = get("/health")
                if status == 200:
                    break
            except Exception:  # noqa: BLE001
                time.sleep(0.25)
        else:
            raise SystemExit("server did not start")

        status, body = get("/health")
        check("health", status == 200 and body["status"] == "ok", body)

        # 鉴权：未登录时接口必须 401、页面必须跳登录，登录后才能继续
        status, body = get("/v1/trackers")
        check("未登录访问接口返回 401", status == 401 and body.get("code") == "UNAUTHORIZED", (status, body))
        status, location = status_without_redirect("/")
        check("未登录访问页面跳登录", status == 302 and location.startswith("/login"), (status, location))
        status, _, _ = get_html("/static/app.css")
        check("静态资源不拦", status == 200, status)
        status, location = post_form("/login", {"email": ADMIN_EMAIL, "password": "wrong-password"})
        check("错误密码不跳首页", status == 303 and "error=1" in location, (status, location))
        status, location = post_form("/login", {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        check("正确密码登录成功", status == 303 and location == "/", (status, location))
        check("登录后拿到 session cookie", any(c.name == "geo_session" for c in _COOKIES), list(_COOKIES))
        # CSRF(003 FR-005):登录同时下发非 HttpOnly 的 geo_csrf;写请求不带
        # X-CSRF-Token 必须 403——SameSite=None 场景下第三方页面的写请求没有这个头
        check("登录后下发 csrf cookie", any(c.name == "geo_csrf" for c in _COOKIES), list(_COOKIES))
        csrf = _csrf_token()
        no_token_req = urllib.request.Request(
            BASE + "/logout", data=b"", method="POST"
        )
        try:
            with urllib.request.urlopen(no_token_req, timeout=5) as resp:
                no_token_status = resp.status
        except urllib.error.HTTPError as exc:
            no_token_status = exc.code
        check("写请求缺 CSRF token 被 403", no_token_status == 403, no_token_status)
        with_token_req = urllib.request.Request(
            BASE + "/logout", data=b"", method="POST", headers={"X-CSRF-Token": csrf}
        )
        try:
            with _NO_REDIRECT.open(with_token_req, timeout=5) as resp:
                with_token_status, with_token_loc = resp.status, resp.headers.get("location", "")
        except urllib.error.HTTPError as exc:
            with_token_status, with_token_loc = exc.code, exc.headers.get("location", "")
        check("带 token 的写请求放行(logout 303)", with_token_status == 303, (with_token_status, with_token_loc))
        # logout 之后重新登录,后续断言继续用会话
        status, location = post_form("/login", {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        check("csrf 验证后重新登录", status == 303 and location == "/", (status, location))

        status, body = get("/v1/trackers")
        # P7 后 seed 造 19 个对象（黄金 + 教培 + 17 个空对象）。这里守「列表能用、
        # 数量符合当前 seed」，不再写死 2——对象数是演示规模，由 P7 测试单独守。
        check("tracker list", status == 200 and len(body["items"]) == 19, body)
        golden = next(t for t in body["items"] if t["id"] == TRACKER)
        check(
            "question summary",
            golden["question_summary"] == {"total": 12, "core": 4, "long_tail": 8},
            golden["question_summary"],
        )

        status, body = get(f"/v1/trackers/{TRACKER}/metrics/overview?from=2026-11-01&to=2026-11-03")
        check("valid total 9", body["valid_total"] == 9, body)
        check("mention count 2", body["cards"]["mention_count"] == 2, body)
        check("mention rate 22.22%", body["cards"]["mention_rate"]["value"] == 0.2222, body)
        check("avg rank insufficient (2 ranked)", body["cards"]["avg_rank"]["status"] == "insufficient_samples", body)
        check("no list samples 0", body["no_list_samples"] == 0, body)

        status, body = get(f"/v1/trackers/{TRACKER}/metrics/overview?from=2026-11-01&to=2026-11-04")
        check("no list samples 1", body["no_list_samples"] == 1, body)
        check("mention count 3", body["cards"]["mention_count"] == 3, body)

        status, body = get(f"/v1/trackers/{TRACKER}/metrics/overview?from=2026-11-05&to=2026-11-05")
        check("two samples insufficient", body["cards"]["mention_rate"]["status"] == "insufficient_samples", body)
        check("two samples value null", body["cards"]["mention_rate"]["value"] is None, body)

        status, body = get(f"/v1/trackers/{TRACKER}/answers?from=2026-11-01&to=2026-11-03&limit=100")
        check("answer list 9", status == 200 and body["page"]["total"] == 9, body)
        check("answer has mentions", any(item["mentions"] for item in body["items"]), body)

        status, body = get(f"/v1/trackers/{TRACKER}/citations/summary?from=2026-11-01&to=2026-11-03")
        check("citations present", body["total_citations"] == 18, body)
        check("video share present", body["video_share"] == 0.5, body)
        check("unknown published share", body["unknown_published_share"] == 0.5, body)

        status, body = get(f"/v1/trackers/{TRACKER}/citations?from=2026-11-01&to=2026-11-03")
        urls = [x["url"] for x in body["items"]]
        check(
            "citation detail dedups URLs but accumulates counts",
            status == 200 and len(urls) == len(set(urls))
            and body["total_urls"] == len(urls)
            and body["total_occurrences"] >= body["total_urls"],
            body,
        )

        # 变异检查：种子里 18 个 URL 本来就不重复，只断言「URL 唯一」是空断言。
        # 从本次响应里挑一个确实落在查询周期内的 URL，复制它的一条引用，
        # 验证明细仍只一行、次数累计 +1；结束后删掉，避免污染后续断言。
        target_url = body["items"][0]["url"]
        baseline_times = int(body["items"][0]["times"])
        conn = sqlite3.connect(DB)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM citation WHERE url = ? LIMIT 1", (target_url,)
            ).fetchone()
            check("mutation target is in the queried period", row is not None, {"url": target_url})
            cols = list(row.keys())
            values = [row[k] for k in cols]
            values[cols.index("id")] = "smoke-duplicate-citation"
            conn.execute(
                f"INSERT INTO citation({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})", values
            )
            conn.commit()

            status, body2 = get(f"/v1/trackers/{TRACKER}/citations?from=2026-11-01&to=2026-11-03")
            same = [x for x in body2["items"] if x["url"] == target_url]
            check(
                "duplicate citation keeps one detail row but accumulates times",
                status == 200
                and body2["total_urls"] == body["total_urls"]
                and body2["total_occurrences"] == body["total_occurrences"] + 1
                and len(same) == 1 and int(same[0]["times"]) == baseline_times + 1,
                {"before": body["total_occurrences"], "after": body2["total_occurrences"], "same": same},
            )
        finally:
            conn.execute("DELETE FROM citation WHERE id = ?", ("smoke-duplicate-citation",))
            conn.commit()
            conn.close()

        status, body = get("/v1/meta/methodology")
        check(
            "methodology exposes configurable no-list threshold",
            status == 200 and "no_list_alert_threshold" in body and body["definitions"],
            body,
        )
        status, ctype, html = get_html("/")
        check(
            "dashboard reads threshold from methodology, not hardcoded",
            "no_list_alert_threshold" in html,
            "threshold must come from /v1/meta/methodology",
        )
        check(
            "dashboard fetches deduped citation detail",
            "/citations?" in html and "信源明细" in html,
            "citation detail must be wired into the dashboard",
        )

        status, body = get(f"/v1/trackers/{TRACKER}/metrics/trend?from=2026-11-01&to=2026-11-03")
        check("trend has 3 days", status == 200 and len(body["points"]) == 3, body)

        # ---- 富数据窗口 2026-11-07..30：页面密度与内容故事（黄金窗口之外）----
        status, body = get(f"/v1/trackers/{TRACKER}/metrics/overview?from=2026-11-07&to=2026-11-30")
        check("rich period valid total 1288", body["valid_total"] == 1288, body["valid_total"])
        status, body = get(f"/v1/trackers/{TRACKER}/metrics/trend?from=2026-11-07&to=2026-11-30")
        # 24 个自然日减去 11-22 采集中断日（0 条答案的日期不进趋势点）
        check("rich period trend 23 days", status == 200 and len(body["points"]) == 23, len(body["points"]))
        status, early = get(f"/v1/trackers/{TRACKER}/metrics/overview?from=2026-11-07&to=2026-11-12")
        status, late = get(f"/v1/trackers/{TRACKER}/metrics/overview?from=2026-11-25&to=2026-11-30")
        check(
            "mention rate rises after content push",
            early["cards"]["mention_rate"]["value"] is not None
            and late["cards"]["mention_rate"]["value"] is not None
            and late["cards"]["mention_rate"]["value"] > early["cards"]["mention_rate"]["value"],
            {"early": early["cards"]["mention_rate"], "late": late["cards"]["mention_rate"]},
        )
        check("rich period avg rank has value", late["cards"]["avg_rank"]["status"] == "ok", late["cards"]["avg_rank"])
        status, body = get(f"/v1/trackers/{TRACKER}/citations?from=2026-11-07&to=2026-11-30")
        check(
            "citation totals are full while items capped at 100",
            status == 200
            and len(body["items"]) == 100
            and body["total_urls"] > 100
            and body["total_occurrences"] > body["total_urls"]
            and int(body["items"][0]["times"]) > 1,
            {
                "items": len(body["items"]),
                "total_urls": body["total_urls"],
                "occurrences": body["total_occurrences"],
                "top_times": body["items"][0]["times"] if body["items"] else None,
            },
        )

        # 分页：明细表 10 行/页，服务端分页必须真的翻得动、页间不重叠、总数口径一致。
        status, c1 = get(f"/v1/trackers/{TRACKER}/citations?from=2026-11-07&to=2026-11-30&limit=10&page=1")
        status, c2 = get(f"/v1/trackers/{TRACKER}/citations?from=2026-11-07&to=2026-11-30&limit=10&page=2")
        u1 = {x["url"] for x in c1["items"]}
        u2 = {x["url"] for x in c2["items"]}
        check(
            "citations paginate without overlap",
            len(c1["items"]) == 10 and len(c2["items"]) == 10
            and not (u1 & u2)
            and c1["page"]["total"] == c2["page"]["total"] == c1["total_urls"],
            {"p1": len(u1), "p2": len(u2), "meta": c2["page"]},
        )
        status, a1 = get(f"/v1/trackers/{TRACKER}/answers?from=2026-11-07&to=2026-11-30&limit=10&page=1")
        status, a2 = get(f"/v1/trackers/{TRACKER}/answers?from=2026-11-07&to=2026-11-30&limit=10&page=2")
        check(
            "answers paginate without overlap",
            len(a1["items"]) == 10 and len(a2["items"]) == 10
            and not ({x["id"] for x in a1["items"]} & {x["id"] for x in a2["items"]})
            and a1["page"]["total"] == a2["page"]["total"] == 1288,
            {"p1": len(a1["items"]), "p2": len(a2["items"]), "total": a1["page"]["total"]},
        )

        status, body = get("/v1/trackers/does-not-exist/metrics/overview?from=2026-11-01&to=2026-11-03")
        check("404 for unknown tracker", status == 404 and body["code"] == "NOT_FOUND", body)

        # 看板必须由后端同源提供：file:// 打开时浏览器会拦掉对 127.0.0.1 的请求，
        # 表现为 "Failed to fetch"。这两条断言就是为这个故障加的回归。
        status, ctype, html = get_html("/")
        check("dashboard served at /", status == 200 and "text/html" in ctype, {"status": status, "type": ctype})
        check("dashboard is the app page", "GEO Insight" in html and 'id="cards"' in html, html[:200])
        check(
            "page does not hardcode absolute api base",
            'http://127.0.0.1:8123/v1' not in html,
            "index.html must derive API base from location, not pin 127.0.0.1",
        )
        check("page falls back only for file://", 'location.protocol === "file:"' in html, "missing same-origin logic")
        check("dashboard uses a tracker select", '<select id="tracker"' in html, "UUID input should be replaced by a select")

        status, ctype, _ = get_html("/static/index.html")
        check("static mount works", status == 200 and "text/html" in ctype, {"status": status, "type": ctype})

        # ---- 002 内容工作室 ----
        status, ctype, html = get_html("/studio")
        check("studio page served", status == 200 and "内容工作室" in html, {"status": status})
        # 决策 003 曾要求页面不得出现积分/AI 成稿；决策 009 覆盖 003，
        # 明确要求把积分账本做真（账本是真的，定价是假的）。按决策 009 文档
        # 第 33 行的要求，这里显式改成正向断言，不悄悄删掉旧断言。
        # 2026-11-26 拍板 A3：全局放积分徽章 + AI 助手按钮，但明写「需 key，未接入」。
        # 因此「AI 助手」从负向词移出，改为正向契约——不是放宽检查，是加严：
        # 入口必须在、必须诚实标注未接入、余额必须来自真账本、不得偷带生成动作。
        check("studio shows real credit ledger", "creditBalance" in html
              and "账本是真的" in html, "missing credit ledger badge")
        # 2026-11-27：AI 生成草稿后端已真接（ffedaa5，真调 LLM+账本+人工审核），
        # 前端按钮也是真的了——从负向词移到正向契约：入口必须在、必须显式标人工核对。
        # 不是放宽：旧负向断言只查「没有假按钮」，新断言还要查「真按钮+诚实标注」。
        check("studio has no fake price", "5 积分" not in html, {"found": "5 积分"})
        check("studio has real AI generate button", "AI 生成草稿" in html
              and 'articles/generate' in html and "人工核对" in html,
              "missing honest AI generate button")
        check("studio states human authorship", "人工成稿" in html, "missing 人工成稿 statement")

        # A3 正向契约：每个路由页都注入公共顶栏脚本；打印/详情页排除。
        hook = '/static/global-bar.js'
        for page in ("/", "/monitor", "/assets", "/insight", "/opportunities",
                     "/sources", "/publish", "/tasks", "/settings", "/reports",
                     "/studio", "/update"):
            code, _, page_html = get_html(page)
            check(f"A3 global bar injected [{page}]", code == 200 and hook in page_html,
                  {"status": code, "hook": hook in page_html})
        for page in ("/pilot",):
            code, _, page_html = get_html(page)
            check(f"A3 global bar excluded [{page}]", code == 200 and hook not in page_html,
                  {"status": code, "hook": hook in page_html})

        # JS 源文本：必须明写未接入、只从 /credits 读；不得有假生成/写操作。
        code, _, js = get_html(hook)
        check("A3 JS served", code == 200 and "global-bar.js" in hook, {"status": code})
        for required in ("AI 助手", "需 key", "未接入", "/credits"):
            check(f"A3 JS honest [{required}]", required in js, {"missing": required})
        for forbidden in ("生成草稿", "立即分析", "开始分析", "/v1/assistant/",
                          "method=\"post\"", "type=\"submit\""):
            check(f"A3 JS no fake action [{forbidden}]", forbidden not in js, {"found": forbidden})

        # 余额真值：活 API 与账本求和一致，JS 里没有写死余额数字。
        credit_status, credit = get("/v1/credits")
        check("A3 credits from real ledger",
              credit_status == 200
              and credit.get("unit") == "分"
              and credit.get("balance") == sum(e.get("delta", 0) for e in credit.get("entries", [])),
              {"status": credit_status, "balance": credit.get("balance")})

        status, body = get("/v1/channels")
        channels = body.get("items", [])
        check("7 channels seeded", status == 200 and len(channels) == 7, {"count": len(channels)})
        connected = [c for c in channels if c["status"] == "connected"]
        disconnected = [c for c in channels if c["status"] != "connected"]
        # 决策 011:seed 不再有假「已连接」;诚实初始态 = wechat(冒烟布置)+ 3 个浏览器渠道
        # 之外的 needs_config / not_implemented。字段 last_check_note 必须在(前端显示检测附注)。
        check("honest channel states (no fake connected)",
              len(connected) == 1 and connected[0]["platform_key"] == "wechat",
              [c["platform_key"] + ":" + c["status"] for c in channels])
        by_key = {c["platform_key"]: c for c in channels}
        check("baijiahao needs_config", by_key["baijiahao"]["status"] == "needs_config", by_key["baijiahao"])
        # 决策 011 第二批:三渠道发布器上线,初始态 disconnected(不再是 not_implemented)
        check("xhs/toutiao/douyin have publishers (disconnected)",
              all(by_key[k]["status"] == "disconnected" for k in ("xiaohongshu", "toutiao", "douyin")),
              {k: by_key[k]["status"] for k in ("xiaohongshu", "toutiao", "douyin")})
        check("channels expose last_check_note", all("last_check_note" in c for c in channels), channels[:1])

        # 决策 011 第二批:7 渠道全部有发布器(不再有 not_implemented)
        check("all seven channels have publishers",
              all(c.get("support") in ("browser", "api") for c in channels),
              [c["platform_key"] for c in channels if c.get("support") not in ("browser", "api")])
        # 测试模式(worker=0)下 connect 不派登录子进程,如实回 skipped
        status, body = post(f"/v1/channels/{by_key['xiaohongshu']['id']}/connect", {})
        check("connect skipped in smoke mode (no login subprocess)",
              status == 202 and body["status"] == "skipped", body)
        status, body = post(f"/v1/channels/{by_key['baijiahao']['id']}/connect", {})
        check("connect baijiahao points to key config",
              status == 400 and body["code"] == "NEEDS_CONFIG" and "baijiahao.local.json" in body["message"],
              body)

        status, body = get(f"/v1/trackers/{TRACKER}/articles")
        check("8 articles seeded", status == 200 and len(body["items"]) == 8, body)

        status, body = get(f"/v1/articles/{ART_PUBLISHED}/impact")
        check("impact status ok", body["status"] == "ok", body)
        check("impact before 22.22%", body["before"]["mention_rate"]["value"] == 0.2222, body["before"])
        check("impact after 40%", body["after"]["mention_rate"]["value"] == 0.4, body["after"])
        check("impact delta 17.8pp", body["delta_pp"] == 17.8, body)

        status, body = get(f"/v1/articles/{ART_DRAFT}/impact")
        check("unpublished article has no comparison", body["status"] == "not_published", body)

        status, body = post(f"/v1/articles/{ART_DRAFT}/publish", {"channel_ids": [disconnected[0]["id"]]})
        check(
            "disconnected channel rejected",
            status == 400 and body["code"] == "CHANNEL_NOT_CONNECTED",
            body,
        )

        status, body = post(f"/v1/articles/{ART_DRAFT}/publish", {"channel_ids": [connected[0]["id"]]})
        check("publish queues one channel", status == 202 and len(body["queued"]) == 1, body)
        check("publish is real integration (worker off in smoke)",
              body.get("integration") == "real" and body.get("worker") is False, body)

        status, body = post(f"/v1/articles/{ART_DRAFT}/publish", {"channel_ids": [connected[0]["id"]]})
        check(
            "republish does not duplicate",
            status == 202 and body["created"] == [] and len(body["existing"]) == 1,
            body,
        )

        # ---- H2-6/H2-7 收尾(003 US2) ----
        qsets = get(f"/v1/trackers/{TRACKER}/question-sets")[1]["items"]
        check("two question sets seeded", len([s for s in qsets if s["name"] in ("律所核心问题", "长尾扩展集")]) == 2, qsets)
        extra = next(s for s in qsets if s["name"] == "长尾扩展集")
        main_set_id = next(s for s in qsets if s["name"] == "律所核心问题")["id"]
        status, body = patch(f"/v1/trackers/{TRACKER}", {"active_question_set_id": extra["id"]})
        check("switch question set accepted", status == 200, body)
        status, body = get(f"/v1/trackers/{TRACKER}/questions")
        check("questions follow active set (12 -> 2)", len(body["items"]) == 2, body["summary"])
        status, body = get(f"/v1/trackers/{TRACKER}/metrics/overview?from=2026-11-01&to=2026-11-03")
        check("metrics follow active set (9 -> 0 samples)", body["valid_total"] == 0, body["valid_total"])
        # 切回主集:黄金窗口分毫不动(这是 H2-6 的硬验收)
        status, body = patch(f"/v1/trackers/{TRACKER}", {"active_question_set_id": main_set_id})
        check("switch back to main set", status == 200, body)
        status, body = get(f"/v1/trackers/{TRACKER}/metrics/overview?from=2026-11-01&to=2026-11-03")
        check("golden window restored after switch back", body["valid_total"] == 9, body["valid_total"])
        status, body = get(f"/v1/articles/{ART_PUBLISHED}/impact")
        check("golden impact 22.22% after switch back", body["before"]["mention_rate"]["value"] == 0.2222, body["before"])

        # H2-7:引用类型筛选的条数与手工 SQL 对拍
        import sqlite3 as _sq

        with _sq.connect(DB) as _c:
            _inline = _c.execute(
                "SELECT COUNT(*) FROM citation c JOIN answer a ON a.id=c.answer_id "
                "WHERE a.tracker_id = ? AND a.is_valid = 1 AND c.cite_type = 'inline'",
                (TRACKER,),
            ).fetchone()[0]
        status, body = get(f"/v1/trackers/{TRACKER}/citations?from=2026-11-01&to=2026-11-30&citeType=inline&limit=1")
        check("citation inline filter matches manual SQL",
              status == 200 and body["total_occurrences"] == _inline,
              {"api": body.get("total_occurrences"), "sql": _inline})
        status, body = get(f"/v1/trackers/{TRACKER}/citations?from=2026-11-01&to=2026-11-30&citeType=bogus")
        check("invalid citeType is 400", status == 400 and "citeType" in body["message"], body)

        # ---- 001 配置面 / 下钻 / 报告 ----
        status, ctype, html = get_html("/settings")
        check("settings page served", status == 200 and "监控配置" in html, {"status": status})
        status, ctype, html = get_html("/reports")
        check("reports page served", status == 200 and "报告中心" in html, {"status": status})
        status, ctype, html = get_html("/pilot")
        check("pilot page served", status == 200 and "试点结案" in html, {"status": status})

        status, body = get(f"/v1/trackers/{TRACKER}")
        check("tracker detail has aliases", status == 200 and len(body["brand"]["aliases"]) == 3, body.get("brand"))
        check("daily planned = 60", body["sampling"]["daily_planned"] == 60, body["sampling"])
        check(
            "failure rate over threshold",
            body["sampling"]["failure"]["status"] == "over_threshold"
            and body["sampling"]["failure"]["failed"] == 214,
            body["sampling"]["failure"],
        )

        status, body = get(f"/v1/trackers/{TRACKER}/metrics/avg_rank/samples?from=2026-11-01&to=2026-11-04")
        check("rank samples base 2", status == 200 and body["sample_base"] == 2, body.get("sample_base"))
        check(
            "no-list mention excluded from denominator",
            any(i["mentioned"] and not i["in_denominator"] for i in body["items"]),
            "a mention without list index must be flagged out",
        )

        status, body = get("/v1/meta/methodology")
        check("methodology 6 definitions", status == 200 and len(body["definitions"]) == 6, body)

        status, body = post(
            f"/v1/trackers/{TRACKER}/questions",
            {"text": "*沈阳哪一家律所最专业\n沈阳交通事故律师怎么收费"},
        )
        check(
            "question import dedupes",
            status == 201 and body["created_count"] == 1 and body["duplicate_count"] == 1,
            body,
        )
        question_id = body["created"][0]["id"]
        status, body = patch(f"/v1/questions/{question_id}", {"tier": "core"})
        check(
            "tier change effective tomorrow",
            status == 200 and body["effective_from"] > "2026-11-11" and body["history_unchanged"],
            body,
        )

        status, body = post("/v1/trackers", {"name": "冒烟对象", "brand_name": "冒烟品牌"})
        new_tracker = body.get("id")
        check("tracker created without questions", status == 201 and body["sampling"]["daily_planned"] == 0, body)
        status, body = patch(f"/v1/trackers/{new_tracker}", {"status": "archived"})
        check("tracker archived", status == 200 and body["status"] == "archived", body)
        status, body = patch(f"/v1/trackers/{new_tracker}", {"status": "active"})
        check("archived tracker cannot reactivate", status == 409 and body["code"] == "STATE_CONFLICT", body)

        status, body = get(f"/v1/trackers/{TRACKER}/answers?from=2026-11-01&to=2026-11-03&limit=1")
        answer_id = body["items"][0]["id"]
        status, body = get(f"/v1/answers/{answer_id}")
        check(
            "answer detail has text and citations",
            status == 200 and body["text"] and body["citations"] and body["text_hash"],
            {"status": status},
        )
        status, ctype, html = get_html(f"/answer/{answer_id}")
        check("answer page served", status == 200 and "答案详情" in html, {"status": status})

        # seed 必须自带演示报告：否则用户 --reset 后报告中心是空的，演示会断在这里。
        status, seeded = get(f"/v1/trackers/{TRACKER}/reports")
        check(
            "seed ships demo reports so /reports is never empty after --reset",
            status == 200 and len(seeded["items"]) >= 2
            and any(x["comparable"] is True for x in seeded["items"]),
            {"count": len(seeded.get("items", [])), "comparable": [x.get("comparable") for x in seeded.get("items", [])]},
        )

        status, body = post(f"/v1/trackers/{TRACKER}/reports", {"from": "2026-11-01", "to": "2026-11-03"})
        check("report generated", status == 201 and body["status"] == "ready", {"status": status})
        report_id = body["id"]
        check("report hash is sha256", len(body["payload_hash"]) == 64, body.get("payload_hash"))
        check(
            "first period reported incomparable",
            body["payload"]["comparison"]["comparable"] is False,
            body["payload"]["comparison"],
        )
        check(
            "single-day report rejected",
            post(f"/v1/trackers/{TRACKER}/reports", {"from": "2026-11-03", "to": "2026-11-03"})[0] == 400,
            "period_start must be < period_end",
        )

        status, ctype, html = get_html(f"/report/{report_id}")
        check("report page served", status == 200 and "GEO Insight" in html, {"status": status})
        status, body = get(f"/v1/reports/{report_id}")
        check("report snapshot readable", status == 200 and body["payload_hash"], {"status": status})

        status, again = post(f"/v1/trackers/{TRACKER}/reports", {"from": "2026-11-01", "to": "2026-11-03"})
        check(
            "repeated report generation reuses the snapshot",
            status == 201 and again["id"] == report_id and again.get("reused") is True,
            {"id": again.get("id"), "reused": again.get("reused")},
        )

        status, body = post(f"/v1/trackers/{TRACKER}/reports", {"from": "2026-11-04", "to": "2026-11-05"})
        comparison = body["payload"]["comparison"]
        check(
            "comparable report outputs delta",
            comparison["comparable"] is True and comparison["delta_pp"] is not None,
            comparison,
        )

        with urllib.request.urlopen(
            BASE + f"/v1/trackers/{TRACKER}/answers/export?from=2026-11-01&to=2026-11-03&format=csv", timeout=5
        ) as resp:
            raw = resp.read()
        check("csv export has BOM and header", raw[:3] == b"\xef\xbb\xbf" and b"question_text" in raw, raw[:80])

        print("\nALL SMOKE CHECKS PASSED")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
