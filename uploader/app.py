import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import subprocess
import tempfile
import time
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from flask import Flask, abort, redirect, render_template, request, send_file, url_for
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

STANDARD_EXTENSIONS = {"jpg", "jpeg", "png", "tif", "tiff", "webp"}
RAW_EXTENSIONS = {"raw", "dng", "cr2", "cr3", "nef", "arw", "orf", "rw2", "raf"}
ALLOWED_EXTENSIONS = STANDARD_EXTENSIONS | RAW_EXTENSIONS
METADATA_TAGS = (
    "-XMP-dc:Title",
    "-IPTC:ObjectName",
    "-XMP-photoshop:Headline",
    "-IPTC:Headline",
    "-XMP-dc:Description",
    "-IPTC:Caption-Abstract",
    "-XMP-dc:Subject",
    "-XMP-lr:HierarchicalSubject",
    "-IPTC:Keywords",
    "-EXIF:DateTimeOriginal",
    "-XMP-exif:DateTimeOriginal",
    "-XMP-xmp:CreateDate",
    "-XMP-iptcCore:Location",
    "-XMP-photoshop:City",
    "-XMP-photoshop:State",
    "-XMP-photoshop:Country",
    "-IPTC:Sub-location",
    "-IPTC:City",
    "-IPTC:Province-State",
    "-IPTC:Country-PrimaryLocationName",
    "-XMP-dc:Creator",
    "-IPTC:By-line",
    "-EXIF:Artist",
    "-XMP-dc:Rights",
    "-IPTC:CopyrightNotice",
    "-EXIF:Copyright",
    "-XMP-xmp:Rating",
    "-XMP-xmp:Label",
)
SCHEMA_COLUMNS = {
    "sidecar_path": "TEXT NOT NULL DEFAULT ''",
    "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
}


class PhotoProcessingError(Exception):
    pass


def env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default)).expanduser().resolve()


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, static_url_path="/admin/static")
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", secrets.token_hex(32)),
        MAX_CONTENT_LENGTH=int(os.environ.get("MAX_UPLOAD_MB", "100")) * 1_000_000,
        RAW_MAX_CONTENT_LENGTH=int(os.environ.get("RAW_MAX_UPLOAD_MB", "1024"))
        * 1_000_000,
        STANDARD_BATCH_MAX_CONTENT_LENGTH=int(
            os.environ.get("STANDARD_BATCH_MAX_UPLOAD_MB", "1000")
        )
        * 1_000_000,
        RAW_BATCH_MAX_CONTENT_LENGTH=int(
            os.environ.get("RAW_BATCH_MAX_UPLOAD_MB", "4096")
        )
        * 1_000_000,
        MAX_FORM_MEMORY_SIZE=250_000,
        MAX_FORM_PARTS=50,
        MAX_BATCH_FILES=20,
        ORIGINAL_DIR=env_path("ORIGINAL_DIR", "/srv/end-photography/originals"),
        STAGING_DIR=env_path("STAGING_DIR", "/srv/end-photography/staging"),
        PUBLIC_PHOTO_DIR=env_path(
            "PUBLIC_PHOTO_DIR", "/var/www/end.photography/photos"
        ),
        DATABASE=env_path("DATABASE", "/srv/end-photography/gallery.db"),
        MANIFEST=env_path("MANIFEST", "/var/www/end.photography/data/gallery.json"),
        MAGICK_BIN=os.environ.get("MAGICK_BIN", "magick"),
        EXIFTOOL_BIN=os.environ.get("EXIFTOOL_BIN", "exiftool"),
        RAW_UPLOAD_ORIGIN=os.environ.get("RAW_UPLOAD_ORIGIN", "").rstrip("/"),
    )

    if test_config:
        app.config.update(test_config)

    app.add_template_filter(json.loads, "from_json")

    initialize_storage(app)
    register_routes(app)
    return app


def connect_database(app: Flask) -> sqlite3.Connection:
    connection = sqlite3.connect(app.config["DATABASE"])
    connection.row_factory = sqlite3.Row
    return connection


