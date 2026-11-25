"""发布打包：把仓库打成 zip + update.json（「表哥同款」的发布端）。

用法：

    python scripts/release_package.py                          # 版本号自动 +1
    python scripts/release_package.py --version 1.2.0 --notes "上线提及识别"
    python scripts/release_package.py --out D:\\geo-updates     # 直接产出到更新仓库

产物（默认 ``dist/``）：

    app-<版本>.zip     整个代码树（排除一切运行时数据，见 EXCLUDE_DIRS）
    update.json        version / publishedAt / notes / patch{url, size, sha256}

发布三步（脚本会打印提醒）：

1. 把 ``dist/`` 里的两个文件复制到 Gitee 的更新仓库（如
   ``geo-updates``），提交并推送——**zip 和 json 必须同一个 commit**，
   不然客户端会拉到对不上的清单；
2. 把本仓库改过的 ``VERSION`` 随源码一起提交推送（发版动作有据可查）；
3. 通知同事在「系统更新」页点检查更新。

为什么打包端就排除数据：zip 会被原样解到使用者的仓库根上，任何带进去的
geo.db / 登录态 / 密码文件都会覆盖对方的真实数据。数据只属于它所在的
那台机器（应用端 update_worker 的保留清单是同一份约定的另一半）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND.parent

# 目录名命中即整目录排除。注意 .browser-profiles 不带路径前缀——
# 它可能出现在仓库任何层级（渠道登录态），漏一处就是把 cookie 发出去。
EXCLUDE_DIRS = {
    ".git",
    ".zcode",
    ".workbuddy",
    ".reasonix",
    ".claude",
    ".specify",
    ".browser-profiles",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    ".update-old",
    ".update-tmp",
}
# 相对仓库根要排除的特定路径（数据与本地配置，打包绝不携带）
EXCLUDE_PATHS = {
    "backend/data",
    "backend/backups",
    "backend/import_reports",
}
# 文件名/后缀命中即排除
EXCLUDE_FILE_SUFFIXES = (".db", ".db-journal", ".db-wal", ".db-shm", ".pyc", ".local.json")
EXCLUDE_FILE_NAMES = {"team.local.bat", "update.local.json"}

MIN_APP_VERSION = "0.1.0"


def _excluded(rel: str, name: str, is_dir: bool) -> bool:
    if is_dir:
        if name in EXCLUDE_DIRS:
            return True
    else:
        if name in EXCLUDE_FILE_NAMES:
            return True
        if name.endswith(EXCLUDE_FILE_SUFFIXES):
            return True
    parts = Path(rel).parts
    if rel in EXCLUDE_PATHS:
        return True
    for prefix in EXCLUDE_PATHS:
        if rel.startswith(prefix + "/") or rel.startswith(prefix + "\\"):
            return True
    return False


def build_zip(root: Path, zip_path: Path) -> int:
    """仓库树 → zip（顶层即仓库根，无包裹文件夹）。返回文件数。"""
    count = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            if path.is_dir():
                continue
            rel = path.relative_to(root).as_posix()
            if _excluded(rel, path.name, False):
                continue
            if any(part in EXCLUDE_DIRS for part in Path(rel).parts[:-1]):
                continue
            archive.write(path, rel)
            count += 1
    return count


def build_package(root: Path, version: str, notes: str, out_dir: Path) -> dict:
    """打包全流程：写 VERSION → 打 zip → 产 update.json。可独立测试。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    (root / "VERSION").write_text(version + "\n", encoding="utf-8")

    zip_path = out_dir / f"app-{version}.zip"
    if zip_path.exists():
        zip_path.unlink()
    count = build_zip(root, zip_path)

    sha256 = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    size = zip_path.stat().st_size
    manifest = {
        "version": version,
        "publishedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "notes": notes,
        "minAppVersion": MIN_APP_VERSION,
        "patch": {"url": zip_path.name, "size": size, "sha256": sha256},
    }
    (out_dir / "update.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {"version": version, "files": count, "zip": zip_path, "manifest": manifest}


def _bump(text: str) -> str:
    major, minor, patch = (list(map(int, text.strip().split("."))) + [0, 0, 0])[:3]
    return f"{major}.{minor}.{patch + 1}"


def publish(out_dir: Path, version: str, repo_path: Path) -> bool:
    """产物复制进更新仓库并 commit+push。zip 和 update.json 必须同一个 commit。

    push 失败不报错退出（网络/凭据问题）——文件和 commit 已就位，
    提示手工 push 即可；推送本身大概率要弹一次 Gitee 登录窗。
    """
    for name in (f"app-{version}.zip", "update.json"):
        shutil.copyfile(out_dir / name, repo_path / name)
    git = lambda *args: subprocess.run(  # noqa: E731 - 局部小工具
        ["git", "-C", str(repo_path), *args], capture_output=True, text=True, encoding="utf-8"
    )
    git("add", "-A")
    committed = git("commit", "-m", f"发布 v{version}")
    if committed.returncode != 0:
        print(f"[x] 更新仓库提交失败：{committed.stderr.strip()}")
        return False
    pushed = git("push")
    if pushed.returncode != 0:
        print("  更新仓库已提交但推送失败（第一次会弹 Gitee 登录窗；网络问题重试即可）：")
        print("  " + (pushed.stderr.strip() or pushed.stdout.strip())[:300])
        return False
    print(f"  已推送更新仓库：{repo_path}（v{version}）")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="打包发布 zip + update.json")
    parser.add_argument("--version", default=None, help="版本号，缺省从 VERSION 自动 +1")
    parser.add_argument("--notes", default="常规更新", help="更新说明（页面会展示）")
    parser.add_argument("--root", default=str(REPO_ROOT), help="要打包的仓库根")
    parser.add_argument("--out", default=str(REPO_ROOT / "dist"), help="产物目录")
    parser.add_argument(
        "--push-to",
        default=None,
        help="更新仓库本地路径（git clone），给了就复制产物并 commit+push",
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    out_dir = Path(args.out).resolve()
    version_file = root / "VERSION"
    current = version_file.read_text(encoding="utf-8").strip() if version_file.exists() else "0.0.0"
    version = args.version or _bump(current)

    result = build_package(root, version, args.notes, out_dir)
    print(f"版本 {version}：{result['files']} 个文件，{result['zip'].stat().st_size / 1024:.0f} KB")
    print(f"产物目录：{out_dir}")

    if args.push_to:
        publish(out_dir, version, Path(args.push_to).resolve())
        print()
        print("剩余步骤：")
        print("  1. 本仓库的 VERSION 已改为 " + version + "，随源码一起提交推送。")
        print("  2. 通知同事：系统更新页面 → 检查更新。")
        return 0

    print()
    print("下一步：")
    print(f"  1. 把 {out_dir}\\app-{version}.zip 和 update.json 一起复制到 Gitee 更新仓库，")
    print("     同一个 commit 提交并推送（zip 和 json 分开发会出现清单对不上）。")
    print("  2. 本仓库的 VERSION 已改为 " + version + "，随源码一起提交推送。")
    print("  3. 通知同事：系统更新页面 → 检查更新。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
