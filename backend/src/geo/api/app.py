"""HTTP surface for the MVP slice.

Uses Starlette directly because FastAPI is not installable in this environment
(no package index access). Route shapes, status codes and payloads follow
``contracts/openapi.yaml``; only the framework differs.
"""

import csv
import io
import json
import secrets
import time
import uuid
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, quote

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from geo import code_rev, db, observability
from geo.api import nav
from geo.domain import channels as channels_domain
from geo.domain import citation as citation_domain
from geo.domain import methodology as methodology_module
from geo.domain import relevance
from geo.repositories import config_repository as config_repo
from geo.repositories import report_repository as report_repo
from geo.repositories import sqlite_repository as repo
from geo.services import (
    article_stage_service,
    audit_service,
    auth_service,
    config_service,
    credit_service,
    content_service,
    mention_service,
    metric_service,
    opportunity_service,
    publish_service,
    report_service,
    sampling_service,
    tracker_service,
    update_service,
)
from geo.settings import settings

EXPORT_FIELDS = (
    "answer_id",
    "day",
    "collected_at",
    "question_id",
    "question_text",
    "tier",
    "model_key",
    "channel",
    "mentioned",
    "position",
    "sentiment",
    "snippet",
    "citation_count",
    "text",
)


# H4-5 最小可观测：进程内请求窗口。重启清零——不假装成持久化监控。
request_window = observability.RequestWindow()


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request.state.request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        except Exception as exc:  # noqa: BLE001
            return JSONResponse(
                {
                    "code": "INTERNAL_ERROR",
                    "message": str(exc),
                    "request_id": request.state.request_id,
                },
                status_code=500,
            )
        finally:
            # 成功、业务错误、未捕获异常三条路径都要记——漏了异常路径，
            # 错误率就永远偏低，出了问题看板反而一片绿。
            request_window.observe(
                request.method,
                request.url.path,
                status,
                int((time.perf_counter() - started) * 1000),
                request.state.request_id,
            )


# 登录页、健康检查、图标、静态资源不需要登录。
# /static 必须放行：登录页自己的 css 就在那儿，否则登录页会裸奔。
PUBLIC_PATHS = {"/login", "/health", "/favicon.ico"}
PUBLIC_PREFIXES = ("/static/",)


class AuthMiddleware(BaseHTTPMiddleware):
    """最简鉴权：有 session 放行，没有就按"接口 401 / 页面跳登录"处理。

    接口返回 401 而不是 302：调用方是脚本或 fetch，跟着 302 拿到一坨 HTML 更难排查。
    """

    async def dispatch(self, request: Request, call_next):
        if not settings.auth_required:
            return await call_next(request)

        path = request.url.path
        if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
            return await call_next(request)

        session = auth_service.get_session(request.cookies.get(settings.cookie_name))
        if session:
            request.state.user = session
            # CSRF 双提交校验需要会话里的 token(CSRF 中间件比对请求头)
            request.state.csrf = session.get("csrf_token")
            return await call_next(request)

        if path.startswith(settings.api_prefix):
            return JSONResponse(
                {
                    "code": "UNAUTHORIZED",
                    "message": "请先登录",
                    "request_id": getattr(request.state, "request_id", ""),
                },
                status_code=401,
            )
        target = "/login"
        if request.method in {"GET", "HEAD"}:
            target = f"/login?next={quote(path)}"
        return RedirectResponse(target, status_code=302)


WRITE_METHODS = {"POST", "PATCH", "PUT", "DELETE"}


class CsrfMiddleware(BaseHTTPMiddleware):
    """写接口校验 X-CSRF-Token(003 FR-005,补决策 010 自认的洞)。

    方案是「会话态 token + 双提交 cookie」:登录时签发 csrf_token 存进 session,
    并以非 HttpOnly cookie(geo_csrf)下发给页面 JS;写请求带 X-CSRF-Token 头,
    与 session 里的 token 比对。SameSite=None 的线上环境里,拿到会话 cookie 的
    第三方页面发起的写请求没有这个头,403 拒之。

    只对「已登录的写」生效:未登录的写已被 AuthMiddleware 401;/login 自身是
    公开路径(还没有会话,由登录限流负责)。鉴权整体关闭(自动化检查)时同样
    跳过——那套检查本来就不走登录态。
    """

    async def dispatch(self, request: Request, call_next):
        if not settings.auth_required:
            return await call_next(request)
        if request.method not in WRITE_METHODS:
            return await call_next(request)
        if getattr(request.state, "csrf", None) is None:
            return await call_next(request)  # 未登录的写:交给 401,不抢戏

        header = request.headers.get("x-csrf-token", "")
        if header and secrets.compare_digest(header, request.state.csrf):
            return await call_next(request)
        return JSONResponse(
            {
                "code": "CSRF_REJECTED",
                "message": "写请求缺少有效的 X-CSRF-Token(跨站请求)",
                "request_id": getattr(request.state, "request_id", ""),
            },
            status_code=403,
        )


def _safe_next(value: str | None) -> str:
    """只接受站内相对路径：挡住 ``//evil.com`` 这类开放重定向。"""
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/"
    return value


async def login_page(request: Request):
    if not settings.auth_required:
        return RedirectResponse("/", status_code=302)
    if auth_service.get_session(request.cookies.get(settings.cookie_name)):
        return RedirectResponse("/", status_code=302)
    return nav.render_login(
        STATIC_DIR,
        _safe_next(request.query_params.get("next")),
        settings.admin_email,
        request.query_params.get("error") if request.query_params.get("error") in ("1", "locked") else False,
        cooldown=int(request.query_params.get("cooldown") or 0),
    )


def _is_https(request: Request) -> bool:
    """判断这次请求在外网看来是不是 HTTPS。

    线上跑在反向代理后面：浏览器地址栏是 https，但 uvicorn 收到的 scheme 可能仍是 http，
    所以先看代理头 X-Forwarded-Proto，再退回真实 scheme。本地双击 start.bat 走 http，两者都是 false。
    """
    proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    return proto == "https" or request.url.scheme == "https"


async def login_submit(request: Request):
    # 直接用 parse_qs 解析 urlencoded 表单：Starlette 的 request.form()
    # 在 multipart 场景要额外依赖，而登录表单只有两个字段，不值得为它引包。
    raw = (await request.body()).decode("utf-8", "replace")
    form = {key: values[0] for key, values in parse_qs(raw, keep_blank_values=True).items()}
    next_url = _safe_next(form.get("next"))
    email = form.get("email", "")

    # 登录限流(FR-006):账号+IP 联合标识。锁定期内连正确密码都拒绝——
    # 否则攻击者可以拿正确密码撞锁来试探
    identifier = f"{email}@{request.client.host if request.client else '?'}"
    cooldown = auth_service.login_throttled(identifier)
    if cooldown:
        return RedirectResponse(
            f"/login?error=locked&cooldown={cooldown}&next={quote(next_url)}", status_code=303
        )

    conn = db.connect()
    try:
        user = auth_service.authenticate(conn, email, form.get("password", ""))
    finally:
        conn.close()

    if not user:
        auth_service.record_login_failure(identifier)
        return RedirectResponse(f"/login?error=1&next={quote(next_url)}", status_code=303)

    auth_service.clear_login_failures(identifier)
    token, ttl = auth_service.create_session(user)
    session = auth_service.get_session(token)
    response = RedirectResponse(next_url, status_code=303)
    # 本地 http 用 lax（浏览器不接受给 http 设 None），线上 https 必须 None + Secure。
    # 原因：发布后的应用是在应用卡的 iframe 里打开的，SameSite=lax 的 cookie 在第三方上下文
    # 不会被发送，登录成功后立刻被弹回登录页，看起来就是一页空白。
    # 代价：SameSite=None 失去它提供的 CSRF 防护。这个演示应用的写操作都要人主动点，
    # 真要对外用之前得补 CSRF token（记在决策 010）。
    is_https = _is_https(request)
    response.set_cookie(
        settings.cookie_name,
        token,
        max_age=ttl,
        httponly=True,
        samesite="none" if is_https else "lax",
        secure=is_https,
        path="/",
    )
    # CSRF 双提交的另一半:非 HttpOnly,页面 JS 读它放进 X-CSRF-Token 头。
    # 有效期跟会话一致(会话没了,token 也没意义了)
    response.set_cookie(
        settings.csrf_cookie_name,
        session["csrf_token"] if session else "",
        max_age=ttl,
        httponly=False,
        samesite="none" if is_https else "lax",
        secure=is_https,
        path="/",
    )
    return response


