"""页内自更新（「表哥同款」）：检查更新源 → 下载校验 → 停服务换文件 → 重启。

为什么是 zip + JSON 清单而不是 git：交付对象是不装 git 的使用者，
拿到一个文件夹双击就能用；更新源是一个 URL（Gitee 上的 update.json），
页面上可配置——和 zeng11234/teacher-desk-updates 的机制一致。

数据与代码分离是整个方案的安全边界，两头都守：

- **打包端排除一切运行时数据**（geo.db、data/、backups/、import_reports/、
  浏览器登录态），zip 里只有代码；
- **应用端保留这些路径不动**——交换只发生在代码文件上，更新失败最多
  把代码留在 ``.update-old/`` 手工恢复，数据永不参与交换。

校验链与教师讲台同款：update.json 里的 ``size`` + ``sha256`` 与下载实体
核对，不匹配即弃。注意清单和实体同源（同一个 Gitee 仓库），这防的是
传输损坏与手滑，不是防投毒——更新源 URL 本身就是这台机器的信任锚。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from geo.settings import BACKEND_DIR, settings

REPO_ROOT = BACKEND_DIR.parent
VERSION_FILE = REPO_ROOT / "VERSION"
DATA_DIR = BACKEND_DIR / "data"
UPDATES_DIR = DATA_DIR / "updates"
SOURCE_FILE = DATA_DIR / "update.local.json"
WORKER_FILE = BACKEND_DIR / "scripts" / "update_worker.py"

# worker 杀掉服务前等的秒数：apply 接口要先把这个 202 响应发完，
# 否则页面收到的是连接中断而不是「正在应用」。
WORKER_GRACE_SECONDS = 2.5


class UpdateError(RuntimeError):
    """更新流程中一切可读的错误，消息直接面向页面展示。"""


# ---------------------------------------------------------------- 版本

def current_version() -> str:
    """当前代码版本。VERSION 文件由发布脚本写入 zip 与仓库。"""
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def parse_version(text: str) -> tuple[int, ...]:
    """「1.2.3-beta」→ (1, 2, 3)。非数字段丢弃；不足三位补零，空串算 0.0.0。"""
    parts: list[int] = []
    for chunk in (text or "").split("."):
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer(remote: str, local: str) -> bool:
    return parse_version(remote) > parse_version(local)


def bump_patch(text: str) -> str:
    major, minor, patch = (list(parse_version(text)) + [0, 0, 0])[:3]
    return f"{major}.{minor}.{patch + 1}"


# ---------------------------------------------------------------- 更新源配置

def read_source() -> str:
    """更新源 URL 存 data/（gitignore 的 *.local.json 覆盖），随机器走不随仓库走。"""
    try:
        data = json.loads(SOURCE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    url = data.get("source_url") or ""
    return url.strip() if isinstance(url, str) else ""


def write_source(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise UpdateError("更新源 URL 不能为空")
    if not url.startswith(("http://", "https://", "file://")):
        raise UpdateError("更新源必须是 http(s):// 或 file:// 开头的 URL")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SOURCE_FILE.write_text(
        json.dumps({"source_url": url}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return url


# ---------------------------------------------------------------- 检查与下载

def fetch_manifest(url: str) -> dict:
    """拉取并校验 update.json 的形状。字段口径与 teacher-desk-updates 一致。"""
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            raw = resp.read()
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise UpdateError(f"更新源访问失败：{exc}") from exc
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise UpdateError("更新源返回的不是合法 JSON") from exc

    patch = manifest.get("patch")
    if not isinstance(manifest.get("version"), str) or not isinstance(patch, dict):
        raise UpdateError("update.json 缺 version 或 patch 字段")
    for key in ("url", "sha256", "size"):
        if not patch.get(key):
            raise UpdateError(f"update.json 的 patch 缺 {key} 字段")
    return manifest


def check() -> dict:
    """页面「检查更新」的口径：错误不抛，进 ``error`` 字段给页面展示。"""
    payload: dict = {"current": current_version(), "source_url": read_source()}
    if not payload["source_url"]:
        payload["has_source"] = False
        return payload
    payload["has_source"] = True
    try:
        manifest = fetch_manifest(payload["source_url"])
    except UpdateError as exc:
        payload["error"] = str(exc)
        return payload
    payload["latest"] = manifest["version"]
    payload["has_update"] = is_newer(manifest["version"], payload["current"])
    payload["notes"] = manifest.get("notes", "")
    payload["published_at"] = manifest.get("publishedAt", "")
    payload["min_app_version"] = manifest.get("minAppVersion", "")
    return payload


def _resolve_patch_url(source_url: str, patch_url: str) -> str:
    if patch_url.startswith(("http://", "https://", "file://")):
        return patch_url
    # 相对名（app-1.0.0.zip）——相对 update.json 所在目录解析
    return source_url.rsplit("/", 1)[0] + "/" + patch_url


def _download(url: str, expected_size: int, expected_sha256: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=120) as resp:
            data = resp.read()
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise UpdateError(f"安装包下载失败：{exc}") from exc
    if expected_size and len(data) != expected_size:
        raise UpdateError(f"安装包大小不符（期望 {expected_size}，实得 {len(data)}）")
    digest = hashlib.sha256(data).hexdigest()
    if digest != expected_sha256:
        raise UpdateError("sha256 校验不过，安装包已丢弃")
    return data


def download_and_stage() -> dict:
    """下载新包并解压到暂存区。返回 worker 需要的全部路径。

    暂存区放在 ``data/updates/`` 下——应用阶段的交换永不触碰 data/，
    所以「下载了一半」「解压了一半」都不影响正在运行的代码。
    """
    source_url = read_source()
    if not source_url:
        raise UpdateError("尚未配置更新源")
    manifest = fetch_manifest(source_url)
    version = manifest["version"]
    if not is_newer(version, current_version()):
        raise UpdateError(f"远端版本 {version} 不比当前 {current_version()} 新，无需更新")

    patch = manifest["patch"]
    data = _download(
        _resolve_patch_url(source_url, patch["url"]),
        int(patch["size"] or 0),
        str(patch["sha256"]),
    )

    UPDATES_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = UPDATES_DIR / f"app-{version}.zip"
    zip_path.write_bytes(data)

    staging = UPDATES_DIR / f"staging-{version}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    shutil.unpack_archive(str(zip_path), str(staging))

    # 解出来的必须是「仓库根」的形状（顶层有 backend/），防止清单指了别的包
    if not (staging / "backend").is_dir():
        shutil.rmtree(staging, ignore_errors=True)
        zip_path.unlink(missing_ok=True)
        raise UpdateError("安装包内容不对：顶层缺少 backend/ 目录")

    # worker 从暂存副本跑，不跑仓库里的原件——交换过程会把仓库原件挪走
    worker_copy = UPDATES_DIR / f"worker-{version}.py"
    shutil.copyfile(WORKER_FILE, worker_copy)
    return {"version": version, "zip": zip_path, "staging": staging, "worker": worker_copy}


def apply_update() -> dict:
    """一键更新：下载 → 校验 → 解压 → 派生 worker（停服务换文件重启）。

    只把「正在应用」的回执发出去；真正的交换在 2.5 秒宽限期之后由
    worker 独立进程完成——服务进程亲手杀自己之前，响应必须已经落地。
    """
    info = download_and_stage()
    cmd = [
        sys.executable,
        str(info["worker"]),
        "--root",
        str(REPO_ROOT),
        "--staging",
        str(info["staging"]),
        "--pid",
        str(_server_pid()),
        "--wait",
        str(WORKER_GRACE_SECONDS),
    ]
    creationflags = 0
    if sys.platform == "win32":
        # 分离进程：服务死了 worker 也要活着把交换做完
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(cmd, creationflags=creationflags, close_fds=True)
    else:
        subprocess.Popen(cmd, start_new_session=True, close_fds=True)
    return {"status": "applying", "version": info["version"], "restart_seconds": WORKER_GRACE_SECONDS + 6}


def _server_pid() -> int:
    return os.getpid()
