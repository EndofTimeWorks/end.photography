import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from app import create_app, csrf_token, derive_metadata

EMPTY_METADATA = {
    "title": "",
    "description": "",
    "tags": [],
    "date": "",
    "location": "",
    "creator": "",
    "copyright": "",
    "rating": "",
    "label": "",
    "source": "embedded",
}


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
                "STAGING_DIR": root / "staging",
                "PUBLIC_PHOTO_DIR": root / "public",
                "DATABASE": root / "gallery.db",
                "MANIFEST": root / "gallery.json",
            }
        )
        self.client = self.app.test_client()
        self.metadata_patcher = patch(
            "app.derive_metadata", return_value=EMPTY_METADATA
        )
        self.metadata_patcher.start()

    def tearDown(self):
        self.metadata_patcher.stop()
        self.temporary_directory.cleanup()

    def csrf_token(self):
        return csrf_token(self.app)

    def test_empty_gallery_creates_manifest(self):
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"No photos uploaded yet", response.data)
        self.assertIn(b"/admin/static/admin.css", response.data)
        self.assertIn(b".cr2,.cr3", response.data)
        self.assertEqual(
            json.loads(self.app.config["MANIFEST"].read_text(encoding="utf-8")),
            [],
        )

    @patch("app.process_preview")
    def test_review_uses_filename_when_title_metadata_is_missing(self, process_preview):
        process_preview.side_effect = lambda _app, _source, destination: (
            destination.write_bytes(b"preview")
        )
        response = self.client.post(
            "/admin/inspect",
            data={
                "csrf_token": self.csrf_token(),
                "photo": (BytesIO(b"photo"), "August_14-2026.jpg"),
            },
            content_type="multipart/form-data",
        )
        review = self.client.get(response.headers["Location"])
        self.assertIn(b'value="August 14 2026"', review.data)

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
        manifest = json.loads(self.app.config["MANIFEST"].read_text(encoding="utf-8"))
        self.assertEqual(len(manifest), 1)
        self.assertEqual(manifest[0]["title"], "Desert Light")
        self.assertEqual(manifest[0]["tags"], ["desert", "evening"])
        self.assertTrue(list(self.app.config["ORIGINAL_DIR"].iterdir()))
        self.assertTrue(list(self.app.config["PUBLIC_PHOTO_DIR"].iterdir()))

    @patch("app.process_photo")
    def test_delete_removes_original_public_copy_and_manifest_entry(
        self, process_photo
    ):
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

        manifest = json.loads(self.app.config["MANIFEST"].read_text(encoding="utf-8"))
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

    @patch("app.process_photo")
    @patch("app.extract_raw_preview")
    def test_raw_upload_uses_raw_route_and_filename_fallback(
        self, extract_raw_preview, process_photo
    ):
        extract_raw_preview.side_effect = lambda _app, source: source
        process_photo.side_effect = lambda _app, _source, destination: (
            destination.write_bytes(b"processed")
        )

        response = self.client.post(
            "/admin/upload/raw",
            data={
                "csrf_token": self.csrf_token(),
                "photo": (BytesIO(b"camera raw"), "desert-sunset.cr3"),
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 302)
        manifest = json.loads(self.app.config["MANIFEST"].read_text())
        self.assertEqual(manifest[0]["title"], "desert sunset")

    def test_standard_route_rejects_raw_file(self):
        response = self.client.post(
            "/admin/upload",
            data={
                "csrf_token": self.csrf_token(),
                "photo": (BytesIO(b"camera raw"), "photo.cr2"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)

    @patch("app.process_photo")
    @patch("app.extract_raw_preview")
    def test_raw_route_has_larger_request_limit(
        self, extract_raw_preview, process_photo
    ):
        self.app.config["MAX_CONTENT_LENGTH"] = 1_000
        self.app.config["RAW_MAX_CONTENT_LENGTH"] = 10_000
        payload = b"x" * 2_000
        standard = self.client.post(
            "/admin/upload",
            data={
                "csrf_token": self.csrf_token(),
                "photo": (BytesIO(payload), "photo.jpg"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(standard.status_code, 413)

        extract_raw_preview.side_effect = lambda _app, source: source
        process_photo.side_effect = lambda _app, _source, destination: (
            destination.write_bytes(b"processed")
        )
        raw = self.client.post(
            "/admin/upload/raw",
            data={
                "csrf_token": self.csrf_token(),
                "photo": (BytesIO(payload), "photo.cr3"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(raw.status_code, 302)

    @patch("app.process_photo")
    def test_xmp_sidecar_is_stored_and_deleted(self, process_photo):
        process_photo.side_effect = lambda _app, _source, destination: (
            destination.write_bytes(b"processed")
        )
        self.client.post(
            "/admin/upload",
            data={
                "csrf_token": self.csrf_token(),
                "photo": (BytesIO(b"original"), "photo.jpg"),
                "sidecar": (BytesIO(b"<x:xmpmeta/>"), "photo.xmp"),
            },
            content_type="multipart/form-data",
        )

        with closing(sqlite3.connect(self.app.config["DATABASE"])) as database:
            photo_id, sidecar_path = database.execute(
                "SELECT id, sidecar_path FROM photos"
            ).fetchone()
        self.assertTrue(Path(sidecar_path).exists())

        self.client.post(
            f"/admin/photos/{photo_id}/delete",
            data={"csrf_token": self.csrf_token()},
        )
        self.assertFalse(Path(sidecar_path).exists())

    @patch("app.process_photo")
    @patch("app.process_preview")
    def test_bulk_review_prefills_metadata_and_publishes(
        self, process_preview, process_photo
    ):
        self.metadata_patcher.stop()
        metadata = {
            **EMPTY_METADATA,
            "title": "Adobe title",
            "description": "Adobe caption",
            "tags": ["desert", "evening"],
            "date": "2026-07-12",
            "location": "Phoenix",
            "creator": "EndofTimeWorks",
            "source": "embedded+xmp-sidecar",
        }
        self.metadata_patcher = patch("app.derive_metadata", return_value=metadata)
        derive = self.metadata_patcher.start()
        process_preview.side_effect = lambda _app, _source, destination: (
            destination.write_bytes(b"preview")
        )
        process_photo.side_effect = lambda _app, _source, destination: (
            destination.write_bytes(b"processed")
        )

        response = self.client.post(
            "/admin/inspect",
            data={
                "csrf_token": self.csrf_token(),
                "photo": [
                    (BytesIO(b"first"), "first.jpg"),
                    (BytesIO(b"second"), "second.jpg"),
                ],
                "sidecar": (BytesIO(b"<x:xmpmeta/>"), "first.xmp"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 302)
        review = self.client.get(response.headers["Location"])
        self.assertIn(b"Metadata and previews", review.data)
        self.assertEqual(review.data.count(b'value="Adobe title"'), 2)
        self.assertIn(b"Adobe caption", review.data)
        self.assertIn(b"EndofTimeWorks", review.data)
        self.assertIsNotNone(derive.call_args_list[0].args[2])
        self.assertIsNone(derive.call_args_list[1].args[2])

        batch_files = list(self.app.config["STAGING_DIR"].glob("*.batch.json"))
        stage_ids = json.loads(batch_files[0].read_text())
        publish_data = {"csrf_token": self.csrf_token()}
        for stage_id in stage_ids:
            publish_data[f"title_{stage_id}"] = "Reviewed title"
        published = self.client.post(
            f"/admin/publish/{batch_files[0].name.removesuffix('.batch.json')}",
            data=publish_data,
        )

        self.assertEqual(published.status_code, 302)
        manifest = json.loads(self.app.config["MANIFEST"].read_text())
        self.assertEqual(len(manifest), 2)
        self.assertEqual({item["title"] for item in manifest}, {"Reviewed title"})
        self.assertFalse(list(self.app.config["STAGING_DIR"].iterdir()))
        gallery = self.client.get(published.headers["Location"])
        self.assertEqual(gallery.status_code, 200)
        self.assertIn(b"Reviewed title", gallery.data)
        self.assertIn(b"/admin/preview/photos/", gallery.data)


class MetadataTestCase(unittest.TestCase):
    @patch("app.read_exiftool_json")
    def test_sidecar_overrides_embedded_adobe_metadata(self, read_metadata):
        read_metadata.side_effect = [
            {
                "XMP-dc:Title": "Embedded title",
                "ExifIFD:DateTimeOriginal": "2026:07:12 18:20:00",
                "XMP-dc:Subject": ["desert", "evening"],
            },
            {
                "XMP-dc:Title": {"x-default": "Adobe title"},
                "XMP-dc:Description": "Adobe caption",
                "XMP-photoshop:City": "Phoenix",
                "XMP-dc:Creator": ["EndofTimeWorks"],
                "XMP-xmp:Rating": 5,
            },
        ]

        metadata = derive_metadata(None, Path("photo.cr3"), Path("photo.xmp"))

        self.assertEqual(metadata["title"], "Adobe title")
        self.assertEqual(metadata["description"], "Adobe caption")
        self.assertEqual(metadata["date"], "2026-07-12")
        self.assertEqual(metadata["location"], "Phoenix")
        self.assertEqual(metadata["tags"], ["desert", "evening"])
        self.assertEqual(metadata["creator"], "EndofTimeWorks")
        self.assertEqual(metadata["rating"], "5")
        self.assertEqual(metadata["source"], "embedded+xmp-sidecar")


class MigrationTestCase(unittest.TestCase):
    def test_existing_database_gets_additive_metadata_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database_path = root / "gallery.db"
            with closing(sqlite3.connect(database_path)) as database:
                database.execute(
                    """
                    CREATE TABLE photos (
                        id TEXT PRIMARY KEY, original_name TEXT NOT NULL,
                        title TEXT NOT NULL, category TEXT NOT NULL,
                        photo_date TEXT NOT NULL DEFAULT '',
                        location TEXT NOT NULL DEFAULT '',
                        description TEXT NOT NULL DEFAULT '',
                        tags TEXT NOT NULL DEFAULT '[]', original_path TEXT NOT NULL,
                        public_filename TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
                    )
                    """
                )
            create_app(
                {
                    "TESTING": True,
                    "ORIGINAL_DIR": root / "originals",
                    "STAGING_DIR": root / "staging",
                    "PUBLIC_PHOTO_DIR": root / "public",
                    "DATABASE": database_path,
                    "MANIFEST": root / "gallery.json",
                }
            )

            with closing(sqlite3.connect(database_path)) as database:
                columns = {
                    row[1] for row in database.execute("PRAGMA table_info(photos)")
                }
            self.assertIn("sidecar_path", columns)
            self.assertIn("metadata_json", columns)


if __name__ == "__main__":
    unittest.main()
