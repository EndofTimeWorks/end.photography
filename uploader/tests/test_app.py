import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from app import create_app


class UploaderTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-secret",
                "SESSION_COOKIE_SECURE": False,
                "ORIGINAL_DIR": root / "originals",
                "PUBLIC_PHOTO_DIR": root / "public",
                "DATABASE": root / "gallery.db",
                "MANIFEST": root / "gallery.json",
            }
        )
        self.client = self.app.test_client()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def csrf_token(self):
        self.client.get("/admin/")
        with self.client.session_transaction() as session:
            return session["csrf_token"]

    def test_empty_gallery_creates_manifest(self):
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"No photos uploaded yet", response.data)
        self.assertIn(b'/admin/static/admin.css', response.data)
        self.assertEqual(
            json.loads(self.app.config["MANIFEST"].read_text(encoding="utf-8")),
            [],
        )

    def test_rejects_invalid_csrf_token(self):
        response = self.client.post(
            "/admin/upload",
            data={"title": "Test", "photo": (BytesIO(b"not-an-image"), "test.jpg")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)

    @patch("app.process_photo")
    def test_upload_writes_metadata_and_manifest(self, process_photo):
        def create_processed_file(_app, _source, destination):
            destination.write_bytes(b"processed")

        process_photo.side_effect = create_processed_file
        response = self.client.post(
            "/admin/upload",
            data={
                "csrf_token": self.csrf_token(),
                "title": "Desert Light",
                "category": "editorial",
                "date": "2026-07-12",
                "location": "Phoenix",
                "tags": "desert, evening",
                "description": "A test upload.",
                "photo": (BytesIO(b"original"), "desert.jpg"),
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 302)
        manifest = json.loads(
            self.app.config["MANIFEST"].read_text(encoding="utf-8")
        )
        self.assertEqual(len(manifest), 1)
        self.assertEqual(manifest[0]["title"], "Desert Light")
        self.assertEqual(manifest[0]["tags"], ["desert", "evening"])
        self.assertTrue(list(self.app.config["ORIGINAL_DIR"].iterdir()))
        self.assertTrue(list(self.app.config["PUBLIC_PHOTO_DIR"].iterdir()))

    @patch("app.process_photo")
    def test_delete_removes_original_public_copy_and_manifest_entry(self, process_photo):
        def create_processed_file(_app, _source, destination):
            destination.write_bytes(b"processed")

        process_photo.side_effect = create_processed_file
        self.client.post(
            "/admin/upload",
            data={
                "csrf_token": self.csrf_token(),
                "title": "Temporary",
                "photo": (BytesIO(b"original"), "temporary.jpg"),
            },
            content_type="multipart/form-data",
        )

        manifest = json.loads(
            self.app.config["MANIFEST"].read_text(encoding="utf-8")
        )
        photo_id = manifest[0]["id"]
        response = self.client.post(
            f"/admin/photos/{photo_id}/delete",
            data={"csrf_token": self.csrf_token()},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(list(self.app.config["ORIGINAL_DIR"].iterdir()))
        self.assertFalse(list(self.app.config["PUBLIC_PHOTO_DIR"].iterdir()))
        self.assertEqual(
            json.loads(self.app.config["MANIFEST"].read_text(encoding="utf-8")),
            [],
        )


if __name__ == "__main__":
    unittest.main()