def initialize_storage(app: Flask) -> None:
    for path in (
        app.config["ORIGINAL_DIR"],
        app.config["STAGING_DIR"],
        app.config["PUBLIC_PHOTO_DIR"],
    ):
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
                created_at TEXT NOT NULL,
                sidecar_path TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        existing_columns = {
            row["name"] for row in database.execute("PRAGMA table_info(photos)")
        }
        for name, definition in SCHEMA_COLUMNS.items():
            if name not in existing_columns:
                database.execute(f"ALTER TABLE photos ADD COLUMN {name} {definition}")
        database.commit()

    write_manifest(app)


def write_manifest(app: Flask) -> None:
    with closing(connect_database(app)) as database:
        rows = database.execute(
            "SELECT * FROM photos ORDER BY created_at DESC"
        ).fetchall()

    photos = [serialize_photo(row) for row in rows]

    manifest = app.config["MANIFEST"]
    temporary = manifest.with_name(f".{manifest.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(photos, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, manifest)


def serialize_photo(row: sqlite3.Row) -> dict[str, Any]:
    return {
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


def csrf_token(app: Flask) -> str:
    key = str(app.config["SECRET_KEY"]).encode()
    return hmac.new(key, b"end-photography-admin-form-v1", hashlib.sha256).hexdigest()


def valid_csrf_token(app: Flask) -> bool:
    submitted = request.form.get("csrf_token", "")
    return bool(submitted and secrets.compare_digest(submitted, csrf_token(app)))


def file_extension(filename: str) -> str | None:
    if "." not in filename:
        return None
    return filename.rsplit(".", 1)[1].lower()


def allowed_extension(filename: str) -> str | None:
    extension = file_extension(filename)
    return extension if extension in ALLOWED_EXTENSIONS else None


def clean_scalar(value: Any) -> str:
    if isinstance(value, list):
        return clean_scalar(value[0]) if value else ""
    if isinstance(value, dict):
        for key in ("x-default", "default", "en", "en-US"):
            if key in value:
                return clean_scalar(value[key])
        return clean_scalar(next(iter(value.values()))) if value else ""
    return str(value).strip() if value is not None else ""


def metadata_value(metadata: dict[str, Any], *names: str) -> str:
    for name in names:
        if name in metadata:
            value = clean_scalar(metadata[name])
            if value:
                return value
    return ""


def metadata_list(metadata: dict[str, Any], *names: str) -> list[str]:
    values: list[str] = []
    for name in names:
        value = metadata.get(name)
        if value is None:
            continue
        items = value if isinstance(value, list) else [value]
        for item in items:
            cleaned = clean_scalar(item)
            if cleaned and cleaned not in values:
                values.append(cleaned)
    return values


def read_exiftool_json(app: Flask, source: Path) -> dict[str, Any]:
    command = [
        app.config["EXIFTOOL_BIN"],
        "-json",
        "-G1",
        "-struct",
        *METADATA_TAGS,
        str(source),
    ]
    result = subprocess.run(command, check=True, capture_output=True, timeout=60)
    records = json.loads(result.stdout)
    return records[0] if records else {}


def derive_metadata(app: Flask, original: Path, sidecar: Path | None) -> dict[str, Any]:
    embedded = read_exiftool_json(app, original)
    combined = dict(embedded)
    if sidecar:
        combined.update(read_exiftool_json(app, sidecar))

    raw_location_parts = [
        metadata_value(combined, "XMP-iptcCore:Location", "IPTC:Sub-location"),
        metadata_value(combined, "XMP-photoshop:City", "IPTC:City"),
        metadata_value(combined, "XMP-photoshop:State", "IPTC:Province-State"),
        metadata_value(
            combined,
            "XMP-photoshop:Country",
            "IPTC:Country-PrimaryLocationName",
        ),
    ]
    location_parts = list(dict.fromkeys(part for part in raw_location_parts if part))
    date = metadata_value(
        combined,
        "ExifIFD:DateTimeOriginal",
        "EXIF:DateTimeOriginal",
        "XMP-exif:DateTimeOriginal",
        "XMP-xmp:CreateDate",
    )
    if len(date) >= 10:
        date = date[:10].replace(":", "-")

    return {
        "title": metadata_value(
            combined,
            "XMP-dc:Title",
            "IPTC:ObjectName",
            "XMP-photoshop:Headline",
            "IPTC:Headline",
        ),
        "description": metadata_value(
            combined, "XMP-dc:Description", "IPTC:Caption-Abstract"
        ),
        "tags": metadata_list(
            combined,
            "XMP-dc:Subject",
            "XMP-lr:HierarchicalSubject",
            "IPTC:Keywords",
        ),
        "date": date,
        "location": ", ".join(location_parts),
        "creator": metadata_value(
            combined, "XMP-dc:Creator", "IPTC:By-line", "IFD0:Artist"
        ),
        "copyright": metadata_value(
            combined,
            "XMP-dc:Rights",
            "IPTC:CopyrightNotice",
            "IFD0:Copyright",
        ),
        "rating": metadata_value(combined, "XMP-xmp:Rating"),
        "label": metadata_value(combined, "XMP-xmp:Label"),
        "source": "embedded+xmp-sidecar" if sidecar else "embedded",
    }


def extract_raw_preview(app: Flask, source: Path) -> Path:
    for tag in ("JpgFromRaw", "PreviewImage", "OtherImage"):
        preview: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=app.config["ORIGINAL_DIR"], suffix=".jpg", delete=False
            ) as temporary:
                preview = Path(temporary.name)
                subprocess.run(
                    [app.config["EXIFTOOL_BIN"], "-b", f"-{tag}", str(source)],
                    check=True,
                    stdout=temporary,
                    stderr=subprocess.PIPE,
                    timeout=120,
                )
            if preview.stat().st_size > 0:
                return preview
        except (OSError, subprocess.SubprocessError):
            pass
        if preview:
            preview.unlink(missing_ok=True)

    raise PhotoProcessingError(
        "This RAW file has no full-size embedded preview. Export it from Adobe as "
        "JPEG or TIFF to preserve the edited appearance."
    )


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


def process_preview(app: Flask, source: Path, destination: Path) -> None:
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
        "1200x1200>",
        "-quality",
        "82",
        str(destination),
    ]
    subprocess.run(command, check=True, capture_output=True, timeout=120)