async def logout(request: Request):
    auth_service.destroy_session(request.cookies.get(settings.cookie_name))
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(settings.cookie_name, path="/")
    return response


def _tenant(request: Request) -> str:
    user = getattr(request.state, "user", None)
    if user and user.get("tenant_id"):
        # 登录后以会话里的租户为准。若继续信任 X-Tenant-Id，
        # 任何登录用户都能改个请求头看别人的数据。
        return user["tenant_id"]
    return request.headers.get("X-Tenant-Id", settings.default_tenant_id)


def _actor_id(request: Request) -> str | None:
    """审计里的操作人。鉴权关闭时没有会话，返回 None —— 不编造系统用户。"""
    user = getattr(request.state, "user", None)
    return user.get("user_id") if user else None


def _error(request: Request, code: str, message: str, status: int = 400) -> JSONResponse:
    return JSONResponse(
        {"code": code, "message": message, "request_id": request.state.request_id},
        status_code=status,
    )


def _period(request: Request) -> tuple[str, str] | JSONResponse:
    today = date.today()
    raw_from = request.query_params.get("from")
    raw_to = request.query_params.get("to")
    try:
        start = date.fromisoformat(raw_from) if raw_from else today - timedelta(days=6)
        end = date.fromisoformat(raw_to) if raw_to else today
    except ValueError:
        return _error(request, "VALIDATION_ERROR", "from/to must be YYYY-MM-DD")
    if start > end:
        return _error(request, "VALIDATION_ERROR", "from must be earlier than to")
    return start.isoformat(), end.isoformat()


