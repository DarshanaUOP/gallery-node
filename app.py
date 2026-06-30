#!/usr/bin/env python3
"""
Luminary — Local Media Gallery Backend
app.py: Filesystem scanner, metadata extractor, and REST API
"""

import os
import sys
import json
import uuid
import hashlib
import logging
import sqlite3
import threading
from pathlib import Path
from datetime import datetime

# ── optional deps (graceful degradation) ──────────────────────────────────────
try:
    from PIL import Image
    from PIL.ExifTags import TAGS, GPSTAGS
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    print("[WARN] Pillow not installed — EXIF extraction disabled. Run: pip install Pillow")

try:
    from flask import Flask, jsonify, request
    from flask_cors import CORS
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False
    print("[WARN] Flask/flask-cors not installed. Run: pip install flask flask-cors")

# ── config ─────────────────────────────────────────────────────────────────────
BASE_DIR       = Path(__file__).parent
DATA_DIR       = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

MEDIA_JSON     = DATA_DIR / "media.json"          # source directory config — stays JSON (small, human-edited)
CONFIG_JSON    = DATA_DIR / "configuration.json"  # app settings — stays JSON (small, human-edited)
SQLITE_DB      = DATA_DIR / "luminary.db"          # media + albums — SQLite (replaces db.json / albums.json)
LOGS_DIR       = BASE_DIR / "logs"

# ── bootstrap missing data files ───────────────────────────────────────────────
def _bootstrap():
    """Create default data files on first run if they don't exist."""
    if not MEDIA_JSON.exists():
        import json as _json
        MEDIA_JSON.write_text(_json.dumps([
            {
                "name": "My Photos",
                "path": str(Path.home() / "Pictures"),
                "visibility": True
            }
        ], indent=2), encoding="utf-8")
        print(f"[INFO] Created default {MEDIA_JSON} — edit it to point at your photo directories.")

    if not CONFIG_JSON.exists():
        import json as _json
        CONFIG_JSON.write_text(_json.dumps({
            "thumbnail_size": 400,
            "thumbnail_quality": 60,
            "thumbnail_cache_path": "",
            "lazy_load_batch": 50,
            "supported_image_formats": [
                "jpg", "jpeg", "png", "heic", "heif", "webp", "tiff", "bmp", "gif"
            ],
            "supported_video_formats": [
                "mp4", "mov", "avi", "mkv", "webm", "m4v", "3gp", "wmv", "flv", "ts", "mts"
            ],
            "show_hidden_default": False,
            "api_port": 5000,
            "log_level": "INFO"
        }, indent=2), encoding="utf-8")
        print(f"[INFO] Created default {CONFIG_JSON}.")

_bootstrap()

# ── logging setup ──────────────────────────────────────────────────────────────
LOGS_DIR.mkdir(exist_ok=True)