def stage_path(app: Flask, stage_id: str, suffix: str) -> Path:
    return app.config["STAGING_DIR"] / f"{stage_id}{suffix}"


def valid_identifier(identifier: str) -> bool:
    try:
        return uuid.UUID(identifier).hex == identifier
    except ValueError:
        return False


def cleanup_staging(app: Flask) -> None:
    cutoff = time.time() - 24 * 60 * 60
    for path in app.config["STAGING_DIR"].iterdir():
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
        except FileNotFoundError:
            pass


def save_stage_record(app: Flask, record: dict[str, Any]) -> None:
    path = stage_path(app, record["id"], ".json")
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def load_stage_record(app: Flask, stage_id: str) -> dict[str, Any]:
    if not valid_identifier(stage_id):
        abort(404)
    path = stage_path(app, stage_id, ".json")
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        abort(404)
    if not record["metadata"].get("title"):
        record["metadata"]["title"] = (
            Path(record["original_name"]).stem.replace("_", " ").replace("-", " ")
        )
    return record


def save_batch(app: Flask, batch_id: str, stage_ids: list[str]) -> None:
    path = stage_path(app, batch_id, ".batch.json")
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(stage_ids) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def load_batch(app: Flask, batch_id: str) -> list[str]:
    if not valid_identifier(batch_id):
        abort(404)
    try:
        stage_ids = json.loads(
            stage_path(app, batch_id, ".batch.json").read_text(encoding="utf-8")
        )
    except (FileNotFoundError, json.JSONDecodeError):
        abort(404)
    if not isinstance(stage_ids, list) or not all(
        isinstance(stage_id, str) and valid_identifier(stage_id)
        for stage_id in stage_ids
    ):
        abort(404)
    return stage_ids


