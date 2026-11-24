# -*- coding: utf-8 -*-
"""渠道发布编排(决策 011:发布通道真实化)。

把 CLI 发布器(`scripts/{sohu,zhihu,wechat}_publish.py` → `geo.publishers.*`,
`scripts/baijiahao_check.py` → 开放平台 API)接进 Web 应用:

- CHANNEL_SUPPORT:哪些 platform_key 有真实发布器、走哪条路线、标题长度区间
- markdown_to_html:article.body → content_html(各渠道编辑器通吃的子集)
- run_channel_check:真实检测登录态(同步子进程,调用方持锁并落库)
- start_login:派生可见登录子进程(扫码要人),监控线程按退出码落库
- dispatch_publications:发布 worker(draft → CLI publish → 解析 JSON → 落库)

进程模型(app 第一次引入子进程 + 线程,全部标准库):
- 登录是交互式的(扫码),HTTP 等不了 → 子进程独立跑,监控线程收尾;
- 发布是分钟级的(浏览器自动化)→ 202 先回,worker 跑完落库,前端轮询;
- 同一渠道的浏览器 profile 只有一份,check/login/publish 必须互斥:
  进程内 per-channel 锁,busy 时调用方回 409。单进程部署,进程内锁够用;
  多进程是 H4 的事,不提前设计。

测试缝:`_run_cli` 是唯一的子进程出口,测试把它换成假实现,不打真浏览器;
`GEO_PUBLISH_WORKER=0` 时发布走「纯入队」语义(冒烟脚本用)。
"""
from __future__ import annotations

import html as html_mod
import json
import re
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from geo import db
from geo.repositories import sqlite_repository as repo
from geo.settings import settings

BACKEND_DIR = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = BACKEND_DIR / "scripts"
DRAFT_DIR = BACKEND_DIR / "data" / "publish_drafts"

# 渠道支持表。publish_args 是 CLI 的发布参数形态:浏览器渠道用子命令
# `publish <draft>`,百家号用 argparse 标志 `--publish <draft>`。
# 抖音/小红书是图文平台:draft 必须带 images(发布器侧校验,缺失即 rejected)。
# 头条/小红书 2026-11-24 本机网络被重置,模块就位但登录/发布待网络恢复后校准。
CHANNEL_SUPPORT: dict[str, dict[str, Any]] = {
    "sohu": {
        "script": "sohu_publish.py", "kind": "browser",
        "title_min": 5, "title_max": 72, "publish_args": ("publish",),
    },
    "zhihu": {
        "script": "zhihu_publish.py", "kind": "browser",
        "title_min": 2, "title_max": 100, "publish_args": ("publish",),
    },
    "wechat": {
        "script": "wechat_publish.py", "kind": "browser",
        "title_min": 1, "title_max": 64, "publish_args": ("publish",),
    },
    "baijiahao": {
        "script": "baijiahao_check.py", "kind": "api",
        "title_min": 8, "title_max": 40, "publish_args": ("--publish",),
    },
    "douyin": {
        "script": "douyin_publish.py", "kind": "browser",
        "title_min": 1, "title_max": 55, "publish_args": ("publish",),
    },
    "toutiao": {
        "script": "toutiao_publish.py", "kind": "browser",
        "title_min": 5, "title_max": 30, "publish_args": ("publish",),
    },
    "xiaohongshu": {
        "script": "xiaohongshu_publish.py", "kind": "browser",
        "title_min": 1, "title_max": 20, "publish_args": ("publish",),
    },
}

CHECK_TIMEOUT_S = 90
LOGIN_TIMEOUT_S = 900  # 登录流程内部最长等 5 分钟扫码,加浏览器启停余量

# 渠道名 → 中文说明(busy/未接入时给用户看)
CHANNEL_NAMES = {
    "sohu": "搜狐号", "zhihu": "知乎", "wechat": "微信公众号", "baijiahao": "百家号",
    "douyin": "抖音", "toutiao": "今日头条", "xiaohongshu": "小红书",
}

# ---------------------------------------------------------------------------
# 进程内互斥
# ---------------------------------------------------------------------------

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _channel_lock(key: str) -> threading.Lock:
    with _locks_guard:
        if key not in _locks:
            _locks[key] = threading.Lock()
        return _locks[key]


def try_channel_lock(key: str) -> bool:
    """非阻塞拿锁;拿不到说明该渠道正有浏览器操作(check/login/publish)。"""
    return _channel_lock(key).acquire(blocking=False)


def release_channel_lock(key: str) -> None:
    _channel_lock(key).release()


def channel_busy(key: str) -> bool:
    locked = try_channel_lock(key)
    if locked:
        release_channel_lock(key)
    return not locked


# ---------------------------------------------------------------------------
# 正文转换
# ---------------------------------------------------------------------------

_ORDERED_RE = re.compile(r"^(\d+)[.、]\s*")


