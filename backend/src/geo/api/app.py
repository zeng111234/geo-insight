"""HTTP surface for the MVP slice.

Uses Starlette directly because FastAPI is not installable in this environment
(no package index access). Route shapes, status codes and payloads follow
``contracts/openapi.yaml``; only the framework differs.
"""

import csv
import io
import json
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

from geo import db
from geo.api import nav
from geo.domain import methodology as methodology_module
from geo.repositories import config_repository as config_repo
from geo.repositories import report_repository as report_repo
from geo.repositories import sqlite_repository as repo
from geo.services import (
    audit_service,
    auth_service,
    config_service,
    content_service,
    metric_service,
    report_service,
    sampling_service,
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


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request.state.request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex
        try:
            return await call_next(request)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse(
                {
                    "code": "INTERNAL_ERROR",
                    "message": str(exc),
                    "request_id": request.state.request_id,
                },
                status_code=500,
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
        request.query_params.get("error") == "1",
    )


async def login_submit(request: Request):
    # 直接用 parse_qs 解析 urlencoded 表单：Starlette 的 request.form()
    # 在 multipart 场景要额外依赖，而登录表单只有两个字段，不值得为它引包。
    raw = (await request.body()).decode("utf-8", "replace")
    form = {key: values[0] for key, values in parse_qs(raw, keep_blank_values=True).items()}
    next_url = _safe_next(form.get("next"))

    conn = db.connect()
    try:
        user = auth_service.authenticate(conn, form.get("email", ""), form.get("password", ""))
    finally:
        conn.close()

    if not user:
        return RedirectResponse(f"/login?error=1&next={quote(next_url)}", status_code=303)

    token, ttl = auth_service.create_session(user)
    response = RedirectResponse(next_url, status_code=303)
    # secure=False 因为本地是 http；上 https 后要同时打开 secure。
    response.set_cookie(
        settings.cookie_name,
        token,
        max_age=ttl,
        httponly=True,
        samesite="lax",
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
    return JSONResponse({"status": "ok", "metric_version": settings.metric_version})


async def list_trackers(request: Request):
    conn = db.connect()
    try:
        items = repo.list_trackers(conn, _tenant(request))
        for item in items:
            item["question_summary"] = repo.question_summary(conn, item["id"])
        return JSONResponse({"items": items, "page": {"page": 1, "limit": len(items), "total": len(items)}})
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

    conn = db.connect()
    try:
        tracker_id = request.path_params["tracker_id"]
        if not repo.get_tracker(conn, tracker_id, _tenant(request)):
            return _error(request, "NOT_FOUND", "tracker not found", 404)
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
        )
        return JSONResponse({"items": items, "page": {"page": page, "limit": limit, "total": total}})
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
        return JSONResponse(
            metric_service.build_citation_summary(
                conn,
                tracker_id,
                period[0],
                period[1],
                request.query_params.get("model"),
                request.query_params.get("channel"),
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
        detail = repo.url_dedup_citations(
            conn,
            tracker_id,
            period[0],
            period[1],
            limit=limit,
            offset=(page - 1) * limit,
            media_type=media_type,
        )
        return JSONResponse({
            "items": detail["items"],
            "media_type": media_type,
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
        return JSONResponse({"items": repo.list_channels(conn, _tenant(request))})
    finally:
        conn.close()


async def connect_channel(request: Request):
    conn = db.connect()
    try:
        channel = repo.get_channel(conn, request.path_params["channel_id"])
        if not channel or channel["tenant_id"] != _tenant(request):
            return _error(request, "NOT_FOUND", "channel not found", 404)
        updated = repo.connect_channel(conn, channel["id"], _now())
        return JSONResponse(
            {
                **updated,
                "integration": "demo",
                "note": "展示态：已置为已连接，未接入真实登录或授权",
            }
        )
    finally:
        conn.close()


async def list_articles(request: Request):
    conn = db.connect()
    try:
        tracker_id = request.path_params["tracker_id"]
        if not repo.get_tracker(conn, tracker_id, _tenant(request)):
            return _error(request, "NOT_FOUND", "tracker not found", 404)
        return JSONResponse({"items": repo.list_articles(conn, tracker_id, _tenant(request))})
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


async def publish_article(request: Request):
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
                blocked.append({"channel_id": channel_id, "channel": channel["platform_name"], "reason": "not_connected"})
            else:
                targets.append(channel)
        if blocked:
            return JSONResponse(
                {
                    "code": "CHANNEL_NOT_CONNECTED",
                    "message": "存在未连接或不存在的渠道，未发起任何发布",
                    "request_id": request.state.request_id,
                    "blocked": blocked,
                },
                status_code=400,
            )

        created, existing = repo.create_publications(conn, article_id, targets, _now())
        return JSONResponse(
            {
                "article_id": article_id,
                "status": "queued",
                "queued": [{"channel_id": c["id"], "channel": c["platform_name"]} for c in targets],
                "created": created,
                "existing": existing,
                "integration": "demo",
                "note": "展示态：已写入发布队列，未真实发帖",
            },
            status_code=202,
        )
    finally:
        conn.close()


async def article_impact(request: Request):
    try:
        window_days = int(request.query_params.get("windowDays", 3))
    except ValueError:
        return _error(request, "VALIDATION_ERROR", "windowDays must be an integer")
    if not 1 <= window_days <= 14:
        return _error(request, "VALIDATION_ERROR", "windowDays must be between 1 and 14")

    conn = db.connect()
    try:
        payload = content_service.publish_impact(conn, request.path_params["article_id"], window_days)
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
            updated = config_repo.update_tracker(
                conn,
                tracker_id,
                tenant_id,
                name=payload.get("name"),
                brand_name=payload.get("brand_name"),
                industry=payload.get("industry"),
                region=payload.get("region"),
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

    routes = [
        Route("/login", login_page),
        Route("/login", login_submit, methods=["POST"]),
        Route("/logout", logout, methods=["POST"]),
        Route("/", dashboard),
        Route("/studio", studio_page),
        Route("/settings", _page("settings.html", "settings")),
        Route("/reports", _page("reports.html", "reports")),
        Route("/report/{report_id}", _page("report.html", "reports")),
        Route("/answer/{answer_id}", _page("answer.html", "monitor")),
        Route("/monitor", _page("monitor.html", "monitor")),
        Route("/insight", _page("insight.html", "insight")),
        Route("/opportunities", _page("opportunities.html", "opportunities")),
        Route("/tasks", _page("tasks.html", "tasks")),
        Route("/publish", _page("publish.html", "publish")),
        Route("/assets", _page("assets.html", "assets")),
        Route("/sources", _page("sources.html", "sources")),
        Route("/favicon.ico", favicon),
        Route("/health", health),
        Route(f"{settings.api_prefix}/meta/methodology", methodology),
        Route(f"{settings.api_prefix}/trackers", list_trackers, methods=["GET"]),
        Route(f"{settings.api_prefix}/trackers", create_tracker, methods=["POST"]),
        Route(f"{settings.api_prefix}/trackers/{{tracker_id}}", tracker_detail, methods=["GET", "PATCH"]),
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
        Route(f"{settings.api_prefix}/questions/{{question_id}}", question_tier, methods=["PATCH"]),
        Route(f"{settings.api_prefix}/trackers/{{tracker_id}}/sampling-plan", sampling_plan),
        Route(
            f"{settings.api_prefix}/trackers/{{tracker_id}}/collection-health", collection_health
        ),
        Route(
            f"{settings.api_prefix}/trackers/{{tracker_id}}/metrics/by-question",
            metrics_by_question,
        ),
        Route(
            f"{settings.api_prefix}/trackers/{{tracker_id}}/metrics/sentiment-by-model",
            metrics_sentiment_by_model,
        ),
        Route(f"{settings.api_prefix}/trackers/{{tracker_id}}/metrics/overview", metrics_overview),
        Route(f"{settings.api_prefix}/trackers/{{tracker_id}}/metrics/trend", metrics_trend),
        Route(
            f"{settings.api_prefix}/trackers/{{tracker_id}}/metrics/{{metric}}/samples",
            metric_samples,
        ),
        Route(f"{settings.api_prefix}/trackers/{{tracker_id}}/answers", list_answers),
        Route(f"{settings.api_prefix}/trackers/{{tracker_id}}/answers/export", answers_export),
        Route(f"{settings.api_prefix}/trackers/{{tracker_id}}/citations/summary", citations_summary),
        Route(f"{settings.api_prefix}/trackers/{{tracker_id}}/citations", citations_detail),
        Route(f"{settings.api_prefix}/trackers/{{tracker_id}}/reports", list_reports, methods=["GET"]),
        Route(f"{settings.api_prefix}/trackers/{{tracker_id}}/reports", create_report, methods=["POST"]),
        Route(f"{settings.api_prefix}/reports/{{report_id}}", report_detail),
        Route(f"{settings.api_prefix}/answers/{{answer_id}}", answer_detail),
        Route(f"{settings.api_prefix}/trackers/{{tracker_id}}/articles", list_articles),
        Route(f"{settings.api_prefix}/channels", list_channels),
        Route(f"{settings.api_prefix}/channels/{{channel_id}}/connect", connect_channel, methods=["POST"]),
        Route(f"{settings.api_prefix}/articles/{{article_id}}", article_detail, methods=["GET", "PATCH"]),
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
        ],
    )


app = create_app()