def cleanup_stage(app: Flask, record: dict[str, Any]) -> None:
    for key in ("original_path", "sidecar_path", "source_path", "preview_path"):
        value = record.get(key)
        if value:
            Path(value).unlink(missing_ok=True)
    stage_path(app, record["id"], ".json").unlink(missing_ok=True)


def stage_upload(
    app: Flask, uploaded, sidecar_upload, expected_extensions: set[str]
) -> dict[str, Any]:
    extension = allowed_extension(uploaded.filename)
    if not extension or extension not in expected_extensions:
        raise PhotoProcessingError(
            f"{secure_filename(uploaded.filename)} does not match this upload type."
        )

    stage_id = uuid.uuid4().hex
    safe_name = secure_filename(uploaded.filename) or f"upload.{extension}"
    original_path = stage_path(app, stage_id, f".{extension}")
    sidecar_path = stage_path(app, stage_id, ".xmp") if sidecar_upload else None
    preview_path = stage_path(app, stage_id, ".preview.webp")
    source_path = original_path

    uploaded.save(original_path)
    per_file_limit = (
        app.config["RAW_MAX_CONTENT_LENGTH"]
        if extension in RAW_EXTENSIONS
        else app.config["MAX_CONTENT_LENGTH"]
    )
    if original_path.stat().st_size > per_file_limit:
        original_path.unlink(missing_ok=True)
        raise PhotoProcessingError(f"{safe_name} exceeds its per-file size limit.")

    try:
        if sidecar_upload and sidecar_path:
            sidecar_upload.save(sidecar_path)
        metadata = derive_metadata(app, original_path, sidecar_path)
        if extension in RAW_EXTENSIONS:
            extracted = extract_raw_preview(app, original_path)
            source_path = stage_path(app, stage_id, ".source.jpg")
            os.replace(extracted, source_path)
        process_preview(app, source_path, preview_path)
        record = {
            "id": stage_id,
            "original_name": safe_name,
            "extension": extension,
            "original_path": str(original_path),
            "sidecar_path": str(sidecar_path) if sidecar_path else "",
            "source_path": str(source_path),
            "preview_path": str(preview_path),
            "metadata": metadata,
            "created_at": datetime.now(UTC).isoformat(),
        }
        save_stage_record(app, record)
        return record
    except Exception:
        cleanup_stage(
            app,
            {
                "id": stage_id,
                "original_path": str(original_path),
                "sidecar_path": str(sidecar_path) if sidecar_path else "",
                "source_path": str(source_path),
                "preview_path": str(preview_path),
            },
        )
        raise


def inspect_uploads(app: Flask, expected_extensions: set[str]):
    if not valid_csrf_token(app):
        abort(400, "Invalid form token. Refresh the page and try again.")

    uploads = [item for item in request.files.getlist("photo") if item.filename]
    if not uploads:
        abort(400, "Choose at least one image to upload.")
    if len(uploads) > app.config["MAX_BATCH_FILES"]:
        abort(400, f"Choose at most {app.config['MAX_BATCH_FILES']} photos at once.")

    sidecars = [item for item in request.files.getlist("sidecar") if item.filename]
    if any(file_extension(item.filename) != "xmp" for item in sidecars):
        abort(400, "Metadata sidecars must be XMP files.")
    sidecars_by_stem = {
        Path(secure_filename(item.filename)).stem.lower(): item for item in sidecars
    }

    records = []
    try:
        for uploaded in uploads:
            safe_name = secure_filename(uploaded.filename)
            sidecar = sidecars_by_stem.get(Path(safe_name).stem.lower())
            if len(uploads) == 1 and len(sidecars) == 1:
                sidecar = sidecars[0]
            records.append(stage_upload(app, uploaded, sidecar, expected_extensions))
    except Exception:
        for record in records:
            cleanup_stage(app, record)
        raise

    batch_id = uuid.uuid4().hex
    save_batch(app, batch_id, [record["id"] for record in records])
    return redirect(url_for("review_batch", batch_id=batch_id))