def _is_ordered(line: str) -> bool:
    return bool(_ORDERED_RE.match(line.strip()))


def markdown_to_html(body: str) -> str:
    """article.body 的轻量 Markdown → HTML(编辑器通吃的子集)。

    只覆盖工作台正文实际用到的形态:空行分段、## 小标题、有序列表、
    段内换行。不引完整 Markdown 库(零依赖原则,决策 002);
    出现新的语法需求时再扩这里,而不是换库。
    """
    blocks = [b.strip() for b in body.split("\n\n") if b.strip()]
    parts: list[str] = []
    for block in blocks:
        lines = block.split("\n")
        if lines[0].startswith("## "):
            parts.append(f"<h2>{html_mod.escape(lines[0][3:].strip())}</h2>")
        elif all(_is_ordered(line) for line in lines):
            items = "".join(
                f"<li>{html_mod.escape(_ORDERED_RE.sub('', line.strip()))}</li>" for line in lines
            )
            parts.append(f"<ol>{items}</ol>")
        else:
            parts.append(f"<p>{html_mod.escape(block).replace(chr(10), '<br>')}</p>")
    return "\n".join(parts)


def title_error(channel_key: str, title: str) -> str | None:
    """派发前的标题长度预检:错误信息或 None。秒级反馈,不等浏览器跑一半。"""
    support = CHANNEL_SUPPORT.get(channel_key)
    if not support:
        return f"{CHANNEL_NAMES.get(channel_key, channel_key)} 没有发布器,无法发布"
    if not support["title_min"] <= len(title) <= support["title_max"]:
        return (
            f"标题长度 {len(title)} 不在 {support['title_min']}-{support['title_max']} 字区间"
            f"({CHANNEL_NAMES.get(channel_key, channel_key)})"
        )
    return None


# ---------------------------------------------------------------------------
# 子进程出口(测试缝:换这个函数就全部换成假实现)
# ---------------------------------------------------------------------------