_log_formatter = logging.Formatter(
    "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Console handler
_console_handler = logging.StreamHandler(sys.stdout)
_console_handler.setFormatter(_log_formatter)

# Daily rotating file handler — one file per day: logs/log-yyyy-mm-dd.log
from logging.handlers import TimedRotatingFileHandler
_log_file = LOGS_DIR / f"log-{datetime.now().strftime('%Y-%m-%d')}.log"
_file_handler = TimedRotatingFileHandler(
    filename=str(_log_file),
    when="midnight",        # rotate at midnight
    interval=1,             # every 1 day
    backupCount=30,         # keep 30 days of logs
    encoding="utf-8",
    utc=False,
)
# Rename rotated files to log-yyyy-mm-dd.log instead of the default .log.YYYY-MM-DD suffix
_file_handler.namer = lambda name: str(
    LOGS_DIR / ("log-" + name.rsplit(".", 1)[-1] + ".log")
    if "." in Path(name).name else name
)
_file_handler.setFormatter(_log_formatter)

logging.basicConfig(level=logging.INFO, handlers=[_console_handler, _file_handler])
log = logging.getLogger("luminary")

# ── media type sets ────────────────────────────────────────────────────────────
IMAGE_FORMATS = {"jpg", "jpeg", "png", "heic", "heif", "webp", "tiff", "bmp", "gif"}
VIDEO_FORMATS = {"mp4", "mov", "avi", "mkv", "webm", "m4v", "3gp", "wmv", "flv", "ts", "mts"}

def is_video(path: str) -> bool:
    return Path(path).suffix.lstrip(".").lower() in VIDEO_FORMATS


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def load_json(path: Path, default=None):
    """Still used for media.json and configuration.json (small, human-edited files)."""
    if default is None:
        default = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        log.warning("Could not load %s: %s", path, e)
        return default

def save_json(path: Path, data):
    """Still used for media.json and configuration.json."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    log.info("Saved %s", path)


# ══════════════════════════════════════════════════════════════════════════════
#  SQLITE LAYER  —  replaces db.json / albums.json
# ══════════════════════════════════════════════════════════════════════════════
#
# Design: media metadata is stored as a JSON blob (full fidelity, matches the
# exact dict shape the rest of the app already expects) PLUS a handful of
# frequently filtered/sorted fields are extracted into real indexed columns
# (format, camera_label, source_root, date_sort, is_hidden). This gives O(log n)
# filtering/sorting via SQL while every route keeps working with plain Python
# list[dict] — load_media()/save_media()/load_albums()/save_albums() preserve
# their exact original signatures so no route code needs to change.
#
# A thread-local connection is used since Flask's dev server may serve
# requests from multiple threads; each thread gets its own sqlite3 connection.
# WAL mode allows concurrent readers while a write is in progress.

_local = threading.local()

def get_db_conn() -> sqlite3.Connection:
    """Return a thread-local SQLite connection, creating schema on first use."""
    if not hasattr(_local, "conn"):
        conn = sqlite3.connect(str(SQLITE_DB), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return _local.conn

def _init_schema():
    """Create tables and indexes if they don't already exist."""
    conn = get_db_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS media (
            uniqueName    TEXT PRIMARY KEY,
            name          TEXT NOT NULL,
            hash          TEXT,
            type          TEXT NOT NULL DEFAULT 'image',
            isHidden      INTEGER NOT NULL DEFAULT 0,
            format        TEXT,            -- normalised: HEIC, JPEG, MP4, etc.
            camera_label  TEXT,            -- "Make Model", empty if none
            source_root   TEXT,            -- resolved source directory
            file_path     TEXT,            -- directory containing the file
            date_sort     TEXT,            -- modified date, fallback created — used for sorting
            date_created  TEXT,
            metadata_json TEXT NOT NULL    -- full metadata dict as JSON (file/image/video/camera/date/location/software)
        );
        CREATE INDEX IF NOT EXISTS idx_media_format       ON media(format);
        CREATE INDEX IF NOT EXISTS idx_media_camera        ON media(camera_label);
        CREATE INDEX IF NOT EXISTS idx_media_source_root   ON media(source_root);
        CREATE INDEX IF NOT EXISTS idx_media_hidden         ON media(isHidden);
        CREATE INDEX IF NOT EXISTS idx_media_date_sort      ON media(date_sort);
        CREATE INDEX IF NOT EXISTS idx_media_name           ON media(name);
        CREATE INDEX IF NOT EXISTS idx_media_hash           ON media(hash);

        CREATE TABLE IF NOT EXISTS albums (
            id    TEXT PRIMARY KEY,
            name  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS album_media (
            album_id    TEXT NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
            uniqueName  TEXT NOT NULL REFERENCES media(uniqueName) ON DELETE CASCADE,
            position    INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (album_id, uniqueName)
        );
        CREATE INDEX IF NOT EXISTS idx_album_media_album ON album_media(album_id);
    """)
    conn.commit()

# Initialize SQLite schema on module load (creates tables/indexes if missing)
_init_schema()

def _migrate_legacy_json_if_present():
    """
    One-time migration: if old data/db.json or data/albums.json exist from a
    previous JSON-based version and the SQLite tables are still empty, import
    them automatically so existing libraries aren't lost on upgrade.
    """
    conn = get_db_conn()
    media_count = conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]

    legacy_db_json     = DATA_DIR / "db.json"
    legacy_albums_json = DATA_DIR / "albums.json"

    if media_count == 0 and legacy_db_json.exists():
        try:
            legacy_media = load_json(legacy_db_json, [])
            if isinstance(legacy_media, dict):  # old combined {media, albums} shape
                legacy_albums_inline = legacy_media.get("albums", [])
                legacy_media = legacy_media.get("media", [])
            else:
                legacy_albums_inline = []
            if legacy_media:
                save_media(legacy_media)
                log.info("Migrated %d media records from legacy db.json into SQLite", len(legacy_media))
                legacy_db_json.rename(legacy_db_json.with_suffix(".json.migrated"))
            if legacy_albums_inline:
                save_albums(legacy_albums_inline)
        except Exception as e:
            log.warning("Legacy db.json migration failed: %s", e)

    album_count = conn.execute("SELECT COUNT(*) FROM albums").fetchone()[0]
    if album_count == 0 and legacy_albums_json.exists():
        try:
            legacy_albums = load_json(legacy_albums_json, [])
            if legacy_albums:
                save_albums(legacy_albums)
                log.info("Migrated %d albums from legacy albums.json into SQLite", len(legacy_albums))
                legacy_albums_json.rename(legacy_albums_json.with_suffix(".json.migrated"))
        except Exception as e:
            log.warning("Legacy albums.json migration failed: %s", e)

_FORMAT_ALIASES = {"HEIF": "HEIC", "JPG": "JPEG"}

def _row_to_media_dict(row: sqlite3.Row) -> dict:
    """Reconstruct the exact original media dict shape from a DB row."""
    meta = json.loads(row["metadata_json"]) if row["metadata_json"] else {}
    return {
        "name":       row["name"],
        "uniqueName": row["uniqueName"],
        "hash":       row["hash"],
        "type":       row["type"],
        "isHidden":   bool(row["isHidden"]),
        "metadata":   meta,
    }

def _media_dict_to_row(m: dict) -> dict:
    """Extract indexed column values from a media dict's metadata."""
    meta  = m.get("metadata", {}) or {}
    file_ = meta.get("file", {}) or {}
    cam   = meta.get("camera", {}) or {}
    date  = meta.get("date", {}) or {}

    raw_fmt = (file_.get("format") or "").upper().strip()
    fmt     = _FORMAT_ALIASES.get(raw_fmt, raw_fmt)

    make  = (cam.get("make")  or "").strip()
    model = (cam.get("model") or "").strip()
    camera_label = (make + " " + model).strip()

    date_modified = date.get("modified") or ""
    date_created  = date.get("created")  or ""
    date_sort     = date_modified or date_created or ""

    return {
        "uniqueName":    m.get("uniqueName"),
        "name":          m.get("name", ""),
        "hash":          m.get("hash"),
        "type":          m.get("type", "image"),
        "isHidden":      1 if m.get("isHidden") else 0,
        "format":        fmt,
        "camera_label":  camera_label,
        "source_root":   (file_.get("source_root") or "").rstrip("/\\"),
        "file_path":     file_.get("path", ""),
        "date_sort":     date_sort,
        "date_created":  date_created,
        "metadata_json": json.dumps(meta, default=str),
    }

def load_media() -> list:
    """Return the full media list as list[dict] — same shape as the old db.json array."""
    conn = get_db_conn()
    rows = conn.execute("SELECT * FROM media").fetchall()
    return [_row_to_media_dict(r) for r in rows]

def save_media(media: list):
    """
    Replace the entire media table with the given list — same semantics as
    the old save_media(media) which overwrote db.json wholesale.
    """
    conn = get_db_conn()
    with conn:
        conn.execute("DELETE FROM media")
        for m in media:
            r = _media_dict_to_row(m)
            conn.execute("""
                INSERT INTO media
                    (uniqueName, name, hash, type, isHidden, format,
                     camera_label, source_root, file_path, date_sort,
                     date_created, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                r["uniqueName"], r["name"], r["hash"], r["type"], r["isHidden"],
                r["format"], r["camera_label"], r["source_root"], r["file_path"],
                r["date_sort"], r["date_created"], r["metadata_json"],
            ))
    log.info("Saved %d media records to SQLite", len(media))

def upsert_media_rows(media_list: list):
    """
    Insert or update many media records in one transaction without touching
    unrelated rows — used by sync_library for incremental indexing instead of
    a full table replace (much faster for repeated syncs on large libraries).
    """
    conn = get_db_conn()
    with conn:
        for m in media_list:
            r = _media_dict_to_row(m)
            conn.execute("""
                INSERT INTO media
                    (uniqueName, name, hash, type, isHidden, format,
                     camera_label, source_root, file_path, date_sort,
                     date_created, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(uniqueName) DO UPDATE SET
                    name=excluded.name, hash=excluded.hash, type=excluded.type,
                    isHidden=excluded.isHidden, format=excluded.format,
                    camera_label=excluded.camera_label, source_root=excluded.source_root,
                    file_path=excluded.file_path, date_sort=excluded.date_sort,
                    date_created=excluded.date_created, metadata_json=excluded.metadata_json
            """, (
                r["uniqueName"], r["name"], r["hash"], r["type"], r["isHidden"],
                r["format"], r["camera_label"], r["source_root"], r["file_path"],
                r["date_sort"], r["date_created"], r["metadata_json"],
            ))

def get_media_by_unique_name(unique_name: str):
    """Fast indexed lookup of a single media item by primary key."""
    conn = get_db_conn()
    row = conn.execute("SELECT * FROM media WHERE uniqueName = ?", (unique_name,)).fetchone()
    return _row_to_media_dict(row) if row else None

def set_media_hidden(unique_name: str, hidden: bool) -> bool:
    """Toggle isHidden for one record. Returns True if a row was updated."""
    conn = get_db_conn()
    with conn:
        cur = conn.execute(
            "UPDATE media SET isHidden = ? WHERE uniqueName = ?",
            (1 if hidden else 0, unique_name)
        )
    return cur.rowcount > 0

def get_existing_hashes_and_paths() -> tuple:
    """
    Return (set of hashes, set of resolved full paths) for dedup checks during sync.
    Reads only the columns needed instead of full metadata blobs.
    """
    conn = get_db_conn()
    rows = conn.execute("SELECT hash, file_path, name FROM media").fetchall()
    hashes = {r["hash"] for r in rows if r["hash"]}
    paths  = set()
    for r in rows:
        if r["file_path"] and r["name"]:
            try:
                paths.add(str(Path(os.path.join(r["file_path"], r["name"])).resolve()))
            except Exception:
                pass
    return hashes, paths

def query_media(filters: dict, sort: str, offset: int, limit: int) -> tuple:
    """
    Core filtered/sorted/paginated query — the SQL equivalent of the old
    _filter_media() + _sort_media() + list-slicing pipeline, but done entirely
    in SQLite with indexes instead of a full Python list scan.
    Returns (items: list[dict], total: int).
    """
    conn = get_db_conn()

    where = []
    args  = []

    hidden = (filters.get("hidden") or "").strip().lower()
    if hidden == "true":
        where.append("isHidden = 1")
    elif hidden != "include":
        where.append("isHidden = 0")

    fmt = (filters.get("format") or "").strip().upper()
    if fmt:
        where.append("format = ?")
        args.append(fmt)

    cam = (filters.get("camera") or "").strip()
    if cam:
        where.append("camera_label = ?")
        args.append(cam)

    loc = (filters.get("location") or "").strip().rstrip("/\\")
    if loc:
        where.append("(source_root = ? OR file_path LIKE ?)")
        args.append(loc)
        args.append(loc + "%")

    q = (filters.get("q") or "").strip().lower()
    if q:
        where.append("(LOWER(name) LIKE ? OR LOWER(camera_label) LIKE ? OR date_created LIKE ?)")
        like = f"%{q}%"
        args.extend([like, like, like])

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    total = conn.execute(f"SELECT COUNT(*) FROM media {where_sql}", args).fetchone()[0]

    if sort == "date-asc":
        order_sql = "ORDER BY date_sort ASC"
    elif sort == "name":
        order_sql = "ORDER BY name COLLATE NOCASE ASC"
    else:
        order_sql = "ORDER BY date_sort DESC"

    rows = conn.execute(
        f"SELECT * FROM media {where_sql} {order_sql} LIMIT ? OFFSET ?",
        args + [limit, offset]
    ).fetchall()

    return [_row_to_media_dict(r) for r in rows], total

def get_distinct_formats() -> list:
    conn = get_db_conn()
    rows = conn.execute(
        "SELECT DISTINCT format FROM media WHERE format != '' ORDER BY format"
    ).fetchall()
    return [r["format"] for r in rows]

def get_distinct_cameras() -> list:
    conn = get_db_conn()
    rows = conn.execute(
        "SELECT DISTINCT camera_label FROM media WHERE camera_label != '' ORDER BY camera_label"
    ).fetchall()
    return [r["camera_label"] for r in rows]

def get_distinct_source_roots() -> list:
    conn = get_db_conn()
    rows = conn.execute(
        "SELECT DISTINCT source_root FROM media WHERE source_root != '' ORDER BY source_root"
    ).fetchall()
    return [r["source_root"] for r in rows]

def get_media_count() -> int:
    conn = get_db_conn()
    return conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]


# ── Albums ──────────────────────────────────────────────────────────────────

def load_albums() -> list:
    """Return all albums as list[dict] — same shape as the old albums.json array."""
    conn  = get_db_conn()
    rows  = conn.execute("SELECT id, name FROM albums").fetchall()
    out   = []
    for a in rows:
        media_rows = conn.execute(
            "SELECT uniqueName FROM album_media WHERE album_id = ? ORDER BY position",
            (a["id"],)
        ).fetchall()
        out.append({
            "id":    a["id"],
            "name":  a["name"],
            "media": [m["uniqueName"] for m in media_rows],
        })
    return out

def save_albums(albums: list):
    """Replace all albums + their membership — same semantics as the old save_albums()."""
    conn = get_db_conn()
    with conn:
        conn.execute("DELETE FROM album_media")
        conn.execute("DELETE FROM albums")
        for a in albums:
            conn.execute(
                "INSERT INTO albums (id, name) VALUES (?, ?)",
                (a.get("id"), a.get("name", "Untitled"))
            )
            for pos, un in enumerate(a.get("media", [])):
                # Skip media references that don't exist (defensive — avoids FK errors)
                exists = conn.execute(
                    "SELECT 1 FROM media WHERE uniqueName = ?", (un,)
                ).fetchone()
                if exists:
                    conn.execute(
                        "INSERT OR IGNORE INTO album_media (album_id, uniqueName, position) VALUES (?, ?, ?)",
                        (a.get("id"), un, pos)
                    )
    log.info("Saved %d albums to SQLite", len(albums))

def create_album(name: str) -> dict:
    conn  = get_db_conn()
    album = {"name": name, "id": "album_" + str(uuid.uuid4())[:8], "media": []}
    with conn:
        conn.execute("INSERT INTO albums (id, name) VALUES (?, ?)", (album["id"], album["name"]))
    return album

def add_media_to_album(album_id: str, unique_name: str) -> bool:
    conn = get_db_conn()
    album_exists = conn.execute("SELECT 1 FROM albums WHERE id = ?", (album_id,)).fetchone()
    if not album_exists:
        return False
    with conn:
        max_pos = conn.execute(
            "SELECT COALESCE(MAX(position), -1) FROM album_media WHERE album_id = ?", (album_id,)
        ).fetchone()[0]
        conn.execute(
            "INSERT OR IGNORE INTO album_media (album_id, uniqueName, position) VALUES (?, ?, ?)",
            (album_id, unique_name, max_pos + 1)
        )
    return True

# Run legacy JSON → SQLite migration now that save_media/save_albums exist
_migrate_legacy_json_if_present()


def file_hash(filepath: str) -> str:
    """MD5 hash of first 64 KB for fast deduplication."""
    h = hashlib.md5()
    try:
        with open(filepath, "rb") as f:
            h.update(f.read(65536))
    except OSError:
        pass
    return h.hexdigest()


# ══════════════════════════════════════════════════════════════════════════════
#  METADATA EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════

def dms_to_decimal(dms, ref):
    """Convert GPS DMS tuple to decimal degrees."""
    try:
        degrees = float(dms[0])
        minutes = float(dms[1]) / 60
        seconds = float(dms[2]) / 3600
        result = degrees + minutes + seconds
        if ref in ("S", "W"):
            result = -result
        return round(result, 6)
    except Exception:
        return None

def extract_gps(gps_info: dict) -> dict:
    gps = {}
    try:
        lat_dms  = gps_info.get(2)
        lat_ref  = gps_info.get(1)
        lon_dms  = gps_info.get(4)
        lon_ref  = gps_info.get(3)
        if lat_dms and lat_ref:
            gps["latitude"]  = dms_to_decimal(lat_dms, lat_ref)
        if lon_dms and lon_ref:
            gps["longitude"] = dms_to_decimal(lon_dms, lon_ref)
    except Exception:
        pass
    return gps

def extract_video_metadata(filepath: str) -> dict:
    """Extract video metadata using ffprobe. Falls back to filesystem stats."""
    import subprocess
    p    = Path(filepath)
    stat = p.stat()
    meta = {
        "file": {
            "size":   stat.st_size,
            "format": p.suffix.lstrip(".").upper(),
            "path":   str(p.parent) + os.sep,
        },
        "video":  {},
        "date":   {
            "created":  datetime.fromtimestamp(stat.st_ctime).isoformat(),
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        },
        "location": {},
    }
    try:
        cmd = [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_streams", "-show_format",
            str(filepath)
        ]
        r = subprocess.run(cmd, capture_output=True, timeout=15)
        if r.returncode == 0:
            info = json.loads(r.stdout)
            fmt  = info.get("format", {})
            tags = fmt.get("tags", {})

            # Duration
            duration = float(fmt.get("duration", 0))
            meta["video"]["duration"] = round(duration, 2)
            meta["video"]["duration_fmt"] = (
                f"{int(duration//3600):02d}:{int((duration%3600)//60):02d}:{int(duration%60):02d}"
            )

            # Streams
            for s in info.get("streams", []):
                if s.get("codec_type") == "video":
                    meta["video"]["width"]      = s.get("width")
                    meta["video"]["height"]     = s.get("height")
                    meta["video"]["codec"]      = s.get("codec_name", "").upper()
                    meta["video"]["resolution"] = f"{s.get('width')}x{s.get('height')}"
                    # FPS as fraction e.g. "30000/1001" → round
                    fps_raw = s.get("r_frame_rate", "0/1")
                    try:
                        n, d = fps_raw.split("/")
                        meta["video"]["fps"] = round(int(n) / int(d), 2)
                    except Exception:
                        pass
                    break

            # Creation date from tags
            for key in ("creation_time", "date", "com.apple.quicktime.creationdate"):
                if key in tags:
                    try:
                        from dateutil import parser as dp
                        meta["date"]["created"] = dp.parse(tags[key]).isoformat()
                    except Exception:
                        meta["date"]["created"] = tags[key]
                    break

            # GPS from tags
            lat = tags.get("location") or tags.get("com.apple.quicktime.location.ISO6709")
            if lat:
                try:
                    # ISO 6709 format: +37.3861-122.0839/
                    import re
                    m = re.match(r'([+-]\d+\.?\d*)([+-]\d+\.?\d*)', lat)
                    if m:
                        meta["location"]["latitude"]  = float(m.group(1))
                        meta["location"]["longitude"] = float(m.group(2))
                except Exception:
                    pass

    except (FileNotFoundError, subprocess.TimeoutExpired):
        log.warning("ffprobe not found — video metadata limited. Install ffmpeg for full support.")
    except Exception as e:
        log.warning("Video metadata extraction failed for %s: %s", filepath, e)

    return meta


def extract_metadata(filepath: str) -> dict:
    """Dispatch to image or video metadata extractor."""
    if is_video(filepath):
        return extract_video_metadata(filepath)
    return extract_image_metadata(filepath)


def open_image_any_format(filepath: str):
    """
    Open any supported image file and return a PIL Image object.
    Handles HEIC/HEIF via pillow-heif, pyheif, or system tools (ImageMagick/ffmpeg).
    All other formats use PIL.Image.open() directly.
    Raises RuntimeError if no decoder is available for HEIC.
    """
    from PIL import Image as PilImage
    ext = Path(filepath).suffix.lower()

    if ext in (".heic", ".heif"):
        # Strategy 1: pillow-heif (preferred — pip install pillow-heif)
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
            img = PilImage.open(filepath)
            return img.copy()
        except ImportError:
            pass
        except Exception:
            pass

        # Strategy 2: pyheif
        try:
            import pyheif
            heif_file = pyheif.read(filepath)
            return PilImage.frombytes(
                heif_file.mode, heif_file.size, heif_file.data,
                "raw", heif_file.mode, heif_file.stride,
            )
        except ImportError:
            pass
        except Exception:
            pass

        # Strategy 3: system tools — ImageMagick or ffmpeg
        import subprocess, tempfile
        for cmd_fn in [
            lambda s, d: ["magick", s, d],
            lambda s, d: ["convert", s, d],
            lambda s, d: ["ffmpeg", "-y", "-i", s, d],
        ]:
            try:
                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                    tmp_path = tmp.name
                r = subprocess.run(cmd_fn(filepath, tmp_path),
                                   capture_output=True, timeout=30)
                if r.returncode == 0 and os.path.isfile(tmp_path):
                    img = PilImage.open(tmp_path).copy()
                    os.unlink(tmp_path)
                    return img
            except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
                continue

        raise RuntimeError(
            f"No HEIC decoder found for {filepath}. "
            "Run: pip install pillow-heif"
        )

    # Standard formats
    return PilImage.open(filepath).copy()


def extract_image_metadata(filepath: str) -> dict:
    p = Path(filepath)
    stat = p.stat()

    meta = {
        "file": {
            "size":   stat.st_size,
            "format": p.suffix.lstrip(".").upper(),
            "path":   str(p.parent) + os.sep,
        },
        "image":    {},
        "camera":   {},
        "date":     {
            "created":  datetime.fromtimestamp(stat.st_ctime).isoformat(),
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        },
        "location": {},
        "software": {},
    }

    if not PIL_AVAILABLE:
        return meta

    try:
        img = open_image_any_format(filepath)
        with img:
            w, h = img.size
            orientation = "landscape" if w >= h else "portrait"
            meta["image"] = {
                "width":       w,
                "height":      h,
                "resolution":  f"{w}x{h}",
                "color_space": img.mode,
                "orientation": orientation,
            }

            # Try both Pillow EXIF APIs (newer getexif() and older _getexif())
            exif_obj  = None
            exif_data = None
            try:
                if hasattr(img, "getexif"):
                    exif_obj = img.getexif()
                    if exif_obj:
                        exif_data = dict(exif_obj)
            except Exception:
                pass
            if not exif_data and hasattr(img, "_getexif"):
                try:
                    exif_data = img._getexif()
                except Exception:
                    pass

            GPS_TAG_ID = 0x8825   # 34853 — GPSInfo IFD pointer

            for tag_id, value in (exif_data or {}).items():
                tag = TAGS.get(tag_id, tag_id)

                if tag == "DateTimeOriginal":
                    try:
                        dt = datetime.strptime(str(value), "%Y:%m:%d %H:%M:%S")
                        meta["date"]["created"] = dt.isoformat()
                    except ValueError:
                        pass

                elif tag == "Make":
                    meta["camera"]["make"] = str(value).strip()
                elif tag == "Model":
                    meta["camera"]["model"] = str(value).strip()
                elif tag == "LensModel":
                    meta["camera"]["lens"] = str(value)
                elif tag == "ISOSpeedRatings":
                    meta["camera"]["iso"] = int(value) if isinstance(value, (int, float)) else str(value)
                elif tag == "FNumber":
                    try:
                        meta["camera"]["aperture"] = f"f/{float(value):.1f}"
                    except Exception:
                        pass
                elif tag == "ExposureTime":
                    try:
                        v = float(value)
                        meta["camera"]["shutter_speed"] = f"1/{int(round(1/v))}" if v < 1 else f"{v}s"
                    except Exception:
                        pass
                elif tag == "FocalLength":
                    try:
                        meta["camera"]["focal_length"] = f"{float(value):.0f}mm"
                    except Exception:
                        pass
                elif tag == "Software":
                    meta["software"]["editor"] = str(value).strip()

                elif tag == "GPSInfo" or tag_id == GPS_TAG_ID:
                    gps_dict = None
                    try:
                        if isinstance(value, dict):
                            # _getexif() already decoded the GPS sub-IFD into a dict
                            gps_dict = value
                        elif isinstance(value, int) and exif_obj is not None:
                            # getexif() returns the raw IFD offset as int;
                            # use get_ifd() on the still-open exif_obj to decode it
                            raw_ifd = exif_obj.get_ifd(GPS_TAG_ID)
                            if raw_ifd:
                                gps_dict = dict(raw_ifd)
                        elif hasattr(value, "items"):
                            gps_dict = dict(value)
                    except Exception:
                        pass
                    if gps_dict:
                        coords = extract_gps(gps_dict)
                        meta["location"].update(coords)

    except Exception as e:
        log.warning("Metadata extraction failed for %s: %s", filepath, e)

    return meta


# ══════════════════════════════════════════════════════════════════════════════
#  SCANNER / SYNC
# ══════════════════════════════════════════════════════════════════════════════

def load_config() -> dict:
    defaults = {
        # Appearance
        "theme":                    "dark",       # dark | light | system
        "grid_columns":             4,            # 2 | 3 | 4 | auto
        "card_size":                "medium",     # small | medium | large
        "show_filename_on_card":    True,
        "show_date_on_card":        True,
        "show_subfolder_on_card":   True,
        # Sorting & Filtering
        "default_sort":             "date-desc",  # date-desc | date-asc | name
        "default_date_field":       "modified",   # modified | created
        "show_hidden_default":      False,
        # Performance
        "lazy_load_batch":          50,
        "media_page_size":          500,
        "thumbnail_size":           400,
        "thumbnail_quality":        60,
        "thumbnail_cache_path":     "",
        # Media Types
        "supported_image_formats":  list(IMAGE_FORMATS),
        "supported_video_formats":  list(VIDEO_FORMATS),
        "video_autoplay":           False,
        "video_preload":            "metadata",   # none | metadata | auto
        # Sync Behaviour
        "follow_symlinks":          True,
        "skip_hidden_dirs":         True,
        "max_scan_depth":           0,            # 0 = unlimited
        "dedup_method":             "both",       # path | hash | both
        # Metadata
        "show_gps_in_metadata":     True,
        "extract_video_metadata":   True,
        # Server
        "api_port":                 5000,
        "log_level":                "INFO",
        "log_retention_days":       30,
    }
    cfg = load_json(CONFIG_JSON, defaults)
    for k, v in defaults.items():
        cfg.setdefault(k, v)
    return cfg

def sync_library() -> dict:
    """Scan configured directories and incrementally index new media into SQLite."""
    cfg     = load_config()
    sources = load_json(MEDIA_JSON, [])

    supported = (
        {f.lower() for f in cfg.get("supported_image_formats", [])} |
        {f.lower() for f in cfg.get("supported_video_formats", [])}
    )

    # Fast dedup lookup — reads only hash/path columns, not full metadata blobs
    existing_hashes, existing_paths = get_existing_hashes_and_paths()

    added       = 0
    scanned     = 0
    new_entries = []   # batched and upserted once at the end of each source

    for source in sources:
        if not source.get("visibility", True):
            log.info("Skipping hidden source: %s", source.get("name"))
            continue

        dir_path = source.get("path", "").rstrip("/\\")
        if not os.path.isdir(dir_path):
            log.warning("Directory not found: %s", dir_path)
            continue

        source_root     = str(Path(dir_path).resolve())
        follow_links    = cfg.get("follow_symlinks", True)
        skip_hidden     = cfg.get("skip_hidden_dirs", True)
        max_depth       = int(cfg.get("max_scan_depth", 0))   # 0 = unlimited
        dedup_method    = cfg.get("dedup_method", "both")
        source_depth    = source_root.rstrip("/").count("/")
        log.info("Scanning: %s → %s (recursive)", source.get("name", dir_path), source_root)

        for root, dirs, files in os.walk(dir_path, followlinks=follow_links):
            if skip_hidden:
                dirs[:] = [d for d in dirs if not d.startswith(".")]
            if max_depth > 0:
                current_depth = str(Path(root).resolve()).rstrip("/").count("/") - source_depth
                if current_depth >= max_depth:
                    dirs.clear()
            dirs.sort()
            for filename in sorted(files):
                ext = Path(filename).suffix.lstrip(".").lower()
                if ext not in supported:
                    continue

                full_path     = os.path.join(root, filename)
                resolved_path = str(Path(full_path).resolve())
                scanned += 1

                if dedup_method in ("path", "both"):
                    if resolved_path in existing_paths:
                        log.debug("Already indexed (path): %s", resolved_path)
                        continue

                if dedup_method in ("hash", "both"):
                    fhash = file_hash(full_path)
                    if fhash in existing_hashes:
                        log.debug("Duplicate (hash): %s", filename)
                        continue
                else:
                    fhash = file_hash(full_path)

                media_type = "video" if is_video(full_path) else "image"
                meta       = extract_metadata(full_path)
                meta["file"]["path"] = str(Path(full_path).parent) + os.sep
                try:
                    rel = str(Path(full_path).relative_to(source_root))
                    meta["file"]["relative_path"] = rel
                    meta["file"]["source_root"]   = source_root
                    rel_dir = str(Path(rel).parent)
                    meta["file"]["subfolder"] = "" if rel_dir == "." else rel_dir
                except ValueError:
                    meta["file"]["relative_path"] = filename
                    meta["file"]["source_root"]   = source_root
                    meta["file"]["subfolder"]     = ""

                entry = {
                    "name":       filename,
                    "uniqueName": str(uuid.uuid4()),
                    "hash":       fhash,
                    "type":       media_type,
                    "isHidden":   False,
                    "metadata":   meta,
                }
                new_entries.append(entry)
                existing_hashes.add(fhash)
                existing_paths.add(resolved_path)
                added += 1
                log.info("Indexed [%s]: %s", media_type, meta["file"].get("relative_path", filename))

                # Flush in batches of 200 to keep memory bounded on very large libraries
                if len(new_entries) >= 200:
                    upsert_media_rows(new_entries)
                    new_entries = []

    if new_entries:
        upsert_media_rows(new_entries)

    total = get_media_count()
    log.info("Sync complete — scanned %d, added %d, total %d", scanned, added, total)
    return {"added": added, "scanned": scanned, "total": total}


# ══════════════════════════════════════════════════════════════════════════════
#  FLASK API
# ══════════════════════════════════════════════════════════════════════════════

if FLASK_AVAILABLE:
    app = Flask(__name__, static_folder=".", static_url_path="")
    CORS(app, expose_headers=["Content-Range", "Accept-Ranges", "Content-Length", "Content-Type"])

    @app.route("/")
    def serve_index():
        return app.send_static_file("index.html")

    @app.route("/api/config", methods=["GET"])
    def api_config_get():
        """Return configuration.json from data/."""
        return jsonify(load_config())

    @app.route("/api/config", methods=["POST"])
    def api_config_post():
        """Save updated configuration to data/configuration.json."""
        data = request.get_json(force=True)
        if not isinstance(data, dict):
            return jsonify({"error": "Expected a JSON object"}), 400
        # Merge with existing — never wipe keys we don't know about
        cfg = load_config()
        cfg.update(data)
        save_json(CONFIG_JSON, cfg)
        log.info("configuration.json updated")
        return jsonify({"ok": True})

    @app.route("/api/media/count", methods=["GET"])
    def api_media_count():
        """Return total number of indexed media items — single indexed COUNT(*)."""
        return jsonify({"total": get_media_count()})

    @app.route("/api/media/formats", methods=["GET"])
    def api_media_formats():
        """
        Return all unique normalised format strings present in the media table.
        Aliases applied at write-time: HEIF→HEIC, JPG→JPEG.
        Uses SELECT DISTINCT on an indexed column instead of scanning every record.
        """
        return jsonify(get_distinct_formats())

    @app.route("/api/media/cameras", methods=["GET"])
    def api_media_cameras():
        """
        Return all unique non-empty 'Make Model' camera strings.
        Uses SELECT DISTINCT on an indexed column.
        """
        return jsonify(get_distinct_cameras())

    @app.route("/api/media/locations", methods=["GET"])
    def api_media_locations():
        """
        Return all known locations — union of:
          1. Every entry in media.json (configured sources, regardless of sync state)
          2. Every unique source_root in the media table (indexed DISTINCT query)
        Response: [ { root: str, label: str }, … ] sorted by label.
        """
        sources  = load_json(MEDIA_JSON, [])
        name_map = {}   # normalised path → label

        for src in sources:
            raw  = (src.get("path") or "").rstrip("/\\")
            name = (src.get("name") or "").strip()
            if raw:
                name_map[raw] = name or raw.split("/")[-1] or raw

        for root in get_distinct_source_roots():
            if root and root not in name_map:
                name_map[root] = root.split("/")[-1] or root

        result = sorted(
            [{"root": r, "label": l} for r, l in name_map.items()],
            key=lambda x: x["label"].lower()
        )
        return jsonify(result)

    @app.route("/api/media", methods=["GET"])
    def api_media():
        """
        Return a filtered, sorted, paginated slice of the media table.
        Entirely SQL-backed — filtering, sorting, and pagination all happen
        inside SQLite using indexed columns (format, camera_label, source_root,
        isHidden, date_sort), avoiding a full Python scan of the dataset.

        Query params:
          offset   (int,    default 0)          — start index
          limit    (int,    default 500)        — max items to return
          sort     (str,    default date-desc)  — date-desc | date-asc | name
          format   (str,    optional)           — filter by format e.g. HEIC
          camera   (str,    optional)           — filter by "Make Model"
          location (str,    optional)           — filter by source_root path
          q        (str,    optional)           — search string
          hidden   (str,    optional)           — true | false | include
        Response:
          { items, offset, limit, total, has_more }
        """
        sort   = request.args.get("sort", "date-desc")
        offset = max(0, int(request.args.get("offset", 0)))
        limit  = min(2000, max(1, int(request.args.get("limit", 500))))
        items, total = query_media(request.args, sort, offset, limit)
        return jsonify({
            "items":    items,
            "offset":   offset,
            "limit":    limit,
            "total":    total,
            "has_more": (offset + limit) < total,
        })

    # ── shared image helpers ──────────────────────────────────────────────────

    def _resolve_path(unique_name):
        """Return (full_path, item) or raise 404. Uses indexed SQLite primary key lookup."""
        from flask import abort
        item = get_media_by_unique_name(unique_name)
        if not item:
            abort(404)
        file_dir  = item.get("metadata", {}).get("file", {}).get("path", "")
        full_path = os.path.join(file_dir, item["name"])
        if not os.path.isfile(full_path):
            log.warning("File not found on disk: %s", full_path)
            abort(404)
        return full_path, item

    def _open_as_pil(full_path):
        """Delegate to module-level open_image_any_format (handles HEIC/HEIF)."""
        return open_image_any_format(full_path)

    def _jpeg_response(img, quality, max_side=None):
        """Resize (if requested) and return a JPEG Flask Response."""
        import io
        from flask import Response
        img = img.convert("RGB")
        if max_side:
            img.thumbnail((max_side, max_side), resample=3)  # LANCZOS=3
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        buf.seek(0)
        return Response(buf, mimetype="image/jpeg",
                        headers={"Cache-Control": "max-age=86400"})

    def _get_thumb_cache_dir() -> Path:
        """Read thumbnail_cache_path from configuration.json. Falls back to .thumb_cache/ next to app.py."""
        cfg = load_config()
        raw = cfg.get("thumbnail_cache_path", "")
        if raw:
            p = Path(raw).expanduser()
        else:
            p = BASE_DIR / ".thumb_cache"
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log.warning("Cannot create thumbnail cache dir %s: %s — falling back", p, e)
            p = BASE_DIR / ".thumb_cache"
            p.mkdir(parents=True, exist_ok=True)
        return p

    def _thumb_cache_path(unique_name, size, quality):
        return _get_thumb_cache_dir() / f"{unique_name}_{size}q{quality}.jpg"

    # ── /api/thumb/<unique_name>  — small, cached, for grid ──────────────────
    @app.route("/api/thumb/<unique_name>", methods=["GET"])
    def api_thumb(unique_name):
        """
        Return a small thumbnail (default 400px max-side, quality 60).
        For videos: extracts a frame with ffmpeg (at 10% of duration or 1s).
        Result is cached to disk so decoding only happens once.
        """
        from flask import Response, abort
        import subprocess, tempfile
        cfg        = load_config()
        size       = int(request.args.get("size",    cfg.get("thumbnail_size",    400)))
        quality    = int(request.args.get("quality", cfg.get("thumbnail_quality", 60)))
        cache_file = _thumb_cache_path(unique_name, size, quality)

        # Serve from disk cache if available
        if cache_file.is_file():
            with open(cache_file, "rb") as f:
                data = f.read()
            return Response(data, mimetype="image/jpeg",
                            headers={"Cache-Control": "max-age=86400"})

        full_path, item = _resolve_path(unique_name)

        # ── Video thumbnail: extract frame with ffmpeg ────────────────────────
        if item.get("type") == "video" or is_video(full_path):
            try:
                # Seek to 10% of duration (or 1s fallback) for a meaningful frame
                duration = item.get("metadata", {}).get("video", {}).get("duration", 10)
                seek_sec = max(1.0, round(float(duration) * 0.10, 2))

                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                    tmp_path = tmp.name

                cmd = [
                    "ffmpeg", "-y",
                    "-ss", str(seek_sec),
                    "-i", str(full_path),
                    "-vframes", "1",
                    "-vf", f"scale={size}:{size}:force_original_aspect_ratio=decrease",
                    "-q:v", "3",
                    tmp_path
                ]
                r = subprocess.run(cmd, capture_output=True, timeout=30)
                if r.returncode == 0 and os.path.isfile(tmp_path):
                    with open(tmp_path, "rb") as f:
                        data = f.read()
                    os.unlink(tmp_path)
                    try:
                        with open(cache_file, "wb") as f:
                            f.write(data)
                    except OSError as e:
                        log.warning("Could not write thumb cache: %s", e)
                    return Response(data, mimetype="image/jpeg",
                                    headers={"Cache-Control": "max-age=86400"})
                else:
                    if os.path.isfile(tmp_path):
                        os.unlink(tmp_path)
                    log.warning("ffmpeg frame extract failed for %s", full_path)
            except (FileNotFoundError, subprocess.TimeoutExpired) as e:
                log.warning("ffmpeg not available for video thumbnail: %s", e)
            # Return a generic video placeholder on failure
            return _video_placeholder_response(size)

        # ── Image thumbnail ───────────────────────────────────────────────────
        try:
            img  = _open_as_pil(full_path)
            resp = _jpeg_response(img, quality=quality, max_side=size)
            try:
                with open(cache_file, "wb") as f:
                    f.write(resp.get_data())
            except OSError as e:
                log.warning("Could not write thumb cache: %s", e)
            return resp
        except RuntimeError as e:
            log.error(str(e))
            return Response(json.dumps({"error": str(e)}),
                            status=415, mimetype="application/json")

    # ── /api/image/<unique_name>  — full quality, for lightbox ───────────────
    @app.route("/api/image/<unique_name>", methods=["GET"])
    def api_image(unique_name):
        """
        Return the full-resolution image (JPEG quality 92).
        HEIC/HEIF are transcoded; all others are re-encoded to ensure
        browser compatibility and avoid 206 partial-content issues.
        """
        from flask import send_file, Response, abort
        import mimetypes
        full_path, _ = _resolve_path(unique_name)
        ext = Path(full_path).suffix.lower()

        # Non-HEIC formats that browsers handle natively — send directly
        if ext not in (".heic", ".heif"):
            mime, _ = mimetypes.guess_type(full_path)
            return send_file(full_path, mimetype=mime or "image/jpeg",
                             conditional=False)

        # HEIC — transcode to JPEG at full resolution
        try:
            img = _open_as_pil(full_path)
            return _jpeg_response(img, quality=92)
        except RuntimeError as e:
            log.error(str(e))
            return Response(json.dumps({"error": str(e)}),
                            status=415, mimetype="application/json")

    def _video_placeholder_response(size):
        """Return a minimal dark JPEG with a play symbol for videos with no extractable frame."""
        import io
        from flask import Response
        try:
            from PIL import Image as PilImage, ImageDraw
            img  = PilImage.new("RGB", (size, size), color=(20, 20, 24))
            draw = ImageDraw.Draw(img)
            cx, cy, r = size // 2, size // 2, size // 5
            triangle = [
                (cx - r // 2, cy - r),
                (cx - r // 2, cy + r),
                (cx + r, cy),
            ]
            draw.polygon(triangle, fill=(180, 180, 180))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=70)
            buf.seek(0)
            return Response(buf, mimetype="image/jpeg",
                            headers={"Cache-Control": "max-age=86400"})
        except Exception:
            return Response(b"", status=204)

    # ── /api/video/<unique_name>  — byte-range streaming for <video> ──────────
    @app.route("/api/video/<unique_name>", methods=["GET", "HEAD"])
    def api_video(unique_name):
        """
        Stream video with proper HTTP range-request support.
        Uses send_file for simple requests; manual range slicing for seek requests.
        """
        import mimetypes
        from flask import Response, abort, send_file

        full_path, _ = _resolve_path(unique_name)

        if not is_video(full_path):
            abort(415)

        # MIME type
        ext  = Path(full_path).suffix.lower()
        mime = {
            ".mp4":  "video/mp4",
            ".m4v":  "video/mp4",
            ".mov":  "video/quicktime",
            ".avi":  "video/x-msvideo",
            ".mkv":  "video/x-matroska",
            ".webm": "video/webm",
            ".3gp":  "video/3gpp",
            ".wmv":  "video/x-ms-wmv",
            ".flv":  "video/x-flv",
            ".ts":   "video/mp2t",
            ".mts":  "video/mp2t",
        }.get(ext, mimetypes.guess_type(full_path)[0] or "application/octet-stream")

        file_size    = os.path.getsize(full_path)
        range_header = request.headers.get("Range", None)

        # ── HEAD request ──────────────────────────────────────────────────────
        if request.method == "HEAD":
            return Response(status=200, headers={
                "Accept-Ranges":  "bytes",
                "Content-Length": str(file_size),
                "Content-Type":   mime,
            })

        # ── No Range header: full file, 200 ──────────────────────────────────
        if not range_header:
            resp = send_file(full_path, mimetype=mime, conditional=False)
            resp.headers["Accept-Ranges"]  = "bytes"
            resp.headers["Content-Length"] = str(file_size)
            resp.headers["Cache-Control"]  = "no-store"
            return resp

        # ── Range request: respond with 206 ──────────────────────────────────
        try:
            raw   = range_header.replace("bytes=", "").strip()
            parts = raw.split("-")
            start = int(parts[0])
            end   = int(parts[1]) if parts[1].strip() else file_size - 1
        except Exception:
            # Malformed range — send whole file
            resp = send_file(full_path, mimetype=mime, conditional=False)
            resp.headers["Accept-Ranges"] = "bytes"
            return resp

        start  = max(0, start)
        end    = min(end, file_size - 1)
        length = end - start + 1

        # Read the exact requested byte range into memory
        # (safe for typical browser chunks of 256 KB – 2 MB)
        with open(full_path, "rb") as f:
            f.seek(start)
            data = f.read(length)

        return Response(
            data,
            status=206,
            mimetype=mime,
            headers={
                "Content-Range":  f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges":  "bytes",
                "Content-Length": str(len(data)),
                "Cache-Control":  "no-store",
            },
        )

    @app.route("/api/albums", methods=["GET"])
    def api_albums():
        return jsonify(load_albums())

    @app.route("/api/locations", methods=["GET"])
    def api_locations_get():
        """
        Return current contents of media.json.
        Each entry: { name, path, visibility, root, label }
        'root' and 'label' aliases are included so this endpoint can be used
        directly by the location filter dropdowns without a separate /api/media/locations call.
        """
        sources = load_json(MEDIA_JSON, [])
        result  = []
        for s in sources:
            path  = (s.get("path") or "").rstrip("/\\")
            name  = (s.get("name") or "").strip()
            label = name or (path.split("/")[-1] if path else path)
            result.append({
                "name":       name,
                "path":       s.get("path", ""),
                "visibility": s.get("visibility", True),
                "root":       path,
                "label":      label,
            })
        return jsonify(result)

    @app.route("/api/locations", methods=["POST"])
    def api_locations_post():
        """Overwrite media.json with updated location list from frontend."""
        data = request.get_json(force=True)
        if not isinstance(data, list):
            return jsonify({"error": "Expected a JSON array"}), 400
        cleaned = []
        for entry in data:
            if not isinstance(entry, dict) or not entry.get("path", "").strip():
                continue
            cleaned.append({
                "name":       str(entry.get("name", entry["path"])).strip(),
                "path":       str(entry["path"]).strip(),
                "visibility": bool(entry.get("visibility", True)),
            })
        save_json(MEDIA_JSON, cleaned)
        log.info("media.json updated — %d locations", len(cleaned))
        return jsonify({"ok": True, "count": len(cleaned)})

    @app.route("/api/sync", methods=["POST"])
    def api_sync():
        result = sync_library()
        return jsonify(result)

    @app.route("/api/db", methods=["GET"])
    def api_db_get():
        """
        Return albums in full + first filtered/sorted page of media.
        Accepts all the same filter params as /api/media.
        Uses the same SQL-backed query_media() as /api/media for performance.
        """
        sort   = request.args.get("sort", "date-desc")
        offset = max(0, int(request.args.get("offset", 0)))
        limit  = min(2000, max(1, int(request.args.get("limit", 500))))
        items, total = query_media(request.args, sort, offset, limit)
        albums = load_albums()
        return jsonify({
            "media":    items,
            "albums":   albums,
            "total":    total,
            "offset":   offset,
            "has_more": (offset + limit) < total,
        })

    @app.route("/api/db", methods=["POST"])
    def api_db_post():
        """
        Accept {media, albums} from frontend and save to SQLite.
        Note: this does a full-table replace for whichever of media/albums is
        provided — same semantics as the original JSON-based endpoint.
        """
        data = request.get_json(force=True)
        if not data:
            return jsonify({"error": "No data"}), 400

        if "media" in data:
            # Preserve hashes for any record that doesn't include one
            existing_hashes, _ = get_existing_hashes_and_paths()
            conn = get_db_conn()
            hash_lookup = {
                r["uniqueName"]: r["hash"]
                for r in conn.execute("SELECT uniqueName, hash FROM media").fetchall()
            }
            for m in data["media"]:
                if not m.get("hash") and m.get("uniqueName") in hash_lookup:
                    m["hash"] = hash_lookup[m["uniqueName"]]
            save_media(data["media"])

        if "albums" in data:
            save_albums(data["albums"])

        return jsonify({"ok": True})

    @app.route("/api/album/create", methods=["POST"])
    def api_album_create():
        body  = request.get_json(force=True) or {}
        name  = body.get("name", "Untitled")
        album = create_album(name)
        return jsonify(album)

    @app.route("/api/album/add", methods=["POST"])
    def api_album_add():
        body        = request.get_json(force=True) or {}
        album_id    = body.get("albumId")
        unique_name = body.get("uniqueName")
        ok = add_media_to_album(album_id, unique_name)
        if ok:
            return jsonify({"ok": True})
        return jsonify({"error": "Album not found"}), 404

    @app.route("/api/media/hide", methods=["POST"])
    def api_media_hide():
        body        = request.get_json(force=True) or {}
        unique_name = body.get("uniqueName")
        hidden      = body.get("hidden", True)
        ok = set_media_hidden(unique_name, hidden)
        if ok:
            return jsonify({"ok": True})
        return jsonify({"error": "Not found"}), 404

    def run_server(port: int = 5000):
        log.info("Starting Luminary backend on http://0.0.0.0:%d", port)
        app.run(host="0.0.0.0", port=port, debug=False)


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Luminary backend")
    parser.add_argument("--sync-only", action="store_true", help="Run sync and exit without starting server")
    parser.add_argument("--port", type=int, default=5000, help="API port (default 5000)")
    args = parser.parse_args()

    if args.sync_only:
        result = sync_library()
        print(json.dumps(result, indent=2))
        sys.exit(0)

    if not FLASK_AVAILABLE:
        print("ERROR: Flask not installed. Run: pip install flask flask-cors")
        sys.exit(1)

    run_server(port=args.port)