def batch_form_value(name: str, stage_id: str, derived: str, limit: int) -> str:
    return (request.form.get(f"{name}_{stage_id}", "").strip() or derived)[:limit]


def publish_stage(app: Flask, record: dict[str, Any]) -> None:
    stage_id = record["id"]
    extension = record["extension"]
    metadata = record["metadata"]
    staged_original = Path(record["original_path"])
    staged_sidecar = Path(record["sidecar_path"]) if record["sidecar_path"] else None
    source = Path(record["source_path"])
    original_path = app.config["ORIGINAL_DIR"] / f"{stage_id}.{extension}"
    sidecar_path = app.config["ORIGINAL_DIR"] / f"{stage_id}.xmp"
    public_filename = f"{stage_id}.webp"
    public_path = app.config["PUBLIC_PHOTO_DIR"] / public_filename
    temporary_public = public_path.with_name(f".{public_filename}.tmp.webp")
    moved_original = False
    moved_sidecar = False
    database_inserted = False

    typed_tags = [
        tag.strip()
        for tag in request.form.get(f"tags_{stage_id}", "").split(",")
        if tag.strip()
    ]
    title = batch_form_value("title", stage_id, metadata["title"], 160)
    if not title:
        title = Path(record["original_name"]).stem.replace("_", " ").replace("-", " ")

    try:
        process_photo(app, source, temporary_public)
        os.replace(staged_original, original_path)
        moved_original = True
        if staged_sidecar:
            os.replace(staged_sidecar, sidecar_path)
            moved_sidecar = True
        os.replace(temporary_public, public_path)

        with closing(connect_database(app)) as database:
            database.execute(
                """
                INSERT INTO photos (
                    id, original_name, title, category, photo_date, location,
                    description, tags, original_path, public_filename, created_at,
                    sidecar_path, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stage_id,
                    record["original_name"],
                    title[:160],
                    request.form.get(f"category_{stage_id}", "").strip().lower()[:80]
                    or "uncategorized",
                    batch_form_value("date", stage_id, metadata["date"], 10),
                    batch_form_value("location", stage_id, metadata["location"], 160),
                    batch_form_value(
                        "description", stage_id, metadata["description"], 2000
                    ),
                    json.dumps(typed_tags or metadata["tags"]),
                    str(original_path),
                    public_filename,
                    datetime.now(UTC).isoformat(),
                    str(sidecar_path) if staged_sidecar else "",
                    json.dumps(metadata),
                ),
            )
            database.commit()
            database_inserted = True
        write_manifest(app)
    except Exception:
        temporary_public.unlink(missing_ok=True)
        public_path.unlink(missing_ok=True)
        if database_inserted:
            with closing(connect_database(app)) as database:
                database.execute("DELETE FROM photos WHERE id = ?", (stage_id,))
                database.commit()
        if moved_original:
            os.replace(original_path, staged_original)
        if moved_sidecar and staged_sidecar:
            os.replace(sidecar_path, staged_sidecar)
        raise

    cleanup_stage(app, record)


def form_value(name: str, derived: str, limit: int) -> str:
    return (request.form.get(name, "").strip() or derived)[:limit]


def handle_upload(app: Flask, expected_extensions: set[str]):
    if not valid_csrf_token(app):
        abort(400, "Invalid form token. Refresh the page and try again.")

    uploaded = request.files.get("photo")
    if not uploaded or not uploaded.filename:
        abort(400, "Choose an image to upload.")

    extension = allowed_extension(uploaded.filename)
    if not extension or extension not in expected_extensions:
        abort(400, "The selected file does not match this upload type.")

    sidecar_upload = request.files.get("sidecar")
    if sidecar_upload and sidecar_upload.filename:
        if file_extension(sidecar_upload.filename) != "xmp":
            abort(400, "The metadata sidecar must be an XMP file.")
    else:
        sidecar_upload = None

    photo_id = uuid.uuid4().hex
    safe_name = secure_filename(uploaded.filename) or f"upload.{extension}"
    original_path = app.config["ORIGINAL_DIR"] / f"{photo_id}.{extension}"
    sidecar_path = (
        app.config["ORIGINAL_DIR"] / f"{photo_id}.xmp" if sidecar_upload else None
    )
    public_filename = f"{photo_id}.webp"
    public_path = app.config["PUBLIC_PHOTO_DIR"] / public_filename
    temporary_public = public_path.with_name(f".{public_filename}.tmp.webp")
    preview_path: Path | None = None
    database_inserted = False

    uploaded.save(original_path)
    if sidecar_upload and sidecar_path:
        sidecar_upload.save(sidecar_path)

    try:
        metadata = derive_metadata(app, original_path, sidecar_path)
        source = original_path
        if extension in RAW_EXTENSIONS:
            preview_path = extract_raw_preview(app, original_path)
            source = preview_path
        process_photo(app, source, temporary_public)
        os.replace(temporary_public, public_path)

        typed_tags = [tag.strip() for tag in request.form.get("tags", "").split(",")]
        typed_tags = [tag for tag in typed_tags if tag]
        tags = typed_tags or metadata["tags"]
        title = form_value("title", metadata["title"], 160)
        if not title:
            title = Path(safe_name).stem.replace("_", " ").replace("-", " ")[:160]

        with closing(connect_database(app)) as database:
            database.execute(
                """
                INSERT INTO photos (
                    id, original_name, title, category, photo_date, location,
                    description, tags, original_path, public_filename, created_at,
                    sidecar_path, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    photo_id,
                    safe_name,
                    title,
                    request.form.get("category", "").strip().lower()[:80]
                    or "uncategorized",
                    form_value("date", metadata["date"], 10),
                    form_value("location", metadata["location"], 160),
                    form_value("description", metadata["description"], 2000),
                    json.dumps(tags),
                    str(original_path),
                    public_filename,
                    datetime.now(UTC).isoformat(),
                    str(sidecar_path) if sidecar_path else "",
                    json.dumps(metadata),
                ),
            )
            database.commit()
            database_inserted = True

        write_manifest(app)
    except (
        json.JSONDecodeError,
        OSError,
        sqlite3.Error,
        subprocess.SubprocessError,
        PhotoProcessingError,
    ):
        original_path.unlink(missing_ok=True)
        if sidecar_path:
            sidecar_path.unlink(missing_ok=True)
        temporary_public.unlink(missing_ok=True)
        public_path.unlink(missing_ok=True)
        if database_inserted:
            with closing(connect_database(app)) as database:
                database.execute("DELETE FROM photos WHERE id = ?", (photo_id,))
                database.commit()
        raise
    finally:
        if preview_path:
            preview_path.unlink(missing_ok=True)

    return redirect(url_for("admin_index", uploaded="1"))


