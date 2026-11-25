"""系统更新服务层的口径测试（版本比较 / 清单拉取 / 下载校验 / 打包排除）。

file:// 当更新源：urllib 原生支持，测试不需要起 HTTP 服务，也不碰网络。
模块级路径（VERSION_FILE / SOURCE_FILE / UPDATES_DIR）在 setUp 里换成
临时目录、tearDown 还原——这些是「随机器走」的路径，测试必须隔离。
"""

import hashlib
import importlib.util
import json
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType

from geo.services import update_service
from geo.settings import BACKEND_DIR

RELEASE_MODULE_PATH = BACKEND_DIR / "scripts" / "release_package.py"


def _load_script(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VersionTest(unittest.TestCase):
    def test_parse_version_ignores_non_numeric_tail(self):
        self.assertEqual(update_service.parse_version("1.2.3"), (1, 2, 3))
        self.assertEqual(update_service.parse_version("0.1.0-beta"), (0, 1, 0))
        self.assertEqual(update_service.parse_version(""), (0, 0, 0))

    def test_is_newer_compares_numerically(self):
        self.assertTrue(update_service.is_newer("1.0.1", "1.0.0"))
        self.assertTrue(update_service.is_newer("1.1.0", "1.0.9"))
        self.assertFalse(update_service.is_newer("1.0.0", "1.0.0"))
        self.assertFalse(update_service.is_newer("0.9.9", "1.0.0"))

    def test_bump_patch_increments_last_segment(self):
        self.assertEqual(update_service.bump_patch("1.2.3"), "1.2.4")
        self.assertEqual(update_service.bump_patch("0.1.0"), "0.1.1")


class SourceConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self._source_file = update_service.SOURCE_FILE
        update_service.SOURCE_FILE = Path(self.tmp.name) / "update.local.json"

    def tearDown(self):
        update_service.SOURCE_FILE = self._source_file
        self.tmp.cleanup()

    def test_missing_source_file_reads_empty(self):
        self.assertEqual(update_service.read_source(), "")

    def test_source_round_trips(self):
        url = "https://gitee.com/zeng11234/geo-updates/raw/master/update.json"
        update_service.write_source(url)
        self.assertEqual(update_service.read_source(), url)

    def test_empty_url_is_rejected(self):
        with self.assertRaises(update_service.UpdateError):
            update_service.write_source("   ")

    def test_non_http_scheme_is_rejected(self):
        with self.assertRaises(update_service.UpdateError):
            update_service.write_source("ftp://example.com/update.json")


class _UpdateServiceTempPaths(unittest.TestCase):
    """把服务层的三个机器级路径换成临时目录的公共底座。"""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        base = Path(self.tmp.name)
        self._saved = (
            update_service.VERSION_FILE,
            update_service.SOURCE_FILE,
            update_service.UPDATES_DIR,
        )
        update_service.VERSION_FILE = base / "VERSION"
        update_service.SOURCE_FILE = base / "update.local.json"
        update_service.UPDATES_DIR = base / "updates"

    def tearDown(self):
        (
            update_service.VERSION_FILE,
            update_service.SOURCE_FILE,
            update_service.UPDATES_DIR,
        ) = self._saved
        self.tmp.cleanup()

    def set_local_version(self, version: str):
        update_service.VERSION_FILE.write_text(version + "\n", encoding="utf-8")

    def build_source_dir(self, version: str, *, notes: str = "测试说明") -> Path:
        """造一个「Gitee 更新仓库」的本地替身：update.json + app-<v>.zip。"""
        payload_dir = Path(self.tmp.name) / f"payload-{version}"
        (payload_dir / "backend").mkdir(parents=True)
        (payload_dir / "backend" / "run.py").write_text(f"# version {version}\n", encoding="utf-8")
        (payload_dir / "VERSION").write_text(version + "\n", encoding="utf-8")
        zip_path = Path(self.tmp.name) / f"app-{version}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in payload_dir.rglob("*"):
                if path.is_file():
                    archive.write(path, path.relative_to(payload_dir).as_posix())
        manifest = {
            "version": version,
            "publishedAt": "2026-11-24T12:00:00+08:00",
            "notes": notes,
            "minAppVersion": "0.1.0",
            "patch": {
                "url": zip_path.name,
                "size": zip_path.stat().st_size,
                "sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
            },
        }
        (Path(self.tmp.name) / "update.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )
        return Path(self.tmp.name)

    @property
    def source_url(self) -> str:
        return (Path(self.tmp.name) / "update.json").as_uri()


class ManifestTest(_UpdateServiceTempPaths):
    def test_fetch_manifest_reads_valid_shape(self):
        self.build_source_dir("1.2.4")
        manifest = update_service.fetch_manifest(self.source_url)
        self.assertEqual(manifest["version"], "1.2.4")
        self.assertEqual(manifest["patch"]["url"], "app-1.2.4.zip")

    def test_fetch_manifest_rejects_bad_json(self):
        Path(self.tmp.name, "update.json").write_text("not json", encoding="utf-8")
        with self.assertRaises(update_service.UpdateError):
            update_service.fetch_manifest(self.source_url)

    def test_fetch_manifest_rejects_missing_patch_fields(self):
        Path(self.tmp.name, "update.json").write_text(
            json.dumps({"version": "1.0.0", "patch": {"url": "x.zip"}}), encoding="utf-8"
        )
        with self.assertRaises(update_service.UpdateError):
            update_service.fetch_manifest(self.source_url)

    def test_fetch_manifest_reports_unreachable_source(self):
        with self.assertRaises(update_service.UpdateError):
            update_service.fetch_manifest(
                (Path(self.tmp.name) / "nope.json").as_uri()
            )


class CheckTest(_UpdateServiceTempPaths):
    def test_check_without_source_reports_it(self):
        self.set_local_version("1.0.0")
        result = update_service.check()
        self.assertFalse(result["has_source"])
        self.assertEqual(result["current"], "1.0.0")

    def test_check_finds_newer_version(self):
        self.set_local_version("1.0.0")
        self.build_source_dir("1.2.4", notes="修了引用栏")
        update_service.write_source(self.source_url)
        result = update_service.check()
        self.assertTrue(result["has_source"])
        self.assertTrue(result["has_update"])
        self.assertEqual(result["latest"], "1.2.4")
        self.assertEqual(result["notes"], "修了引用栏")

    def test_check_on_latest_reports_no_update(self):
        self.set_local_version("1.2.4")
        self.build_source_dir("1.2.4")
        update_service.write_source(self.source_url)
        result = update_service.check()
        self.assertFalse(result["has_update"])

    def test_check_returns_error_instead_of_raising(self):
        self.set_local_version("1.0.0")
        update_service.write_source((Path(self.tmp.name) / "nope.json").as_uri())
        result = update_service.check()
        self.assertIn("error", result)
        self.assertIn("访问失败", result["error"])


class DownloadAndStageTest(_UpdateServiceTempPaths):
    def test_download_verifies_and_stages(self):
        self.set_local_version("1.0.0")
        self.build_source_dir("1.2.4")
        update_service.write_source(self.source_url)

        info = update_service.download_and_stage()
        self.assertEqual(info["version"], "1.2.4")
        self.assertTrue((info["staging"] / "backend" / "run.py").exists())
        self.assertTrue(info["worker"].exists())
        # worker 跑的是 data/updates/ 下的副本，不是仓库原件
        self.assertNotEqual(info["worker"], update_service.WORKER_FILE)

    def test_sha_mismatch_is_rejected(self):
        self.set_local_version("1.0.0")
        self.build_source_dir("1.2.4")
        manifest = json.loads(Path(self.tmp.name, "update.json").read_text(encoding="utf-8"))
        manifest["patch"]["sha256"] = "0" * 64
        Path(self.tmp.name, "update.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )
        update_service.write_source(self.source_url)
        with self.assertRaises(update_service.UpdateError) as ctx:
            update_service.download_and_stage()
        self.assertIn("sha256", str(ctx.exception))

    def test_not_newer_is_rejected(self):
        self.set_local_version("2.0.0")
        self.build_source_dir("1.2.4")
        update_service.write_source(self.source_url)
        with self.assertRaises(update_service.UpdateError):
            update_service.download_and_stage()

    def test_missing_source_is_rejected(self):
        with self.assertRaises(update_service.UpdateError):
            update_service.download_and_stage()


class ReleasePackageTest(unittest.TestCase):
    """发布端打包：产物形状 + 数据绝不入包。"""

    @classmethod
    def setUpClass(cls):
        cls.release = _load_script(RELEASE_MODULE_PATH, "release_package_test")

    def setUp(self):
        self.tmp = TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "repo"
        # 代码（应入包）
        (self.root / "backend" / "src").mkdir(parents=True)
        (self.root / "backend" / "run.py").write_text("print('app')", encoding="utf-8")
        (self.root / "backend" / "src" / "app.py").write_text("# app", encoding="utf-8")
        (self.root / "frontend" / "static").mkdir(parents=True)
        (self.root / "frontend" / "static" / "index.html").write_text("<html>", encoding="utf-8")
        (self.root / "start-team.bat").write_text("@echo off\r\n", encoding="utf-8")
        # 数据与本地态（绝不能入包）
        (self.root / "backend" / "geo.db").write_bytes(b"SQLiteFormat 3 fake")
        (self.root / "backend" / "data").mkdir()
        (self.root / "backend" / "data" / "update.local.json").write_text("{}", encoding="utf-8")
        (self.root / "backend" / "data" / "channels.local.json").write_text("{}", encoding="utf-8")
        (self.root / "backend" / "src" / ".browser-profiles").mkdir()
        (self.root / "backend" / "src" / ".browser-profiles" / "sohu").write_text("cookie", encoding="utf-8")
        (self.root / ".git").mkdir()
        (self.root / ".git" / "config").write_text("[core]", encoding="utf-8")
        (self.root / "backend" / "__pycache__").mkdir()
        (self.root / "backend" / "__pycache__" / "run.cpython-312.pyc").write_bytes(b"x")
        self.out = base / "dist"

    def tearDown(self):
        self.tmp.cleanup()

    def test_build_package_shapes_and_excludes(self):
        result = self.release.build_package(self.root, "1.2.3", "发布说明", self.out)
        self.assertEqual((self.root / "VERSION").read_text(encoding="utf-8").strip(), "1.2.3")

        manifest = json.loads((self.out / "update.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "1.2.3")
        self.assertEqual(manifest["patch"]["url"], "app-1.2.3.zip")
        self.assertEqual(manifest["patch"]["size"], (self.out / "app-1.2.3.zip").stat().st_size)
        self.assertEqual(
            manifest["patch"]["sha256"],
            hashlib.sha256((self.out / "app-1.2.3.zip").read_bytes()).hexdigest(),
        )
        self.assertEqual(manifest["notes"], "发布说明")

        with zipfile.ZipFile(self.out / "app-1.2.3.zip") as archive:
            names = set(archive.namelist())
        # 代码在
        self.assertIn("backend/run.py", names)
        self.assertIn("backend/src/app.py", names)
        self.assertIn("frontend/static/index.html", names)
        self.assertIn("start-team.bat", names)
        self.assertIn("VERSION", names)
        # 数据、本地态、git、缓存不在
        self.assertNotIn("backend/geo.db", names)
        self.assertNotIn("backend/data/update.local.json", names)
        self.assertNotIn("backend/data/channels.local.json", names)
        self.assertNotIn(".git/config", names)
        self.assertNotIn("backend/__pycache__/run.cpython-312.pyc", names)
        self.assertTrue(
            all(".browser-profiles" not in name for name in names),
            "渠道登录态绝不能进发布包",
        )
        self.assertEqual(result["files"], len(names))

    def test_bump_patch(self):
        self.assertEqual(self.release._bump("1.2.3"), "1.2.4")
        self.assertEqual(self.release._bump("0.1.0"), "0.1.1")