def _frontend_dir() -> Path | None:
    """Locate frontend/static by walking up from this file.

    Serving the dashboard from the same origin as the API removes an entire
    class of failures: a page opened as file:// cannot call http://127.0.0.1
    in current browsers, which surfaced as "Failed to fetch".
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "frontend" / "static"
        if (candidate / "index.html").is_file():
            return candidate
    return None


STATIC_DIR = _frontend_dir()


async def dashboard(request: Request):
    return nav.render_page(STATIC_DIR, "index.html", "home")


async def favicon(request: Request):
    return Response(status_code=204)


async def studio_page(request: Request):
    return nav.render_page(STATIC_DIR, "studio.html", "studio")


async def health(request: Request):
    # code_rev 是进程启动时读到的 HEAD，代表这个进程加载的代码版本；
    # 与工作区 HEAD 不一致就说明服务是旧进程，检查脚本据此提示重启。
    return JSONResponse({
        "status": "ok",
        "metric_version": settings.metric_version,
        "code_rev": code_rev.code_rev(),
        "ops": request_window.stats(),
    })


async def list_trackers(request: Request):
    conn = db.connect()
    try:
        items = repo.list_trackers(conn, _tenant(request))
        for item in items:
            item["question_summary"] = repo.question_summary(conn, item["id"])
            # H2-6:核心词属于生效问题集(不再读 tracker 行的旧列)
            item["keywords"] = tracker_service.active_keywords(conn, item["id"])
        return JSONResponse({"items": items, "page": {"page": 1, "limit": len(items), "total": len(items)}})
    finally:
        conn.close()


async def collection_runs(request: Request):
    """采集任务列表：真实 run 记录 + 从 run_item 聚合的条目数。无记录返回空数组。"""
    period = _period(request)
    if isinstance(period, JSONResponse):
        return period
    conn = db.connect()
    try:
        tracker_id = request.path_params["tracker_id"]
        if not repo.get_tracker(conn, tracker_id, _tenant(request)):
            return _error(request, "NOT_FOUND", "tracker not found", 404)
        try:
            limit = min(200, max(1, int(request.query_params.get("limit", 50))))
        except ValueError:
            return _error(request, "VALIDATION_ERROR", "limit must be an integer")
        return JSONResponse(
            {"items": config_repo.run_list(conn, tracker_id, period[0], period[1], limit)}
        )
    finally:
        conn.close()


async def collection_health(request: Request):
    """采集健康：计划应采 vs 实际已采。没配采样计划时返回 configured=false。"""
    period = _period(request)
    if isinstance(period, JSONResponse):
        return period
    conn = db.connect()
    try:
        tracker_id = request.path_params["tracker_id"]
        if not repo.get_tracker(conn, tracker_id, _tenant(request)):
            return _error(request, "NOT_FOUND", "tracker not found", 404)
        return JSONResponse(
            sampling_service.collection_health(conn, tracker_id, period[0], period[1])
        )
    finally:
        conn.close()


async def metrics_by_question(request: Request):
    """分问题表现：样本数、提及率、平均位次。"""
    period = _period(request)
    if isinstance(period, JSONResponse):
        return period
    conn = db.connect()
    try:
        tracker_id = request.path_params["tracker_id"]
        if not repo.get_tracker(conn, tracker_id, _tenant(request)):
            return _error(request, "NOT_FOUND", "tracker not found", 404)
        return JSONResponse(
            metric_service.build_question_performance(conn, tracker_id, period[0], period[1])
        )
    finally:
        conn.close()


async def tracker_opportunities(request: Request):
    """按机会分排序：先写哪个问题（排序依据，不是效果预测）。"""
    period = _period(request)
    if isinstance(period, JSONResponse):
        return period
    limit = None
    raw_limit = request.query_params.get("limit")
    if raw_limit is not None:
        try:
            limit = int(raw_limit)
        except ValueError:
            return _error(request, "VALIDATION_ERROR", "limit must be an integer")
        if not 1 <= limit <= 100:
            return _error(request, "VALIDATION_ERROR", "limit must be between 1 and 100")
    conn = db.connect()
    try:
        tracker_id = request.path_params["tracker_id"]
        if not repo.get_tracker(conn, tracker_id, _tenant(request)):
            return _error(request, "NOT_FOUND", "tracker not found", 404)
        return JSONResponse(
            opportunity_service.build_opportunities(
                conn, tracker_id, period[0], period[1], limit=limit
            )
        )
    finally:
        conn.close()


async def metrics_sentiment_by_model(request: Request):
    """情感倾向按模型拆分。"""
    period = _period(request)
    if isinstance(period, JSONResponse):
        return period
    conn = db.connect()
    try:
        tracker_id = request.path_params["tracker_id"]
        if not repo.get_tracker(conn, tracker_id, _tenant(request)):
            return _error(request, "NOT_FOUND", "tracker not found", 404)
        return JSONResponse(
            metric_service.build_sentiment_by_model(conn, tracker_id, period[0], period[1])
        )
    finally:
        conn.close()


async def metrics_tab_counts(request: Request):
    """工作台 10 个 tab 的计数，一次给全（E1d：切筛选同步换数字）。"""
    period = _period(request)
    if isinstance(period, JSONResponse):
        return period
    conn = db.connect()
    try:
        tracker_id = request.path_params["tracker_id"]
        if not repo.get_tracker(conn, tracker_id, _tenant(request)):
            return _error(request, "NOT_FOUND", "tracker not found", 404)
        counts = repo.tab_count_rows(
            conn,
            tracker_id,
            period[0],
            period[1],
            request.query_params.get("questionId") or None,
            request.query_params.get("model") or None,
        )
        samples = counts["samples"] or 0
        return JSONResponse(
            {
                "items": [
                    {"tab": "dashboard", "count": counts["mentions"] or 0, "unit": "提及"},
                    {"tab": "answers", "count": samples, "unit": "条"},
                    {"tab": "metrics", "count": counts["questions"] or 0, "unit": "题"},
                    {"tab": "trend", "count": counts["days"] or 0, "unit": "天"},
                    {"tab": "competitors", "count": counts["competitor_mentions"] or 0, "unit": "次"},
                    {"tab": "sources", "count": counts["citations"] or 0, "unit": "条"},
                    {"tab": "sentiment", "count": counts["mentions"] or 0, "unit": "条"},
                    {"tab": "competitorStats", "count": counts["competitors"] or 0, "unit": "个"},
                    {"tab": "media", "count": counts["media_citations"] or 0, "unit": "条"},
                    {"tab": "health", "count": samples, "unit": "条"},
                ],
                "filters": {
                    "questionId": request.query_params.get("questionId") or "",
                    "model": request.query_params.get("model") or "",
                },
                "window": {"from": period[0], "to": period[1]},
            }
        )
    finally:
        conn.close()


async def metrics_overview(request: Request):
    period = _period(request)
    if isinstance(period, JSONResponse):
        return period
    conn = db.connect()
    try:
        tracker_id = request.path_params["tracker_id"]
        if not repo.get_tracker(conn, tracker_id, _tenant(request)):
            return _error(request, "NOT_FOUND", "tracker not found", 404)
        payload = metric_service.build_overview(
            conn,
            tracker_id,
            period[0],
            period[1],
            request.query_params.get("model"),
            request.query_params.get("channel"),
        )
        return JSONResponse(payload)
    finally:
        conn.close()


async def metrics_trend(request: Request):
    period = _period(request)
    if isinstance(period, JSONResponse):
        return period
    granularity = (request.query_params.get("granularity") or "day").strip().lower()
    if granularity not in {"day", "week"}:
        return _error(request, "VALIDATION_ERROR", "granularity must be day|week")
    conn = db.connect()
    try:
        tracker_id = request.path_params["tracker_id"]
        if not repo.get_tracker(conn, tracker_id, _tenant(request)):
            return _error(request, "NOT_FOUND", "tracker not found", 404)
        return JSONResponse(
            metric_service.build_trend(
                conn,
                tracker_id,
                period[0],
                period[1],
                request.query_params.get("model"),
                request.query_params.get("channel"),
                granularity,
            )
        )
    finally:
        conn.close()


async def list_answers(request: Request):
    period = _period(request)
    if isinstance(period, JSONResponse):
        return period
    scope = request.query_params.get("citationScope", "all")
    if scope not in {"all", "brand", "competitor", "media"}:
        return _error(request, "VALIDATION_ERROR", "citationScope must be all|brand|competitor|media")
    try:
        page = max(1, int(request.query_params.get("page", 1)))
        limit = min(100, max(1, int(request.query_params.get("limit", 20))))
    except ValueError:
        return _error(request, "VALIDATION_ERROR", "page/limit must be integers")
    try:
        cite_type = citation_domain.citation_type_filter(request.query_params.get("citeType"))
        cite_view = citation_domain.citation_view_filter(request.query_params.get("citeView"))
    except ValueError as e:
        return _error(request, "VALIDATION_ERROR", str(e))

    conn = db.connect()
    try:
        tracker_id = request.path_params["tracker_id"]
        if not repo.get_tracker(conn, tracker_id, _tenant(request)):
            return _error(request, "NOT_FOUND", "tracker not found", 404)
        tracker = repo.get_tracker(conn, tracker_id, _tenant(request))
        items, total = repo.list_answers(
            conn,
            tracker_id,
            period[0],
            period[1],
            request.query_params.get("model"),
            request.query_params.get("questionId"),
            scope,
            page,
            limit,
            cite_type=cite_type,
            cite_view=cite_view,
        )
        # 没有核心词时 off_topic 为 None（未判定），不能当成 False;
        # H2-6:核心词取当前生效问题集(切换问题集,跑题判定跟随)
        keywords = tracker_service.active_keywords(conn, tracker_id)
        for item in items:
            item["off_topic"] = relevance.is_off_topic(item.get("text"), keywords)
        return JSONResponse(
            {
                "items": items,
                "keywords": keywords,
                "cite_type": cite_type,
                "cite_view": cite_view,
                "monitoring_available": citation_domain.MONITORING_AVAILABLE,
                "page": {"page": page, "limit": limit, "total": total},
            }
        )
    finally:
        conn.close()


async def citations_summary(request: Request):
    period = _period(request)
    if isinstance(period, JSONResponse):
        return period
    conn = db.connect()
    try:
        tracker_id = request.path_params["tracker_id"]
        if not repo.get_tracker(conn, tracker_id, _tenant(request)):
            return _error(request, "NOT_FOUND", "tracker not found", 404)
        try:
            cite_type = citation_domain.citation_type_filter(request.query_params.get("citeType"))
        except ValueError as e:
            return _error(request, "VALIDATION_ERROR", str(e))
        return JSONResponse(
            metric_service.build_citation_summary(
                conn,
                tracker_id,
                period[0],
                period[1],
                request.query_params.get("model"),
                request.query_params.get("channel"),
                cite_type,
            )
        )
    finally:
        conn.close()


async def citations_detail(request: Request):
    """FR-020：内容明细按 URL 去重，次数按出现次数累计。"""
    period = _period(request)
    if isinstance(period, JSONResponse):
        return period
    conn = db.connect()
    try:
        tracker_id = request.path_params["tracker_id"]
        if not repo.get_tracker(conn, tracker_id, _tenant(request)):
            return _error(request, "NOT_FOUND", "tracker not found", 404)
        try:
            page = max(1, int(request.query_params.get("page", 1)))
            limit = min(100, max(1, int(request.query_params.get("limit", 100))))
        except ValueError:
            return _error(request, "VALIDATION_ERROR", "page/limit must be integers")
        # mediaType：视频/图文引用 tab 只需要某一类，在服务端过滤，
        # 否则前端得先拉全量再去重分页，页码会算错
        media_type = (request.query_params.get("mediaType") or "").strip() or None
        try:
            cite_type = citation_domain.citation_type_filter(request.query_params.get("citeType"))
        except ValueError as e:
            return _error(request, "VALIDATION_ERROR", str(e))
        detail = repo.url_dedup_citations(
            conn,
            tracker_id,
            period[0],
            period[1],
            limit=limit,
            offset=(page - 1) * limit,
            media_type=media_type,
            cite_type=cite_type,
        )
        return JSONResponse({
            "items": detail["items"],
            "media_type": media_type,
            "cite_type": cite_type,
            "total_urls": detail["total_urls"],
            "total_occurrences": detail["total_occurrences"],
            "page": {"page": page, "limit": limit, "total": detail["total_urls"]},
            "rule": "同一 URL 只显示一次；times 为累计引用次数；totals 为全量口径，items 按 page/limit 分页",
        })
    finally:
        conn.close()


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


async def _json_body(request: Request) -> dict | JSONResponse:
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - 空 body 与非法 JSON 同样处理
        return _error(request, "VALIDATION_ERROR", "request body must be JSON")
    if not isinstance(payload, dict):
        return _error(request, "VALIDATION_ERROR", "request body must be a JSON object")
    return payload


# ---------------------------------------------------------------------------
# 002 内容工作室
# ---------------------------------------------------------------------------

async def list_channels(request: Request):
    conn = db.connect()
    try:
        items = repo.list_channels(conn, _tenant(request))
        # support 告诉前端该渠道有没有真实发布器(browser/api);没有的渠道
        # 不渲染「连接」按钮,显示「未接入」
        for c in items:
            support = publish_service.CHANNEL_SUPPORT.get(c["platform_key"])
            c["support"] = support["kind"] if support else None
        return JSONResponse({"items": items})
    finally:
        conn.close()


async def connect_channel(request: Request):
    """真实连接(决策 011):浏览器渠道派生可见登录子进程,扫码后自动落库。

    HTTP 等不了扫码(分钟级交互),所以这里只负责:校验 → 置 connecting →
    派生子进程+监控线程 → 立刻回 202。监控线程按退出码把状态写成
    connected/disconnected 并释放渠道锁。
    """
    conn = db.connect()
    try:
        channel = repo.get_channel(conn, request.path_params["channel_id"])
        if not channel or channel["tenant_id"] != _tenant(request):
            return _error(request, "NOT_FOUND", "channel not found", 404)
    finally:
        conn.close()

    key = channel["platform_key"]
    support = publish_service.CHANNEL_SUPPORT.get(key)
    if support is None:
        return _error(request, "NOT_IMPLEMENTED", f"{channel['platform_name']} 还没有发布器,无法连接", 400)
    if support["kind"] == "api":
        return _error(
            request,
            "NEEDS_CONFIG",
            "百家号走 API 密钥:在 backend/data/baijiahao.local.json 配置"
            " app_id/app_token(后台「开发 → 基本配置」),然后点「检测」",
            400,
        )
    if not publish_service.try_channel_lock(key):
        return _error(request, "CHANNEL_BUSY", f"{channel['platform_name']} 正在登录或发布中,稍后再试", 409)

    # 测试模式(GEO_PUBLISH_WORKER=0)不派登录子进程:冒烟跑在 uvicorn 子进程里,
    # 不该真的弹浏览器;状态也不动,如实回 skipped
    if not settings.publish_worker_enabled:
        return JSONResponse(
            {
                "status": "skipped",
                "channel_id": channel["id"],
                "platform_name": channel["platform_name"],
                "integration": "real",
                "note": "登录派发已关闭(GEO_PUBLISH_WORKER=0,测试模式)",
            },
            status_code=202,
        )

    conn = db.connect()
    try:
        repo.update_channel_status(
            conn, channel["id"], "connecting", _now(),
            note="登录流程进行中:浏览器窗口已打开,请完成扫码登录",
        )
    finally:
        conn.close()
    publish_service.start_login(channel)
    return JSONResponse(
        {
            "status": "connecting",
            "channel_id": channel["id"],
            "platform_name": channel["platform_name"],
            "integration": "real",
            "note": "浏览器窗口已打开,请在其中完成扫码登录;完成后渠道状态会自动更新",
        },
        status_code=202,
    )


async def check_channel(request: Request):
    """真实检测登录态:开一次无头浏览器(10-15 秒),结果落库。

    耗时操作丢线程池,不阻塞事件循环;期间持有渠道锁,登录/发布会被 409。
    """
    conn = db.connect()
    try:
        channel = repo.get_channel(conn, request.path_params["channel_id"])
        if not channel or channel["tenant_id"] != _tenant(request):
            return _error(request, "NOT_FOUND", "channel not found", 404)
    finally:
        conn.close()

    key = channel["platform_key"]
    support = publish_service.CHANNEL_SUPPORT.get(key)
    if support is None:
        return _error(request, "NOT_IMPLEMENTED", f"{channel['platform_name']} 没有发布器,无需检测", 400)
    if not publish_service.try_channel_lock(key):
        return _error(request, "CHANNEL_BUSY", f"{channel['platform_name']} 正在登录或发布中,稍后再试", 409)
    try:
        ok, note = await run_in_threadpool(publish_service.run_channel_check, channel)
    finally:
        publish_service.release_channel_lock(key)

    # 失效状态的落库口径:连过又失效 → expired;从未连上 → disconnected;
    # 百家号缺密钥 → needs_config。已连接过与否看库里旧状态,不靠猜。
    if ok:
        status = "connected"
    elif support["kind"] == "api" and "未配置密钥" in note:
        status = "needs_config"
    elif channel["status"] in ("connected", "expired"):
        status = "expired"
    else:
        status = "disconnected"

    conn = db.connect()
    try:
        updated = repo.update_channel_status(
            conn, channel["id"], status, _now(),
            login_mode=support["kind"] if ok else None,
            note=note,
        )
    finally:
        conn.close()
    return JSONResponse({**updated, "reachable": ok})


async def list_articles(request: Request):
    conn = db.connect()
    try:
        tracker_id = request.path_params["tracker_id"]
        if not repo.get_tracker(conn, tracker_id, _tenant(request)):
            return _error(request, "NOT_FOUND", "tracker not found", 404)
        return JSONResponse({"items": repo.list_articles(conn, tracker_id, _tenant(request))})
    finally:
        conn.close()


async def create_article(request: Request):
    """新建草稿文章：手写的试点稿子也要进系统，才能发布、归因、判定。"""
    payload = await _json_body(request)
    if isinstance(payload, JSONResponse):
        return payload
    tracker_id = request.path_params["tracker_id"]
    tenant_id = _tenant(request)
    conn = db.connect()
    try:
        if not repo.get_tracker(conn, tracker_id, tenant_id):
            return _error(request, "NOT_FOUND", "tracker not found", 404)
        try:
            article = content_service.create_article(conn, tracker_id, tenant_id, payload)
        except content_service.ArticleInputError as exc:
            return _error(request, "VALIDATION_ERROR", str(exc))
        except content_service.QuestionNotFoundError as exc:
            return _error(request, "NOT_FOUND", str(exc), 404)
        return JSONResponse(article, status_code=201)
    finally:
        conn.close()


async def article_detail(request: Request):
    conn = db.connect()
    try:
        article_id = request.path_params["article_id"]
        article = repo.get_article(conn, article_id)
        if not article or article["tenant_id"] != _tenant(request):
            return _error(request, "NOT_FOUND", "article not found", 404)

        if request.method == "PATCH":
            payload = await _json_body(request)
            if isinstance(payload, JSONResponse):
                return payload
            title = payload.get("title", article["title"])
            body = payload.get("body", article["body"])
            if not isinstance(title, str) or not title.strip():
                return _error(request, "VALIDATION_ERROR", "title must be a non-empty string")
            if not isinstance(body, str):
                return _error(request, "VALIDATION_ERROR", "body must be a string")
            article = repo.update_article(conn, article_id, title.strip(), body, _now())

        return JSONResponse(article)
    finally:
        conn.close()


async def article_stages(request: Request):
    """六阶段流水线状态：GET 读，POST 切换/完成（action=set/complete）。"""
    article_id = request.path_params["article_id"]
    conn = db.connect()
    try:
        article = repo.get_article(conn, article_id)
        if not article or article["tenant_id"] != _tenant(request):
            return _error(request, "NOT_FOUND", "article not found", 404)

        if request.method == "GET":
            return JSONResponse(article_stage_service.get_stages(conn, article_id))

        payload = await _json_body(request)
        if isinstance(payload, JSONResponse):
            return payload
        action = payload.get("action")
        try:
            if action == "set":
                data = article_stage_service.set_current(conn, article_id, payload.get("stage"))
            elif action == "complete":
                data = article_stage_service.complete_current(conn, article_id)
            else:
                return _error(request, "VALIDATION_ERROR", "action must be set or complete")
        except article_stage_service.StageError as exc:
            code = 404 if exc.code == "UNKNOWN_STAGE" else 409
            return _error(request, exc.code, exc.message, code)
        conn.commit()
        return JSONResponse(data)
    finally:
        conn.close()


async def publish_article(request: Request):
    """真实发布(决策 011):校验 → 入队(幂等/可重试)→ 派发 worker → 秒回 202。

    worker 在后台线程跑浏览器自动化(分钟级),publication 状态会走
    queued → running → published/draft_saved/blocked/failed/unknown,
    前端轮询文章详情拿进度。渠道锁由本端点先拿、worker 收尾释放,
    同渠道并发会被 409(浏览器 profile 只有一份)。
    GEO_PUBLISH_WORKER=0 时不派发、不加锁,纯入队(冒烟/自动化用)。
    """
    payload = await _json_body(request)
    if isinstance(payload, JSONResponse):
        return payload
    channel_ids = payload.get("channel_ids")
    if not isinstance(channel_ids, list) or not channel_ids:
        return _error(request, "VALIDATION_ERROR", "channel_ids must be a non-empty array")

    conn = db.connect()
    try:
        article_id = request.path_params["article_id"]
        article = repo.get_article(conn, article_id)
        if not article or article["tenant_id"] != _tenant(request):
            return _error(request, "NOT_FOUND", "article not found", 404)

        targets, blocked = [], []
        for channel_id in channel_ids:
            channel = repo.get_channel(conn, str(channel_id))
            if not channel or channel["tenant_id"] != _tenant(request):
                blocked.append({"channel_id": channel_id, "reason": "not_found"})
            elif channel["status"] != "connected":
                blocked.append(
                    {"channel_id": channel_id, "channel": channel["platform_name"], "reason": channel["status"]}
                )
            else:
                title_err = publish_service.title_error(channel["platform_key"], article["title"])
                if title_err:
                    blocked.append(
                        {"channel_id": channel_id, "channel": channel["platform_name"], "reason": title_err}
                    )
                else:
                    targets.append(channel)
        if blocked:
            return JSONResponse(
                {
                    "code": "CHANNEL_NOT_CONNECTED",
                    "message": "存在未连接或不可发布的渠道，未发起任何发布",
                    "request_id": request.state.request_id,
                    "blocked": blocked,
                },
                status_code=400,
            )

        # 渠道互斥:全部渠道先拿锁再入队;任何一个忙就全部放弃(不产生半套记录)
        locked_keys = []
        if settings.publish_worker_enabled:
            for ch in targets:
                if publish_service.try_channel_lock(ch["platform_key"]):
                    locked_keys.append(ch["platform_key"])
                else:
                    for key in locked_keys:
                        publish_service.release_channel_lock(key)
                    return _error(
                        request, "CHANNEL_BUSY", f"{ch['platform_name']} 正在登录或发布中,稍后再试", 409
                    )

        created, requeued, existing = repo.create_publications(conn, article_id, targets, _now())
        to_dispatch = [*created, *requeued]
        pub_channels = repo.get_publication_channels(conn, to_dispatch) if to_dispatch else []
    finally:
        conn.close()

    if settings.publish_worker_enabled and pub_channels:
        key_of = {ch["id"]: ch["platform_key"] for ch in targets}
        for row in pub_channels:
            publish_service.spawn_publication_worker(row["id"], key_of[row["channel_id"]], article)

    return JSONResponse(
        {
            "article_id": article_id,
            "status": "queued",
            "queued": [{"channel_id": c["id"], "channel": c["platform_name"]} for c in targets],
            "created": created,
            "requeued": requeued,
            "existing": existing,
            "integration": "real",
            "worker": settings.publish_worker_enabled,
            "note": (
                "已派发真实发布(浏览器自动化);发布记录状态会持续更新,请稍后刷新查看"
                if settings.publish_worker_enabled
                else "发布 worker 已关闭,记录仅入队不执行(测试模式)"
            ),
        },
        status_code=202,
    )


async def article_impact(request: Request):
    try:
        window_days = int(request.query_params.get("windowDays", 3))
    except ValueError:
        return _error(request, "VALIDATION_ERROR", "windowDays must be an integer")
    if not 1 <= window_days <= 30:
        return _error(request, "VALIDATION_ERROR", "windowDays must be between 1 and 30")

    scope = request.query_params.get("scope", "tracker")
    if scope not in ("tracker", "question"):
        return _error(request, "VALIDATION_ERROR", "scope must be tracker or question")
    control_question_id = request.query_params.get("controlQuestionId") or None

    conn = db.connect()
    try:
        payload = content_service.publish_impact(
            conn,
            request.path_params["article_id"],
            window_days,
            scope=scope,
            control_question_id=control_question_id,
        )
        if payload["status"] == "not_found":
            return _error(request, "NOT_FOUND", "article not found", 404)
        return JSONResponse(payload)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 001 配置面：监控对象、别名、竞品、问题、采样计划（FR-001 ~ FR-012）
# ---------------------------------------------------------------------------

async def create_tracker(request: Request):
    payload = await _json_body(request)
    if isinstance(payload, JSONResponse):
        return payload
    conn = db.connect()
    try:
        try:
            created = config_service.create(conn, _tenant(request), payload, _now())
        except ValueError as exc:
            return _error(request, "VALIDATION_ERROR", str(exc))
        detail = config_service.tracker_detail(conn, created["tracker_id"], _tenant(request))
        return JSONResponse(detail, status_code=201)
    finally:
        conn.close()


async def tracker_detail(request: Request):
    conn = db.connect()
    try:
        tracker_id = request.path_params["tracker_id"]
        tenant_id = _tenant(request)

        if request.method == "PATCH":
            payload = await _json_body(request)
            if isinstance(payload, JSONResponse):
                return payload
            status = payload.get("status")
            if status is not None:
                ok, message = config_repo.set_tracker_status(conn, tracker_id, tenant_id, status, _now())
                if not ok:
                    code = "NOT_FOUND" if message == "tracker not found" else "STATE_CONFLICT"
                    return _error(request, code, message, 404 if code == "NOT_FOUND" else 409)
            for key in ("name", "brand_name"):
                if key in payload and not isinstance(payload[key], str):
                    return _error(request, "VALIDATION_ERROR", f"{key} must be a string")
            if "name" in payload and not payload["name"].strip():
                return _error(request, "VALIDATION_ERROR", "name must not be empty")
            # H2-6:切换生效问题集(校验归属);核心词从数组或逗号分隔字符串
            # 统一去重后写进**生效问题集**(不再写 tracker 行)
            if "active_question_set_id" in payload:
                try:
                    tracker_service.set_active_question_set(
                        conn, tracker_id, tenant_id, str(payload["active_question_set_id"])
                    )
                except tracker_service.QuestionSetError as e:
                    status_code = 404 if "not found" in str(e) else 400
                    return _error(request, "QUESTION_SET_ERROR", str(e), status_code)
            if "keywords" in payload:
                words = relevance.parse_keywords(payload["keywords"])
                error = relevance.validate_keywords(words)
                if error:
                    return _error(request, "VALIDATION_ERROR", error)
                try:
                    tracker_service.update_active_keywords(conn, tracker_id, tenant_id, words)
                except tracker_service.QuestionSetError as e:
                    return _error(request, "QUESTION_SET_ERROR", str(e), 400)
            updated = config_repo.update_tracker(
                conn,
                tracker_id,
                tenant_id,
                name=payload.get("name"),
                brand_name=payload.get("brand_name"),
                industry=payload.get("industry"),
                region=payload.get("region"),
                keywords=None,
                status=None,
                now=_now(),
            )
            if updated is None:
                return _error(request, "NOT_FOUND", "tracker not found", 404)

        detail = config_service.tracker_detail(conn, tracker_id, tenant_id)
        if detail is None:
            return _error(request, "NOT_FOUND", "tracker not found", 404)
        return JSONResponse(detail)
    finally:
        conn.close()


async def tracker_delete(request: Request):
    """删除监控对象：级联删掉它的答案、提及、引用、报告、文章与采样计划。

    这是全系统破坏性最强的操作——一次能带走几千行。三道闸：
    1. 租户校验（不匹配按 404 处理，不泄露对象是否存在）；
    2. 与审计同一事务，审计写失败整体回滚；
    3. 前端二次确认里要手打对象名（见 monitor.html），不是点一下「确定」。
    """
    conn = db.connect()
    try:
        tenant_id = _tenant(request)
        tracker_id = request.path_params["tracker_id"]
        removed = config_repo.delete_tracker(conn, tracker_id, tenant_id)
        if removed is None:
            return _error(request, "NOT_FOUND", "tracker not found", 404)

        audit_service.record(
            conn,
            tenant_id=tenant_id,
            actor_id=_actor_id(request),
            action="tracker.delete",
            object_type="tracker",
            object_id=tracker_id,
            # 表快照 + 删了多少行：事后要能回答「那次删掉了什么」
            before={**removed["tracker"], "deleted_rows": removed["deleted"]},
        )
        conn.commit()
        return Response(status_code=204)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


async def brand_profile(request: Request):
    conn = db.connect()
    try:
        profile = config_repo.get_brand_profile(conn, request.path_params["tracker_id"], _tenant(request))
        if not profile:
            return _error(request, "NOT_FOUND", "tracker not found", 404)
        return JSONResponse(profile)
    finally:
        conn.close()


async def brand_aliases(request: Request):
    payload = await _json_body(request)
    if isinstance(payload, JSONResponse):
        return payload
    alias = (payload.get("alias") or "").strip()
    if not alias:
        return _error(request, "VALIDATION_ERROR", "alias 必填")
    conn = db.connect()
    try:
        try:
            created = config_repo.add_alias(
                conn, request.path_params["brand_id"], alias, payload.get("alias_type") or "short"
            )
        except ValueError as exc:
            status = 404 if "not found" in str(exc) else 409
            return _error(request, "ALIAS_CONFLICT" if status == 409 else "NOT_FOUND", str(exc), status)
        return JSONResponse(created, status_code=201)
    finally:
        conn.close()


async def brand_alias_delete(request: Request):
    conn = db.connect()
    try:
        tenant_id = _tenant(request)
        brand_id = request.path_params["brand_id"]
        # 先验归属：接口按 id 删除，不校验的话知道 id 就能删别人的别名
        if config_repo.brand_tenant(conn, brand_id) != tenant_id:
            return _error(request, "NOT_FOUND", "brand not found", 404)

        removed = config_repo.remove_alias(
            conn, brand_id, request.path_params["alias_id"], commit=False
        )
        if removed is None:
            return _error(request, "NOT_FOUND", "alias not found", 404)

        audit_service.record(
            conn,
            tenant_id=tenant_id,
            actor_id=_actor_id(request),
            action="alias.delete",
            object_type="brand_alias",
            object_id=removed["id"],
            before=removed,
        )
        conn.commit()
        return Response(status_code=204)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


async def competitors_ranking(request: Request):
    """竞品排名：出现次数、占比、平均位次、情感分布。"""
    period = _period(request)
    if isinstance(period, JSONResponse):
        return period
    conn = db.connect()
    try:
        tracker_id = request.path_params["tracker_id"]
        if not repo.get_tracker(conn, tracker_id, _tenant(request)):
            return _error(request, "NOT_FOUND", "tracker not found", 404)
        return JSONResponse(
            metric_service.build_competitor_ranking(conn, tracker_id, period[0], period[1])
        )
    finally:
        conn.close()


async def competitors_cooccurrence(request: Request):
    """竞品共现矩阵：同一答案里同时出现的次数。"""
    period = _period(request)
    if isinstance(period, JSONResponse):
        return period
    conn = db.connect()
    try:
        tracker_id = request.path_params["tracker_id"]
        if not repo.get_tracker(conn, tracker_id, _tenant(request)):
            return _error(request, "NOT_FOUND", "tracker not found", 404)
        return JSONResponse(
            metric_service.build_competitor_cooccurrence(conn, tracker_id, period[0], period[1])
        )
    finally:
        conn.close()


async def competitors(request: Request):
    conn = db.connect()
    try:
        tracker_id = request.path_params["tracker_id"]
        if not repo.get_tracker(conn, tracker_id, _tenant(request)):
            return _error(request, "NOT_FOUND", "tracker not found", 404)
        if request.method == "GET":
            return JSONResponse(config_repo.list_competitors(conn, tracker_id))

        payload = await _json_body(request)
        if isinstance(payload, JSONResponse):
            return payload
        name = (payload.get("name") or "").strip()
        if not name:
            return _error(request, "VALIDATION_ERROR", "name 必填")
        try:
            created = config_repo.add_competitor(conn, tracker_id, name)
        except ValueError as exc:
            return _error(request, "COMPETITOR_CONFLICT", str(exc), 409)
        return JSONResponse(created, status_code=201)
    finally:
        conn.close()


async def competitor_delete(request: Request):
    conn = db.connect()
    try:
        tenant_id = _tenant(request)
        tracker_id = request.path_params["tracker_id"]
        if not repo.get_tracker(conn, tracker_id, tenant_id):
            return _error(request, "NOT_FOUND", "tracker not found", 404)

        removed = config_repo.remove_competitor(
            conn, tracker_id, request.path_params["competitor_id"], commit=False
        )
        if removed is None:
            return _error(request, "NOT_FOUND", "competitor not found", 404)

        audit_service.record(
            conn,
            tenant_id=tenant_id,
            actor_id=_actor_id(request),
            action="competitor.delete",
            object_type="competitor",
            object_id=removed["id"],
            before=removed,
        )
        conn.commit()
        return Response(status_code=204)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


async def question_sets(request: Request):
    """H2-6:问题集的列出与新建(一个监控对象可挂多个,切换走 tracker PATCH)。"""
    conn = db.connect()
    try:
        tracker_id = request.path_params["tracker_id"]
        tenant_id = _tenant(request)

        if request.method == "GET":
            items = tracker_service.list_question_sets(conn, tracker_id, tenant_id)
            if items is None:
                return _error(request, "NOT_FOUND", "tracker not found", 404)
            return JSONResponse({"items": items})

        payload = await _json_body(request)
        if isinstance(payload, JSONResponse):
            return payload
        try:
            created = tracker_service.create_question_set(
                conn, tracker_id, tenant_id,
                str(payload.get("name", "")), payload.get("keywords"), _now(),
            )
        except tracker_service.QuestionSetError as e:
            return _error(request, "QUESTION_SET_ERROR", str(e), 400)
        return JSONResponse(created, status_code=201)
    finally:
        conn.close()


async def question_set_detail(request: Request):
    """H2-6:问题集改名/改核心词。"""
    payload = await _json_body(request)
    if isinstance(payload, JSONResponse):
        return payload
    conn = db.connect()
    try:
        try:
            tracker_service.update_question_set(
                conn, request.path_params["set_id"], _tenant(request),
                payload.get("name"), payload.get("keywords"),
            )
        except tracker_service.QuestionSetError as e:
            status_code = 404 if "not found" in str(e) else 400
            return _error(request, "QUESTION_SET_ERROR", str(e), status_code)
        return JSONResponse({"id": request.path_params["set_id"], "status": "updated"})
    finally:
        conn.close()


async def questions(request: Request):
    conn = db.connect()
    try:
        tracker_id = request.path_params["tracker_id"]
        tenant_id = _tenant(request)
        if not repo.get_tracker(conn, tracker_id, tenant_id):
            return _error(request, "NOT_FOUND", "tracker not found", 404)

        if request.method == "GET":
            return JSONResponse(config_repo.list_questions(conn, tracker_id))

        payload = await _json_body(request)
        if isinstance(payload, JSONResponse):
            return payload
        items = payload.get("items")
        if not isinstance(items, list):
            text = payload.get("text")
            if not isinstance(text, str):
                return _error(request, "VALIDATION_ERROR", "需要 items 数组或 text 文本")
            default_tier = payload.get("default_tier") or "long_tail"
            if default_tier not in {"core", "long_tail"}:
                return _error(request, "VALIDATION_ERROR", "default_tier must be core|long_tail")
            items = []
            for raw in text.splitlines():
                line = raw.strip()
                if not line:
                    continue
                tier = default_tier
                if line.startswith("*") or line.startswith("＊"):
                    tier = "core"
                    line = line[1:].strip()
                if line:
                    items.append({"text": line, "tier": tier})
        if len(items) > 500:
            return _error(request, "VALIDATION_ERROR", "单次最多导入 500 条问题")
        try:
            result = config_repo.import_questions(conn, tracker_id, tenant_id, items, _now())
        except ValueError as exc:
            return _error(request, "VALIDATION_ERROR", str(exc))
        summary = config_repo.list_questions(conn, tracker_id)["summary"]
        return JSONResponse(
            {
                "created": result["created"],
                "duplicates": result["duplicates"],
                "created_count": len(result["created"]),
                "duplicate_count": len(result["duplicates"]),
                "summary": summary,
            },
            status_code=201,
        )
    finally:
        conn.close()


async def question_tier(request: Request):
    payload = await _json_body(request)
    if isinstance(payload, JSONResponse):
        return payload
    tier = payload.get("tier")
    if tier not in {"core", "long_tail"}:
        return _error(request, "VALIDATION_ERROR", "tier must be core|long_tail")
    conn = db.connect()
    try:
        try:
            updated = config_repo.update_question_tier(
                conn, request.path_params["question_id"], tier, _now()
            )
        except ValueError as exc:
            return _error(request, "VALIDATION_ERROR", str(exc))
        if updated is None:
            return _error(request, "NOT_FOUND", "question not found", 404)
        return JSONResponse(updated)
    finally:
        conn.close()


async def sampling_plan(request: Request):
    conn = db.connect()
    try:
        tracker_id = request.path_params["tracker_id"]
        if not repo.get_tracker(conn, tracker_id, _tenant(request)):
            return _error(request, "NOT_FOUND", "tracker not found", 404)
        return JSONResponse(
            config_service.sampling_overview(
                conn,
                tracker_id,
                request.query_params.get("from"),
                request.query_params.get("to"),
            )
        )
    finally:
        conn.close()


async def methodology(request: Request):
    return JSONResponse(methodology_module.methodology())


async def credits_overview(request: Request):
    """积分余额与流水。余额只从流水求和，不在别处缓存（决策 009）。"""
    conn = db.connect()
    try:
        tenant_id = _tenant(request)
        try:
            limit = min(200, max(1, int(request.query_params.get("limit", 50))))
        except ValueError:
            return _error(request, "VALIDATION_ERROR", "limit must be an integer")
        return JSONResponse(
            {
                "balance": credit_service.balance(conn, tenant_id),
                "entries": credit_service.entries(conn, tenant_id, limit),
                "unit": "分",
                "note": "人工发放的账本，不对外收费；余额 = 全部流水求和",
            }
        )
    finally:
        conn.close()


async def meta_channels(request: Request):
    """模型 × 端 16 渠道清单（决策 009 验收点）。不查库，纯口径。"""
    items = channels_domain.channel_list()
    return JSONResponse(
        {
            "items": items,
            "total": len(items),
            "available": sum(1 for x in items if x["available"]),
            "unavailable": sum(1 for x in items if not x["available"]),
        }
    )


async def metrics_channels(request: Request):
    """16 渠道的实际样本与提及；未接入的格子照实标出来。"""
    period = _period(request)
    if isinstance(period, JSONResponse):
        return period
    conn = db.connect()
    try:
        tracker_id = request.path_params["tracker_id"]
        if not repo.get_tracker(conn, tracker_id, _tenant(request)):
            return _error(request, "NOT_FOUND", "tracker not found", 404)
        return JSONResponse(
            metric_service.build_channel_matrix(conn, tracker_id, period[0], period[1])
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 001 下钻、答案详情、导出（FR-022 / FR-023 / FR-034）
# ---------------------------------------------------------------------------

async def metric_samples(request: Request):
    period = _period(request)
    if isinstance(period, JSONResponse):
        return period
    metric = request.path_params["metric"]
    if metric not in metric_service.SAMPLE_METRICS:
        return _error(
            request,
            "VALIDATION_ERROR",
            "metric must be " + "|".join(sorted(metric_service.SAMPLE_METRICS)),
        )
    conn = db.connect()
    try:
        tracker_id = request.path_params["tracker_id"]
        if not repo.get_tracker(conn, tracker_id, _tenant(request)):
            return _error(request, "NOT_FOUND", "tracker not found", 404)
        return JSONResponse(
            metric_service.build_samples(
                conn,
                tracker_id,
                period[0],
                period[1],
                metric,
                request.query_params.get("model"),
                request.query_params.get("channel"),
            )
        )
    finally:
        conn.close()


async def answer_detail(request: Request):
    conn = db.connect()
    try:
        detail = repo.get_answer_detail(conn, request.path_params["answer_id"], _tenant(request))
        if not detail:
            return _error(request, "NOT_FOUND", "answer not found", 404)
        return JSONResponse(detail)
    finally:
        conn.close()


async def answers_export(request: Request):
    period = _period(request)
    if isinstance(period, JSONResponse):
        return period
    fmt = request.query_params.get("format", "csv")
    if fmt not in {"csv", "jsonl"}:
        return _error(request, "VALIDATION_ERROR", "format must be csv|jsonl")
    conn = db.connect()
    try:
        tracker_id = request.path_params["tracker_id"]
        if not repo.get_tracker(conn, tracker_id, _tenant(request)):
            return _error(request, "NOT_FOUND", "tracker not found", 404)
        rows = repo.export_answer_rows(conn, tracker_id, period[0], period[1])
        filename = f"answers-{tracker_id[:8]}-{period[0]}_{period[1]}.{fmt}"
        if fmt == "jsonl":
            body = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
            content = body.encode("utf-8")
        else:
            buffer = io.StringIO()
            writer = csv.DictWriter(buffer, fieldnames=list(EXPORT_FIELDS))
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k) for k in EXPORT_FIELDS})
            # BOM 让 Excel 正确识别中文，否则打开是乱码。
            content = ("\ufeff" + buffer.getvalue()).encode("utf-8")
        return Response(
            content=content,
            media_type="text/csv; charset=utf-8" if fmt == "csv" else "application/x-ndjson; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 003 提及识别（FR-007/FR-008）：改别名后按当前配置一键重跑
# ---------------------------------------------------------------------------

async def mention_recompute(request: Request):
    conn = db.connect()
    try:
        result = mention_service.recompute(
            conn,
            tenant_id=_tenant(request),
            tracker_id=request.path_params["tracker_id"],
            actor_id=_actor_id(request),
            now=_now(),
        )
        if result is None:
            return _error(request, "NOT_FOUND", "tracker not found", 404)
        # 删旧提及、写新提及、审计在同一事务里提交（audit_service 的约定）。
        conn.commit()
        return JSONResponse(result)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 系统更新（「表哥同款」：Gitee 上的 update.json + zip，页内一键更新）
# ---------------------------------------------------------------------------

async def update_status(request: Request):
    """页面首屏信息：当前版本 + 更新源（只读，GET 不涉 CSRF）。"""
    source = update_service.read_source()
    return JSONResponse(
        {
            "current": update_service.current_version(),
            "source_url": source,
            "has_source": bool(source),
        }
    )


async def update_source_set(request: Request):
    payload = await _json_body(request)
    if isinstance(payload, JSONResponse):
        return payload
    try:
        url = update_service.write_source(payload.get("source_url", ""))
    except update_service.UpdateError as exc:
        return _error(request, "VALIDATION_ERROR", str(exc))
    return JSONResponse({"source_url": url})


async def update_check(request: Request):
    # 错误不抛——进 payload.error 给页面展示（检查失败不算接口失败）
    return JSONResponse(update_service.check())


async def update_apply(request: Request):
    try:
        result = update_service.apply_update()
    except update_service.UpdateError as exc:
        return _error(request, "UPDATE_FAILED", str(exc))
    # worker 已带着 2.5 秒宽限期起跑：这个 202 发完之后它才会停服务
    return JSONResponse(result, status_code=202)


# ---------------------------------------------------------------------------
# 001 报告（FR-027 ~ FR-031）
# ---------------------------------------------------------------------------

async def create_report(request: Request):
    payload = await _json_body(request)
    if isinstance(payload, JSONResponse):
        return payload
    start = payload.get("from")
    end = payload.get("to")
    if not start or not end:
        return _error(request, "VALIDATION_ERROR", "from/to 必填")
    try:
        d1, d2 = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        return _error(request, "VALIDATION_ERROR", "from/to must be YYYY-MM-DD")
    if d1 >= d2:
        return _error(request, "VALIDATION_ERROR", "报告周期至少 2 天（period_start < period_end）")
    if (d2 - d1).days > 92:
        return _error(request, "VALIDATION_ERROR", "报告周期最长 92 天")

    conn = db.connect()
    try:
        tracker_id = request.path_params["tracker_id"]
        if not repo.get_tracker(conn, tracker_id, _tenant(request)):
            return _error(request, "NOT_FOUND", "tracker not found", 404)
        started = time.perf_counter()
        report = report_service.generate(
            conn, tracker_id, _tenant(request), d1.isoformat(), d2.isoformat(), _now()
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if report is None:
            return _error(request, "NOT_FOUND", "tracker not found", 404)
        report["generation"] = {"mode": "sync_storage", "duration_ms": elapsed_ms}
        return JSONResponse(report, status_code=201)
    finally:
        conn.close()


async def list_reports(request: Request):
    conn = db.connect()
    try:
        tracker_id = request.path_params["tracker_id"]
        if not repo.get_tracker(conn, tracker_id, _tenant(request)):
            return _error(request, "NOT_FOUND", "tracker not found", 404)
        return JSONResponse({"items": report_repo.list_reports(conn, tracker_id, _tenant(request))})
    finally:
        conn.close()


async def report_detail(request: Request):
    conn = db.connect()
    try:
        report = report_repo.get_report(conn, request.path_params["report_id"], _tenant(request))
        if not report:
            return _error(request, "NOT_FOUND", "report not found", 404)
        return JSONResponse(report)
    finally:
        conn.close()


def _page(name: str, active: str | None = None):
    async def handler(request: Request):
        return nav.render_page(STATIC_DIR, name, active or Path(name).stem)

    return handler


def create_app() -> Starlette:
    db.init_db()
    # 老库升级上来时 app_user 可能还没有密码，这里补齐，否则登录页永远进不去
    with closing(db.connect()) as conn:
        auth_service.ensure_admin_password(conn)
        # 发布通道真实化(决策 011):上一个进程留下的 running / connecting
        # 都没有 worker 在管了(线程随进程死),开机即纠正——页面绝不能
        # 显示"发布中/登录中"而实际什么都不会发生
        repo.sweep_stale_publications(conn, "服务重启,发布中断;请重新发布")
        repo.sweep_stale_connecting(conn, "服务重启,登录流程中断;请重新连接")

    routes = [
        Route("/login", login_page),
        Route("/login", login_submit, methods=["POST"]),
        Route("/logout", logout, methods=["POST"]),
        Route("/", dashboard),
        Route("/studio", studio_page),
        Route("/settings", _page("settings.html", "monitor")),
        Route("/reports", _page("reports.html", "reports")),
        Route("/pilot", _page("pilot.html", "reports")),
        Route("/report/{report_id}", _page("report.html", "reports")),
        Route("/answer/{answer_id}", _page("answer.html", "monitor")),
        Route("/monitor", _page("monitor.html", "monitor")),
        Route("/insight", _page("insight.html", "insight")),
        Route("/opportunities", _page("opportunities.html", "opportunities")),
        Route("/tasks", _page("tasks.html", "tasks")),
        Route("/publish", _page("publish.html", "publish")),
        Route("/assets", _page("assets.html", "assets")),
        Route("/sources", _page("sources.html", "sources")),
        Route("/update", _page("update.html")),
        Route("/favicon.ico", favicon),
        Route("/health", health),
        Route(f"{settings.api_prefix}/meta/methodology", methodology),
        Route(f"{settings.api_prefix}/meta/channels", meta_channels),
        Route(f"{settings.api_prefix}/credits", credits_overview),
        Route(
            f"{settings.api_prefix}/trackers/{{tracker_id}}/metrics/channels",
            metrics_channels,
        ),
        Route(f"{settings.api_prefix}/trackers", list_trackers, methods=["GET"]),
        Route(f"{settings.api_prefix}/trackers", create_tracker, methods=["POST"]),
        Route(f"{settings.api_prefix}/trackers/{{tracker_id}}", tracker_detail, methods=["GET", "PATCH"]),
        # 同路径单独一条路由：DELETE 的语义和 GET/PATCH 完全不同，不塞进同一个函数
        Route(f"{settings.api_prefix}/trackers/{{tracker_id}}", tracker_delete, methods=["DELETE"]),
        Route(f"{settings.api_prefix}/trackers/{{tracker_id}}/brand", brand_profile),
        Route(f"{settings.api_prefix}/brands/{{brand_id}}/aliases", brand_aliases, methods=["POST"]),
        Route(
            f"{settings.api_prefix}/brands/{{brand_id}}/aliases/{{alias_id}}",
            brand_alias_delete,
            methods=["DELETE"],
        ),
        Route(
            f"{settings.api_prefix}/trackers/{{tracker_id}}/competitors/ranking",
            competitors_ranking,
        ),
        Route(
            f"{settings.api_prefix}/trackers/{{tracker_id}}/competitors/cooccurrence",
            competitors_cooccurrence,
        ),
        Route(f"{settings.api_prefix}/trackers/{{tracker_id}}/competitors", competitors, methods=["GET", "POST"]),
        Route(
            f"{settings.api_prefix}/trackers/{{tracker_id}}/competitors/{{competitor_id}}",
            competitor_delete,
            methods=["DELETE"],
        ),
        Route(f"{settings.api_prefix}/trackers/{{tracker_id}}/questions", questions, methods=["GET", "POST"]),
        Route(f"{settings.api_prefix}/trackers/{{tracker_id}}/question-sets", question_sets, methods=["GET", "POST"]),
        Route(f"{settings.api_prefix}/question-sets/{{set_id}}", question_set_detail, methods=["PATCH"]),
        Route(f"{settings.api_prefix}/questions/{{question_id}}", question_tier, methods=["PATCH"]),
        Route(f"{settings.api_prefix}/trackers/{{tracker_id}}/sampling-plan", sampling_plan),
        Route(
            f"{settings.api_prefix}/trackers/{{tracker_id}}/collection-health", collection_health,
        ),
        Route(
            f"{settings.api_prefix}/trackers/{{tracker_id}}/runs", collection_runs
        ),
        Route(
            f"{settings.api_prefix}/trackers/{{tracker_id}}/metrics/by-question",
            metrics_by_question,
        ),
        Route(
            f"{settings.api_prefix}/trackers/{{tracker_id}}/opportunities", tracker_opportunities
        ),
        Route(
            f"{settings.api_prefix}/trackers/{{tracker_id}}/metrics/sentiment-by-model",
            metrics_sentiment_by_model,
        ),
        Route(
            f"{settings.api_prefix}/trackers/{{tracker_id}}/metrics/tab-counts",
            metrics_tab_counts,
        ),
        Route(f"{settings.api_prefix}/trackers/{{tracker_id}}/metrics/overview", metrics_overview),
        Route(f"{settings.api_prefix}/trackers/{{tracker_id}}/metrics/trend", metrics_trend),
        Route(
            f"{settings.api_prefix}/trackers/{{tracker_id}}/metrics/{{metric}}/samples",
            metric_samples,
        ),
        Route(f"{settings.api_prefix}/trackers/{{tracker_id}}/answers", list_answers),
        Route(f"{settings.api_prefix}/trackers/{{tracker_id}}/answers/export", answers_export),
        Route(
            f"{settings.api_prefix}/trackers/{{tracker_id}}/mentions/recompute",
            mention_recompute,
            methods=["POST"],
        ),
        Route(f"{settings.api_prefix}/system/update/status", update_status, methods=["GET"]),
        Route(f"{settings.api_prefix}/system/update/source", update_source_set, methods=["POST"]),
        Route(f"{settings.api_prefix}/system/update/check", update_check, methods=["POST"]),
        Route(f"{settings.api_prefix}/system/update/apply", update_apply, methods=["POST"]),
        Route(f"{settings.api_prefix}/trackers/{{tracker_id}}/citations/summary", citations_summary),
        Route(f"{settings.api_prefix}/trackers/{{tracker_id}}/citations", citations_detail),
        Route(f"{settings.api_prefix}/trackers/{{tracker_id}}/reports", list_reports, methods=["GET"]),
        Route(f"{settings.api_prefix}/trackers/{{tracker_id}}/reports", create_report, methods=["POST"]),
        Route(f"{settings.api_prefix}/reports/{{report_id}}", report_detail),
        Route(f"{settings.api_prefix}/answers/{{answer_id}}", answer_detail),
        Route(f"{settings.api_prefix}/trackers/{{tracker_id}}/articles", list_articles),
        Route(
            f"{settings.api_prefix}/trackers/{{tracker_id}}/articles",
            create_article,
            methods=["POST"],
        ),
Route(f"{settings.api_prefix}/channels", list_channels),
Route(f"{settings.api_prefix}/channels/{{channel_id}}/connect", connect_channel, methods=["POST"]),
Route(f"{settings.api_prefix}/channels/{{channel_id}}/check", check_channel, methods=["POST"]),
        Route(f"{settings.api_prefix}/articles/{{article_id}}", article_detail, methods=["GET", "PATCH"]),
        Route(
            f"{settings.api_prefix}/articles/{{article_id}}/stages",
            article_stages,
            methods=["GET", "POST"],
        ),
        Route(f"{settings.api_prefix}/articles/{{article_id}}/publish", publish_article, methods=["POST"]),
        Route(f"{settings.api_prefix}/articles/{{article_id}}/impact", article_impact),
    ]
    if STATIC_DIR is not None:
        routes.append(Mount("/static", app=StaticFiles(directory=STATIC_DIR), name="static"))
    return Starlette(
        routes=routes,
        middleware=[
            # 顺序有讲究：RequestContext 最外层保证任何响应都有 request_id；
            # CORS 在 Auth 外面，预检请求不会被鉴权拦掉；Auth 最内层。
            Middleware(RequestContextMiddleware),
            # Same-origin serving makes CORS unnecessary; kept so the page still
            # works when opened from another local port during debugging.
            Middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]),
            Middleware(AuthMiddleware),
            Middleware(CsrfMiddleware),
        ],
    )


app = create_app()