def register_routes(app: Flask) -> None:
    @app.get("/admin/healthz")
    def health() -> tuple[dict[str, str], int]:
        return {"status": "ok"}, 200

    @app.get("/admin/")
    def admin_index():
        cleanup_staging(app)
        with closing(connect_database(app)) as database:
            photos = database.execute(
                "SELECT * FROM photos ORDER BY created_at DESC"
            ).fetchall()
        raw_upload_url = f"{app.config['RAW_UPLOAD_ORIGIN']}/admin/inspect/raw"
        if not app.config["RAW_UPLOAD_ORIGIN"]:
            raw_upload_url = url_for("inspect_raw_photos")
        return render_template(
            "index.html",
            photos=photos,
            csrf_token=csrf_token(app),
            raw_upload_url=raw_upload_url,
            batch_id=None,
            stages=[],
        )

    @app.get("/admin/preview/")
    def public_preview():
        with closing(connect_database(app)) as database:
            rows = database.execute(
                "SELECT * FROM photos ORDER BY created_at DESC"
            ).fetchall()
        photos = []
        for row in rows:
            photo = serialize_photo(row)
            photo["src"] = url_for("preview_photo", photo_id=row["id"])
            photos.append(photo)
        return render_template("gallery.html", photos=photos)

    @app.get("/admin/preview/photos/<photo_id>.webp")
    def preview_photo(photo_id: str):
        if not valid_identifier(photo_id):
            abort(404)
        with closing(connect_database(app)) as database:
            photo = database.execute(
                "SELECT public_filename FROM photos WHERE id = ?", (photo_id,)
            ).fetchone()
        if photo is None:
            abort(404)
        return send_file(
            app.config["PUBLIC_PHOTO_DIR"] / photo["public_filename"],
            mimetype="image/webp",
        )

    @app.post("/admin/inspect")
    def inspect_photos():
        request.max_content_length = app.config["STANDARD_BATCH_MAX_CONTENT_LENGTH"]
        return inspect_uploads(app, STANDARD_EXTENSIONS)

    @app.post("/admin/inspect/raw")
    def inspect_raw_photos():
        request.max_content_length = app.config["RAW_BATCH_MAX_CONTENT_LENGTH"]
        return inspect_uploads(app, ALLOWED_EXTENSIONS)

    @app.get("/admin/review/<batch_id>")
    def review_batch(batch_id: str):
        stage_ids = load_batch(app, batch_id)
        stages = [load_stage_record(app, stage_id) for stage_id in stage_ids]
        with closing(connect_database(app)) as database:
            photos = database.execute(
                "SELECT * FROM photos ORDER BY created_at DESC"
            ).fetchall()
        return render_template(
            "index.html",
            photos=photos,
            csrf_token=csrf_token(app),
            raw_upload_url="",
            batch_id=batch_id,
            stages=stages,
        )

    @app.get("/admin/staging/<stage_id>/preview.webp")
    def staged_preview(stage_id: str):
        record = load_stage_record(app, stage_id)
        return send_file(record["preview_path"], mimetype="image/webp")

    @app.post("/admin/publish/<batch_id>")
    def publish_batch(batch_id: str):
        if not valid_csrf_token(app):
            abort(400, "Invalid form token. Refresh the page and try again.")

        stage_ids = load_batch(app, batch_id)
        records = [load_stage_record(app, stage_id) for stage_id in stage_ids]
        failed_ids = []
        published = 0
        for record in records:
            try:
                publish_stage(app, record)
                published += 1
            except (
                json.JSONDecodeError,
                OSError,
                sqlite3.Error,
                subprocess.SubprocessError,
                PhotoProcessingError,
            ):
                failed_ids.append(record["id"])

        batch_path = stage_path(app, batch_id, ".batch.json")
        if failed_ids:
            save_batch(app, batch_id, failed_ids)
            return redirect(
                url_for(
                    "review_batch",
                    batch_id=batch_id,
                    failed=len(failed_ids),
                    published=published,
                )
            )
        batch_path.unlink(missing_ok=True)
        return redirect(url_for("public_preview", published=published))

    @app.post("/admin/upload")
    def upload_photo():
        return handle_upload(app, STANDARD_EXTENSIONS)

    @app.post("/admin/upload/raw")
    def upload_raw_photo():
        request.max_content_length = app.config["RAW_MAX_CONTENT_LENGTH"]
        return handle_upload(app, RAW_EXTENSIONS)

    @app.post("/admin/photos/<photo_id>/delete")
    def delete_photo(photo_id: str):
        if not valid_csrf_token(app):
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
        if photo["sidecar_path"]:
            Path(photo["sidecar_path"]).unlink(missing_ok=True)
        (app.config["PUBLIC_PHOTO_DIR"] / photo["public_filename"]).unlink(
            missing_ok=True
        )
        write_manifest(app)
        return redirect(url_for("admin_index", deleted="1"))

    @app.errorhandler(RequestEntityTooLarge)
    def upload_too_large(_error):
        return "Upload exceeds the configured size limit.", 413

    @app.errorhandler(PhotoProcessingError)
    def raw_processing_failed(error):
        return str(error), 422
