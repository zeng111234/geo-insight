# -*- coding: utf-8 -*-
"""文章图片的 HTTP 全链路：上传 → 详情回显 → 取回 → 删除。

图片落盘目录在测试里换成临时目录，绝不碰 backend/data/article_images/。
鉴权/CSRF 沿用真实中间件（登录后从 cookie 取 token），不绕开。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from starlette.testclient import TestClient

from geo import seed
from geo.services import article_image_service as image_svc
from geo.settings import settings
from geo.api.app import app

FORM = {"Content-Type": "application/x-www-form-urlencoded"}
PNG = b"\x89PNG\r\n\x1a\n" + b"fake-image-bytes"


@unittest.skipUnless(settings.auth_required, "GEO_AUTH_REQUIRED=0,鉴权被显式关闭")
class ArticleImagesHttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed.seed()
        cls.client = TestClient(app)
        body = f"email={settings.admin_email}&password={settings.admin_password}"
        cls.client.post("/login", content=body, headers=FORM, follow_redirects=False)
        cls.client.headers.update({"X-CSRF-Token": cls.client.cookies.get("geo_csrf", "")})

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.p = mock.patch.object(image_svc, "IMAGE_DIR", Path(self.tmp.name))
        self.p.start()
        self.addCleanup(self.p.stop)

    def upload(self, article_id: str, data: bytes = PNG, filename: str = "cover.png"):
        return self.client.post(
            f"/v1/articles/{article_id}/images",
            files={"file": (filename, data, "image/png")},
        )

    def test_upload_detail_fetch_and_delete(self):
        article_id = seed.ART_DRAFT
        resp = self.upload(article_id)
        self.assertEqual(resp.status_code, 201, resp.text)
        name = resp.json()["name"]

        detail = self.client.get(f"/v1/articles/{article_id}").json()
        self.assertEqual(detail["images"], [name])

        fetched = self.client.get(f"/v1/articles/{article_id}/images/{name}")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.content, PNG)

        deleted = self.client.delete(f"/v1/articles/{article_id}/images/{name}")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json()["images"], [])
        self.assertFalse((Path(self.tmp.name) / name).exists())

    def test_rejects_non_image_and_traversal_name(self):
        article_id = seed.ART_DRAFT
        bad = self.upload(article_id, data=b"not an image", filename="evil.png")
        self.assertEqual(bad.status_code, 400)
        self.assertIn("图片", bad.json()["message"])

        wrong_ext = self.upload(article_id, data=PNG, filename="evil.exe")
        self.assertEqual(wrong_ext.status_code, 400)

        # 详情里没有的名字，取回/删除都应 404（防越权访问别人的图片）
        self.assertEqual(
            self.client.get(f"/v1/articles/{article_id}/images/../../secret").status_code, 404
        )
        self.assertEqual(
            self.client.delete(f"/v1/articles/{article_id}/images/not-there.png").status_code, 404
        )


if __name__ == "__main__":
    unittest.main()
