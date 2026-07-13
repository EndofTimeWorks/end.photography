import json
import os
import secrets
import sqlite3
import subprocess
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from flask import Flask, abort, redirect, render_template, request, session, url_for
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename


ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "tif", "tiff", "webp"}


def env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default)).expanduser().resolve()


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, static_url_path="/admin/static")
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", secrets.token_hex(32)),
        MAX_CONTENT_LENGTH=int(os.environ.get("MAX_UPLOAD_MB", "100")) * 1_000_000,
        MAX_FORM_MEMORY_SIZE=250_000,
        MAX_FORM_PARTS=20,
        ORIGINAL_DIR=env_path("ORIGINAL_DIR", "/srv/end-photography/originals"),
        PUBLIC_PHOTO_DIR=env_path("PUBLIC_PHOTO_DIR", "/var/www/end.photography/photos"),
        DATABASE=env_path("DATABASE", "/srv/end-photography/gallery.db"),
        MANIFEST=env_path("MANIFEST", "/var/www/end.photography/data/gallery.json"),
        MAGICK_BIN=os.environ.get("MAGICK_BIN", "magick"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "1") == "1",
    )

    if test_config:
        app.config.update(test_config)

    initialize_storage(app)
    register_routes(app)
    return app


def connect_database(app: Flask) -> sqlite3.Connection:
    connection = sqlite3.connect(app.config["DATABASE"])
    connection.row_factory = sqlite3.Row
    return connection


def initialize_storage(app: Flask) -> None:
    for path in (app.config["ORIGINAL_DIR"], app.config["PUBLIC_PHOTO_DIR"]):
        path.mkdir(parents=True, exist_ok=True)

    app.config["DATABASE"].parent.mkdir(parents=True, exist_ok=True)
    app.config["MANIFEST"].parent.mkdir(parents=True, exist_ok=True)

    with closing(connect_database(app)) as database:
        database.execute(
            """
            CREATE TABLE IF NOT EXISTS photos (
                id TEXT PRIMARY KEY,
                original_name TEXT NOT NULL,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                photo_date TEXT NOT NULL DEFAULT '',
                location TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '[]',
                original_path TEXT NOT NULL,
                public_filename TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            )
            """
        )
        database.commit()

    write_manifest(app)


