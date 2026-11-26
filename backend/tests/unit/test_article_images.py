# -*- coding: utf-8 -*-
"""文章图片存储的行为测试。

守三件事：
1. 文件名服务端生成 + 只认纯文件名 —— 目录穿越进不来；
2. 扩展名 + 文件头双校验，大小有上限 —— 上传口不能成任意文件落盘；
3. draft 构造把文件名转成发布器要的绝对路径，且跳过已丢失的文件。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from geo.services import article_image_service as svc

PNG = b"\x89PNG\r\n\x1a\n" + b"fake-image-bytes"
JPG = b"\xff\xd8\xff" + b"fake-image-bytes"


class ArticleImageServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir_patch = mock.patch.object(svc, "IMAGE_DIR", Path(self.tmp.name))
        self.dir_patch.start()
        self.addCleanup(self.dir_patch.stop)

    def test_save_generates_safe_name_and_keeps_extension(self):
        name = svc.save(PNG, "../../evil.png")
        self.assertNotIn("/", name)
        self.assertNotIn("..", name)
        self.assertTrue(name.endswith(".png"))
        self.assertTrue((Path(self.tmp.name) / name).is_file())

    def test_absolute_paths_only_existing_files(self):
        name = svc.save(PNG, "cover.png")
        paths = svc.absolute_paths([name, "missing.png", "../evil.png"])
        self.assertEqual(paths, [str(Path(self.tmp.name) / name)])

    def test_rejects_wrong_extension_and_fake_content(self):
        for data, filename, reason in [
            (PNG, "a.exe", "扩展名"),
            (b"not an image", "a.png", "文件内容"),
            (b"", "a.png", "空"),
            (b"x" * (svc.MAX_BYTES + 1), "a.png", "大小"),
        ]:
            with self.subTest(reason=reason):
                with self.assertRaises(svc.ImageError):
                    svc.save(data, filename)

    def test_path_for_rejects_traversal(self):
        for name in ("../secret", "/etc/passwd", "sub/a.png", ".", "..", ""):
            with self.subTest(name=name):
                with self.assertRaises(svc.ImageError):
                    svc.path_for(name)

    def test_delete_is_idempotent(self):
        name = svc.save(PNG, "a.png")
        svc.delete(name)
        svc.delete(name)  # 第二次不该抛
        self.assertFalse((Path(self.tmp.name) / name).exists())


class DraftImagesTest(unittest.TestCase):
    """验证 draft.json 真的带上图片绝对路径——发布器就靠这个字段。"""

    def test_draft_carries_absolute_image_paths(self):
        from geo.services import publish_service

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        image_dir = Path(tmp.name) / "images"
        image_dir.mkdir()
        png = image_dir / "cover.png"
        png.write_bytes(PNG)

        captured: dict = {}

        def fake_cli(script, *args, **kwargs):
            captured["draft"] = json.loads(Path(args[-1]).read_text(encoding="utf-8"))
            proc = mock.MagicMock()
            proc.stdout = json.dumps({"status": "published", "final_url": "http://x"})
            proc.stderr = ""
            proc.returncode = 0
            return proc

        drafts_dir = Path(tmp.name) / "drafts"
        patches = [
            mock.patch.object(svc, "IMAGE_DIR", image_dir),
            mock.patch.object(publish_service, "_run_cli", fake_cli),
            mock.patch.object(publish_service, "DRAFT_DIR", drafts_dir),
            mock.patch.object(publish_service, "repo"),
            mock.patch.object(publish_service.db, "connect", return_value=mock.MagicMock()),
            mock.patch.object(publish_service, "release_channel_lock"),
        ]
        for p in patches:
            p.start()
        try:
            article = {
                "title": "测试文章", "body": "正文一段。",
                "images": ["cover.png", "missing.png"],
            }
            publish_service.dispatch_publication("pub-x", "douyin", article)
            self.assertEqual(captured["draft"]["images"], [str(png)])

            # 无图文章：draft 不塞 images 键，让发布器按自己的规则拒绝
            captured.clear()
            publish_service.dispatch_publication(
                "pub-y", "douyin", {"title": "无图", "body": "正文", "images": []}
            )
            self.assertNotIn("images", captured["draft"])
        finally:
            for p in reversed(patches):
                p.stop()


if __name__ == "__main__":
    unittest.main()