def _run_cli(script: str, *args: str, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / script), *args],
        cwd=str(BACKEND_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _tail(text: str, limit: int = 200) -> str:
    """子进程输出的最后几行,给用户看原因。"""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return " / ".join(lines[-2:])[:limit] if lines else ""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 登录态检测
# ---------------------------------------------------------------------------


def run_channel_check(channel: dict) -> tuple[bool, str]:
    """真实检测一个渠道的登录态,返回 (有效?, 附注)。调用方负责锁与落库。

    百家号走 API:先看密钥文件在不在,不在直接给配置指引(不用跑子进程)。
    """
    key = channel["platform_key"]
    support = CHANNEL_SUPPORT.get(key)
    if support is None:
        return False, "该渠道没有发布器(未接入)"
    if support["kind"] == "api" and not (BACKEND_DIR / "data" / "baijiahao.local.json").exists():
        return False, "未配置密钥:backend/data/baijiahao.local.json(后台「开发→基本配置」复制 app_id/app_token)"
    try:
        # baijiahao_check.py 是 argparse 标志式(默认动作就是 check),
        # 浏览器渠道是子命令式,所以这里按 kind 分叉
        proc = (
            _run_cli(support["script"], timeout=CHECK_TIMEOUT_S)
            if support["kind"] == "api"
            else _run_cli(support["script"], "check", timeout=CHECK_TIMEOUT_S)
        )
    except subprocess.TimeoutExpired:
        return False, "检测超时(90 秒)"
    tail = _tail(proc.stdout) or _tail(proc.stderr)
    if proc.returncode == 0:
        return True, tail or "登录态有效"
    return False, tail or "登录态失效(退出码非 0)"


# ---------------------------------------------------------------------------
# 登录(交互式,派生子进程)
# ---------------------------------------------------------------------------


def start_login(channel: dict) -> None:
    """派生可见登录子进程并起监控线程。调用方必须已持有该渠道的锁,
    监控线程在收尾时落库并释放锁。
    """
    key = channel["platform_key"]
    support = CHANNEL_SUPPORT[key]
    proc = subprocess.Popen(  # noqa: S603 - 参数来自 CHANNEL_SUPPORT 白名单
        [sys.executable, str(SCRIPTS_DIR / support["script"]), "login"],
        cwd=str(BACKEND_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    threading.Thread(
        target=_monitor_login, args=(key, channel["id"], proc), daemon=True, name=f"login-{key}"
    ).start()


def _monitor_login(key: str, channel_id: str, proc: subprocess.Popen) -> None:
    ok, note = False, "登录未完成"
    try:
        out, _ = proc.communicate(timeout=LOGIN_TIMEOUT_S)
        ok = proc.returncode == 0
        note = _tail(out) or ("登录成功" if ok else "登录未完成(退出码非 0)")
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        note = "登录超时(15 分钟)"
    except Exception as e:  # noqa: BLE001  # 监控线程不能让异常把锁带进坟墓
        note = f"登录监控异常: {e}"
    finally:
        try:
            conn = db.connect()
            try:
                repo.update_channel_status(
                    conn,
                    channel_id,
                    "connected" if ok else "disconnected",
                    _now(),
                    login_mode=CHANNEL_SUPPORT[key]["kind"] if ok else None,
                    note=note,
                )
            finally:
                conn.close()
        finally:
            release_channel_lock(key)


# ---------------------------------------------------------------------------
# 发布派发
# ---------------------------------------------------------------------------


def dispatch_publication(pub_id: str, channel_key: str, article: dict) -> None:
    """发布 worker 线程主体:queued → running → 终态。调用方持锁,这里收尾释放。

    draft.json 落在 data/publish_drafts/<pub_id>.json,留档可追溯(出问题时
    能复现当时发给渠道的内容)。
    """
    try:
        conn = db.connect()
        try:
            repo.update_publication_status(conn, pub_id, "running")
        finally:
            conn.close()

        support = CHANNEL_SUPPORT[channel_key]
        DRAFT_DIR.mkdir(parents=True, exist_ok=True)
        draft_path = DRAFT_DIR / f"{pub_id}.json"
        draft = {"title": article["title"], "content_html": markdown_to_html(article["body"])}
        draft_path.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")

        try:
            proc = _run_cli(
                support["script"], *support["publish_args"], str(draft_path),
                timeout=settings.publish_timeout_s,
            )
            result = _extract_result_json(proc.stdout)
            if result is None:
                # 百家号 CLI 不打 JSON 信封,按退出码走;浏览器渠道拿不到
                # 信封说明流程中断,同样如实记失败原因
                note = _tail(proc.stdout) or _tail(proc.stderr) or f"退出码 {proc.returncode}"
                if support["kind"] == "api" and proc.returncode == 0:
                    status, extra = "unknown", {
                        "failure_reason": "已受理,errno=0 不代表过审;请到百家号后台确认",
                    }
                else:
                    status, extra = "failed", {"failure_reason": note}
            else:
                status, extra = _map_result(result, _tail(proc.stderr))
        except subprocess.TimeoutExpired:
            status, extra = "failed", {"failure_reason": f"发布超时({settings.publish_timeout_s} 秒)"}

        conn = db.connect()
        try:
            repo.update_publication_status(conn, pub_id, status, **extra)
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001  # worker 线程兜底:宁可记失败也不能悬在 running
        try:
            conn = db.connect()
            try:
                repo.update_publication_status(conn, pub_id, "failed", failure_reason=f"worker 异常: {e}")
            finally:
                conn.close()
        except Exception:  # noqa: BLE001  # 连落库都失败就只能放弃了
            pass
    finally:
        release_channel_lock(channel_key)


def spawn_publication_worker(pub_id: str, channel_key: str, article: dict) -> None:
    threading.Thread(
        target=dispatch_publication, args=(pub_id, channel_key, article),
        daemon=True, name=f"pub-{channel_key}",
    ).start()


def _extract_result_json(stdout: str) -> dict | None:
    """从 CLI stdout 里找最后一个带 status 的 JSON 对象(结果信封)。

    信封是 json.dumps(indent=2) 打的多行块;从后往前找行首 '{',
    逐行累积直到 json.loads 成功。要求含 status 键,避免把
    百家号的脱敏 payload 预览当成结果。
    """
    lines = (stdout or "").splitlines()
    for start in range(len(lines) - 1, -1, -1):
        if not lines[start].lstrip().startswith("{"):
            continue
        buf: list[str] = []
        for end in range(start, len(lines)):
            buf.append(lines[end])
            try:
                obj = json.loads("\n".join(buf))
            except ValueError:
                continue
            if isinstance(obj, dict) and "status" in obj:
                return obj
            # 完整 JSON 但不是信封(比如预览),换个起点继续找
            break
    return None


def _map_result(result: dict, fallback_note: str) -> tuple[str, dict]:
    """CLI 结果信封 → publication 状态与回写字段。宁可不报成功,不伪造成功。"""
    status = result.get("status")
    if status == "published":
        return "published", {
            "published_url": result.get("final_url"),
            "published_at": _now(),
        }
    if status == "draft_saved":
        return "draft_saved", {"published_url": result.get("final_url")}
    if status == "blocked":
        return "blocked", {"failure_reason": result.get("reason") or fallback_note or "被平台拦截"}
    if status in ("rejected", "not_logged_in"):
        return "failed", {"failure_reason": result.get("reason") or fallback_note or status}
    if status == "unknown":
        return "unknown", {
            "published_url": result.get("final_url"),
            "failure_reason": result.get("note") or "结果需人工到渠道后台确认",
        }
    return "failed", {"failure_reason": fallback_note or f"未知状态 {status!r}"}