def write_manifest(app: Flask) -> None:
    with closing(connect_database(app)) as database:
        rows = database.execute(
            "SELECT * FROM photos ORDER BY created_at DESC"
        ).fetchall()

    photos = [
        {
            "id": row["id"],
            "src": f"/photos/{row['public_filename']}",
            "alt": row["title"],
            "title": row["title"],
            "category": row["category"],
            "date": row["photo_date"],
            "location": row["location"],
            "description": row["description"],
            "tags": json.loads(row["tags"]),
        }
        for row in rows
    ]

    manifest = app.config["MANIFEST"]
    temporary = manifest.with_name(f".{manifest.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(photos, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, manifest)


def valid_csrf_token() -> bool:
    submitted = request.form.get("csrf_token", "")
    expected = session.get("csrf_token", "")
    return bool(submitted and expected and secrets.compare_digest(submitted, expected))


def allowed_extension(filename: str) -> str | None:
    if "." not in filename:
        return None

    extension = filename.rsplit(".", 1)[1].lower()
    return extension if extension in ALLOWED_EXTENSIONS else None


def process_photo(app: Flask, source: Path, destination: Path) -> None:
    command = [
        app.config["MAGICK_BIN"],
        "-limit",
        "memory",
        "512MiB",
        "-limit",
        "map",
        "1GiB",
        "-limit",
        "time",
        "120",
        str(source),
        "-auto-orient",
        "-strip",
        "-resize",
        "2400x2400>",
        "(",
        "-size",
        "600x300",
        "xc:none",
        "-gravity",
        "center",
        "-font",
        "DejaVu-Sans-Bold",
        "-pointsize",
        "34",
        "-fill",
        "rgba(255,255,255,0.24)",
        "-stroke",
        "rgba(0,0,0,0.18)",
        "-strokewidth",
        "1",
        "-annotate",
        "25x25",
        "EndofTimeWorks",
        "-write",
        "mpr:watermark",
        "+delete",
        ")",
        "(",
        "-size",
        "2400x2400",
        "tile:mpr:watermark",
        ")",
        "-compose",
        "over",
        "-composite",
        "-quality",
        "86",
        str(destination),
    ]
    subprocess.run(command, check=True, capture_output=True, timeout=120)


def register_routes(app: Flask) -> None:
    @app.get("/admin/healthz")
    def health() -> tuple[dict[str, str], int]:
        return {"status": "ok"}, 200

    @app.get("/admin/")
    def admin_index():
        session.setdefault("csrf_token", secrets.token_urlsafe(32))
        with closing(connect_database(app)) as database:
            photos = database.execute(
                "SELECT * FROM photos ORDER BY created_at DESC"
            ).fetchall()
        return render_template("index.html", photos=photos)

    @app.post("/admin/upload")
    def upload_photo():
        if not valid_csrf_token():
            abort(400, "Invalid form token. Refresh the page and try again.")

        uploaded = request.files.get("photo")
        if not uploaded or not uploaded.filename:
            abort(400, "Choose an image to upload.")

        extension = allowed_extension(uploaded.filename)
        if not extension:
            abort(400, "Supported files: JPG, PNG, TIFF, and WebP.")

        title = request.form.get("title", "").strip()[:160]
        if not title:
            abort(400, "Title is required.")

        photo_id = uuid.uuid4().hex
        safe_name = secure_filename(uploaded.filename) or f"upload.{extension}"
        original_path = app.config["ORIGINAL_DIR"] / f"{photo_id}.{extension}"
        public_filename = f"{photo_id}.webp"
        public_path = app.config["PUBLIC_PHOTO_DIR"] / public_filename
        temporary_public = public_path.with_name(f".{public_filename}.tmp.webp")

        uploaded.save(original_path)

        try:
            process_photo(app, original_path, temporary_public)
            os.replace(temporary_public, public_path)

            tags = [tag.strip() for tag in request.form.get("tags", "").split(",")]
            tags = [tag for tag in tags if tag]
            created_at = datetime.now(UTC).isoformat()

            with closing(connect_database(app)) as database:
                database.execute(
                    """
                    INSERT INTO photos (
                        id, original_name, title, category, photo_date, location,
                        description, tags, original_path, public_filename, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        photo_id,
                        safe_name,
                        title,
                        request.form.get("category", "uncategorized").strip().lower()[:80]
                        or "uncategorized",
                        request.form.get("date", "").strip()[:10],
                        request.form.get("location", "").strip()[:160],
                        request.form.get("description", "").strip()[:2000],
                        json.dumps(tags),
                        str(original_path),
                        public_filename,
                        created_at,
                    ),
                )
                database.commit()

            write_manifest(app)
        except (OSError, sqlite3.Error, subprocess.SubprocessError):
            original_path.unlink(missing_ok=True)
            temporary_public.unlink(missing_ok=True)
            public_path.unlink(missing_ok=True)
            raise

        return redirect(url_for("admin_index", uploaded="1"))

    @app.post("/admin/photos/<photo_id>/delete")
    def delete_photo(photo_id: str):
        if not valid_csrf_token():
            abort(400, "Invalid form token. Refresh the page and try again.")

        with closing(connect_database(app)) as database:
            photo = database.execute(
                "SELECT * FROM photos WHERE id = ?", (photo_id,)
            ).fetchone()
            if photo is None:
                abort(404)

            database.execute("DELETE FROM photos WHERE id = ?", (photo_id,))
            database.commit()

        Path(photo["original_path"]).unlink(missing_ok=True)
        (app.config["PUBLIC_PHOTO_DIR"] / photo["public_filename"]).unlink(
            missing_ok=True
        )
        write_manifest(app)
        return redirect(url_for("admin_index", deleted="1"))

    @app.errorhandler(RequestEntityTooLarge)
    def upload_too_large(_error):
        return "Upload exceeds the configured size limit.", 413
