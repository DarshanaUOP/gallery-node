#!/usr/bin/env python3
"""
Luminary — Local Media Gallery Backend
app.py: Filesystem scanner, metadata extractor, and REST API
"""

import os
import sys
import json
import uuid
import shutil
import string
import hashlib
import logging
import sqlite3
import platform
import threading
import subprocess
import signal
import atexit
from pathlib import Path
from datetime import datetime
from collections import deque

import app_paths

# ── resolve every filesystem location up front, before any file is read or
#    written (see app_paths.py for the full frozen-vs-dev / migration logic) ──
app_paths.ensure_dirs()
app_paths.migrate_legacy_internal_data()  # no-op unless this is a frozen build's first launch

# ── kwargs to suppress the console window Windows otherwise pops up for every
#    subprocess.run() call into a console-subsystem exe (ffmpeg, ffprobe,
#    ImageMagick). Our own process runs windowless (pythonw / PyInstaller
#    --windowed), but that does NOT stop Windows from allocating a brand new
#    console for each *child* process unless told not to — hence a terminal
#    window flashing open/closed for every single thumbnail. CREATE_NO_WINDOW
#    is the documented fix for exactly this. The flag only exists in the
#    subprocess module on Windows, so it's resolved once here rather than at
#    every call site.
if platform.system() == "Windows":
    _SUBPROCESS_NO_WINDOW_KWARGS = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    _SUBPROCESS_NO_WINDOW_KWARGS = {}

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

try:
    from waitress import serve as _waitress_serve
    WAITRESS_AVAILABLE = True
except ImportError:
    WAITRESS_AVAILABLE = False
    print("[WARN] Waitress not installed — falling back to Flask's dev server. Run: pip install waitress")

# ── config ─────────────────────────────────────────────────────────────────────
# All of these now come from app_paths.py: DATA_DIR/CONFIG_DIR/LOGS_DIR/THUMB_DIR
# resolve to an OS-appropriate per-user location in frozen builds (surviving
# installer upgrades), or to project-relative paths in dev mode (unchanged
# from before). BASE_DIR is kept as an alias for anything below still using
# it for non-user-data purposes (e.g. resolving the frontend static folder).
BASE_DIR       = app_paths.INSTALL_DIR
DATA_DIR       = app_paths.DATA_DIR
CONFIG_DIR     = app_paths.CONFIG_DIR
THUMB_DIR      = app_paths.THUMB_DIR
LOGS_DIR       = app_paths.LOGS_DIR
# directories already created by app_paths.ensure_dirs() above

MEDIA_JSON     = DATA_DIR / "media.json"          # source directory config — stays JSON (small, human-edited)
CONFIG_JSON    = CONFIG_DIR / "configuration.json" # app settings — tracked in git, not gitignored
SQLITE_DB      = DATA_DIR / "luminary.db"          # media + albums — SQLite (replaces db.json / albums.json)

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
            "theme":                    "dark",
            "style":                    "modern",
            "font_size":                "large",
            "grid_columns":             "auto",
            "card_size":                "small",
            "show_filename_on_card":    True,
            "show_date_on_card":        True,
            "show_subfolder_on_card":   True,
            "default_sort":             "date-desc",
            "default_date_field":       "modified",
            "show_hidden_default":      False,
            "lazy_load_batch":          10,
            "media_page_size":          50,
            "thumbnail_size":           400,
            "thumbnail_quality":        60,
            "supported_image_formats":  ["jpg","jpeg","png","heic","heif","webp","tiff","bmp","gif"],
            "supported_video_formats":  ["mp4","mov","avi","mkv","webm","m4v","3gp","wmv","flv","ts","mts"],
            "video_autoplay":           False,
            "video_preload":            "metadata",
            "follow_symlinks":          True,
            "skip_hidden_dirs":         True,
            "max_scan_depth":           0,
            "dedup_method":             "both",
            "show_gps_in_metadata":     True,
            "extract_video_metadata":   True,
            "api_port":                 5000,
            "log_level":                "INFO",
            "log_retention_days":       30
        }, indent=2), encoding="utf-8")
        print(f"[INFO] Created default {CONFIG_JSON}.")

_bootstrap()

# ── logging setup ──────────────────────────────────────────────────────────────

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

_db_lock = threading.Lock()   # serialises all WRITE operations
_local   = threading.local()  # each thread gets its own connection (WAL allows concurrent reads)

def get_db_conn() -> sqlite3.Connection:
    """
    Return a thread-local SQLite connection.
    WAL mode allows concurrent readers; _db_lock serialises writers.
    Each thread (Flask request thread + sync thread) gets its own connection
    so there are no cross-thread sharing issues.
    """
    if not hasattr(_local, "conn") or _local.conn is None:
        conn = sqlite3.connect(str(SQLITE_DB), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")   # wait up to 5s if DB is locked
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

        CREATE TABLE IF NOT EXISTS folders (
            id    TEXT PRIMARY KEY,
            name  TEXT NOT NULL
        );

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

        -- Bell-icon notifications (e.g. a synced location or file going
        -- missing on disk). 'location_path' / 'unique_name' identify the
        -- target this notification is about, used both for de-duplication
        -- (see create_notification()) and for wiring up the "Relocate" action.
        CREATE TABLE IF NOT EXISTS notifications (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            type          TEXT NOT NULL,
            title         TEXT NOT NULL,
            message       TEXT,
            location_path TEXT,
            unique_name   TEXT,
            action        TEXT,
            action_label  TEXT,
            is_read       INTEGER NOT NULL DEFAULT 0,
            created_at    TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_notifications_unread   ON notifications(is_read);
        CREATE INDEX IF NOT EXISTS idx_notifications_location ON notifications(location_path);
    """)
    conn.commit()

    # An album belongs to at most one folder (a folder can hold many albums).
    # Added via a post-hoc ALTER TABLE (rather than in the CREATE TABLE above)
    # so upgrading installs with an existing albums table pick it up too —
    # SQLite has no "ADD COLUMN IF NOT EXISTS", so we check pragma table_info
    # first and only add it once. ON DELETE CASCADE means deleting a folder
    # automatically deletes the albums that were inside it (which in turn
    # cascades to album_media, i.e. only membership rows — never the media
    # table itself, so the underlying files/records are never touched).
    existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(albums)").fetchall()}
    if "folder_id" not in existing_cols:
        conn.execute(
            "ALTER TABLE albums ADD COLUMN folder_id TEXT REFERENCES folders(id) ON DELETE CASCADE"
        )
        conn.commit()
    conn.execute("CREATE INDEX IF NOT EXISTS idx_albums_folder ON albums(folder_id)")
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
    """Return the full media list as list[dict]. Reads are safe without lock in WAL mode."""
    conn = get_db_conn()
    rows = conn.execute("SELECT * FROM media").fetchall()
    return [_row_to_media_dict(r) for r in rows]

def save_media(media: list):
    """
    Replace the entire media table atomically.
    NOTE: this wipes all existing records — only use for migration/reset.
    For incremental updates, use upsert_media_rows().
    """
    conn = get_db_conn()
    with _db_lock:
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
    Insert or update media records without touching unrelated rows.
    Uses sqlite3 connection context manager (with conn:) which handles
    BEGIN/COMMIT/ROLLBACK automatically and is safe from nested transaction issues.
    """
    if not media_list:
        return
    conn = get_db_conn()
    with _db_lock:
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
                        name          = excluded.name,
                        hash          = excluded.hash,
                        type          = excluded.type,
                        isHidden      = excluded.isHidden,
                        format        = excluded.format,
                        camera_label  = excluded.camera_label,
                        source_root   = excluded.source_root,
                        file_path     = excluded.file_path,
                        date_sort     = excluded.date_sort,
                        date_created  = excluded.date_created,
                        metadata_json = excluded.metadata_json
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

def set_media_hidden_bulk(unique_names, hidden: bool) -> int:
    """Set isHidden for a batch of records in one transaction. Returns rows updated."""
    if not unique_names:
        return 0
    conn = get_db_conn()
    with _db_lock:
        with conn:
            cur = conn.executemany(
                "UPDATE media SET isHidden = ? WHERE uniqueName = ?",
                [(1 if hidden else 0, un) for un in unique_names]
            )
    return cur.rowcount if cur.rowcount is not None else len(unique_names)

def delete_media_records(unique_names) -> int:
    """
    Permanently remove media rows for the given uniqueNames — used whenever we
    detect the original source file is gone (deleted/renamed on disk).
    album_media rows cascade automatically (ON DELETE CASCADE). Also clears
    any cached thumbnail files for those uniqueNames. Safe to call with an
    empty list. Returns the number of DB rows actually deleted.
    """
    unique_names = [u for u in (unique_names or []) if u]
    if not unique_names:
        return 0
    conn = get_db_conn()
    deleted = 0
    with _db_lock:
        with conn:
            for un in unique_names:
                cur = conn.execute("DELETE FROM media WHERE uniqueName = ?", (un,))
                deleted += cur.rowcount
    for un in unique_names:
        _delete_thumb_files(un)
    if deleted:
        preview = ", ".join(unique_names[:10]) + ("…" if len(unique_names) > 10 else "")
        log.info("Removed %d media record(s) for missing source file(s): %s", deleted, preview)
    return deleted


def _delete_thumb_files(unique_name: str):
    """
    Delete every cached thumbnail variant for a uniqueName. Cache filenames
    are '<uniqueName>_<size>q<quality>.jpg', so there can be more than one
    per item if it was ever viewed at different sizes/qualities.
    """
    try:
        thumb_dir = _get_thumb_cache_dir()
    except NameError:
        # Flask routes (and _get_thumb_cache_dir) aren't defined in --sync-only
        # mode without Flask installed — fall back to the default thumb dir.
        thumb_dir = THUMB_DIR
    try:
        for f in thumb_dir.glob(f"{unique_name}_*.jpg"):
            try:
                f.unlink()
            except OSError as e:
                log.warning("Could not delete cached thumbnail %s: %s", f, e)
    except OSError as e:
        log.warning("Could not scan thumbnail cache dir for %s: %s", unique_name, e)


def _check_missing_media(source_roots, emit=None) -> int:
    """
    For every media record whose source_root was confirmed reachable this
    sync run, check whether the original file still exists on disk.

    Previously this deleted those records outright. It no longer does —
    a subfolder rename, a temporarily-offline network share, or any other
    transient hiccup used to silently wipe indexed history. Instead, one
    aggregated notification per affected location is raised (see
    create_notification()) so the user can review it from the bell icon
    and relocate the folder if it moved, without losing the index.

    Returns the total number of missing files detected (used only for the
    sync summary counter — nothing is deleted here).
    """
    if not source_roots:
        return 0
    conn = get_db_conn()
    placeholders = ",".join("?" for _ in source_roots)
    rows = conn.execute(
        f"SELECT uniqueName, file_path, name, source_root FROM media WHERE source_root IN ({placeholders})",
        source_roots,
    ).fetchall()

    missing_by_root = {}
    for r in rows:
        full_path = os.path.join(r["file_path"] or "", r["name"] or "")
        if not r["file_path"] or not r["name"] or not os.path.isfile(full_path):
            missing_by_root.setdefault(r["source_root"], []).append(r["uniqueName"])

    total_missing = sum(len(v) for v in missing_by_root.values())

    for root, names in missing_by_root.items():
        raw_path = _configured_path_for_source_root(root)
        count = len(names)
        log.info("Sync: %d file(s) missing under %s — notifying instead of deleting", count, root)
        if emit:
            emit("log", msg=f"{count} file(s) missing under {raw_path} — see notifications")
        create_notification(
            "file_missing",
            title=f"{count} file{'s' if count != 1 else ''} could not be found",
            message=(
                f"Luminary could not find {count} previously-synced file"
                f"{'s' if count != 1 else ''} under \"{raw_path}\". "
                f"They're still in your library — relocate the folder if it moved."
            ),
            location_path=raw_path,
            action="relocate",
            action_label="View Location",
        )

    return total_missing


def _remove_orphaned_source_records(sources, emit=None) -> int:
    """
    Purge media rows whose source_root doesn't match ANY currently
    configured location — as opposed to _check_missing_media() above, which
    only flags individual missing *files* under a location that's still
    configured.

    Why this is needed: relocate_media_source() only repoints a row's
    source_root/file_path when the file it points to can actually be
    confirmed at the new location; a row it can't confirm is deliberately
    left pointing at the OLD source_root rather than guessed at (see its
    docstring). Concretely — location "/a/b/c" (10,000 indexed files) gets
    relocated to "/a/b/d", and 200 of those files aren't found under
    "/a/b/d" (moved separately, renamed, whatever): relocate_media_source
    updates media.json's entry to "/a/b/d" and repoints the other 9,800
    rows, but leaves those 200 rows with source_root still "/a/b/c" —
    a location that no longer appears in media.json at all. Nothing in the
    normal sync loop ever revisits "/a/b/c" again (it isn't in `sources`
    any more), so those rows would otherwise sit in the database forever:
    never rescanned, never flagged missing, never cleaned up, never shown.

    Called once at the very start of every sync — before the missing-file
    check and before the scan/add phase — so a manual Sync doubles as
    garbage collection for exactly this leftover case. Only rows whose
    source_root matches NONE of the currently configured paths (visible or
    hidden — hidden just means "don't scan it right now", not "forget
    it") are removed; a location that's merely offline/unreachable at the
    moment is still configured, so its rows are left alone here (that's
    _check_missing_media's job, and it deliberately doesn't delete either).

    Returns the number of rows removed.
    """
    conn = get_db_conn()

    # Resolve every currently-configured path the same way sync_library
    # resolves it when scanning (str(Path(dir_path).resolve())) so the
    # comparison is apples-to-apples regardless of trailing slashes or a
    # relative vs. absolute config string. resolve() with strict=False (the
    # default) is a pure string normalization, not a filesystem check, so an
    # offline/unreachable location still resolves to the same root it would
    # if it were reachable — it won't get mistaken for orphaned here.
    configured_roots = set()
    for source in sources:
        raw = (source.get("path") or "").rstrip("/\\")
        if not raw:
            continue
        try:
            configured_roots.add(str(Path(raw).resolve()))
        except (OSError, RuntimeError):
            configured_roots.add(raw)

    rows = conn.execute(
        "SELECT source_root, COUNT(*) as cnt FROM media "
        "WHERE source_root IS NOT NULL AND source_root != '' "
        "GROUP BY source_root"
    ).fetchall()
    orphaned_roots = [r["source_root"] for r in rows if r["source_root"] not in configured_roots]
    if not orphaned_roots:
        return 0

    placeholders = ",".join("?" for _ in orphaned_roots)
    to_delete = conn.execute(
        f"SELECT uniqueName FROM media WHERE source_root IN ({placeholders})",
        orphaned_roots,
    ).fetchall()
    unique_names = [r["uniqueName"] for r in to_delete]

    removed = delete_media_records(unique_names)  # also clears cached thumbnails

    if removed:
        for root in orphaned_roots:
            log.info(
                "Sync: purging orphaned records under %s — no longer a configured "
                "location (likely left behind by a relocate that didn't fully resolve)",
                root,
            )
            if emit:
                emit("log", msg=f"Removed stale records for: {root}")
            # The old path is gone from media.json entirely now, so any unread
            # "can't find this location" notification for it would otherwise
            # sit unread forever — clear it, same as a successful relocate does.
            resolve_location_notifications(_configured_path_for_source_root(root))

    return removed


# ─────────────────────────────────────────────
#  NOTIFICATIONS
# ─────────────────────────────────────────────

def _configured_path_for_source_root(source_root: str) -> str:
    """
    Map a resolved source_root (as stored on media rows) back to the raw,
    user-typed path as configured in media.json — this is what the
    Locations Manager UI keys its rows on, so notifications can point back
    at the right row. Falls back to source_root itself if no configured
    entry resolves to it (e.g. the location was since removed).
    """
    sources = load_json(MEDIA_JSON, [])
    for s in sources:
        raw = (s.get("path") or "").rstrip("/\\")
        if not raw:
            continue
        try:
            if str(Path(raw).resolve()) == source_root:
                return raw
        except (OSError, RuntimeError):
            continue
    return source_root


def create_notification(ntype, title, message, location_path=None, unique_name=None,
                         action=None, action_label=None) -> int:
    """
    Insert a new notification — unless an unread one already exists for the
    same (type, target) pair, in which case its title/message/timestamp are
    refreshed instead. This is what keeps a folder that's still unreachable
    on every sync run (or a file that keeps failing to load) from spamming
    the bell with duplicate entries. Returns the notification id.
    """
    conn = get_db_conn()
    with _db_lock:
        with conn:
            row = None
            if location_path:
                row = conn.execute(
                    "SELECT id FROM notifications WHERE type=? AND location_path=? AND is_read=0",
                    (ntype, location_path),
                ).fetchone()
            elif unique_name:
                row = conn.execute(
                    "SELECT id FROM notifications WHERE type=? AND unique_name=? AND is_read=0",
                    (ntype, unique_name),
                ).fetchone()

            if row:
                conn.execute(
                    "UPDATE notifications SET title=?, message=?, created_at=datetime('now') WHERE id=?",
                    (title, message, row["id"]),
                )
                return row["id"]

            cur = conn.execute(
                """INSERT INTO notifications
                       (type, title, message, location_path, unique_name, action, action_label)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (ntype, title, message, location_path, unique_name, action, action_label),
            )
            return cur.lastrowid


def get_notifications(limit: int = 100) -> list:
    """Most recent notifications first, unread ones bubbled to the top."""
    conn = get_db_conn()
    rows = conn.execute(
        "SELECT * FROM notifications ORDER BY is_read ASC, created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_unread_notification_count() -> int:
    conn = get_db_conn()
    return conn.execute("SELECT COUNT(*) FROM notifications WHERE is_read = 0").fetchone()[0]


def mark_notification_read(notif_id: int) -> bool:
    conn = get_db_conn()
    with _db_lock:
        with conn:
            cur = conn.execute("UPDATE notifications SET is_read = 1 WHERE id = ?", (notif_id,))
    return cur.rowcount > 0


def mark_all_notifications_read() -> int:
    conn = get_db_conn()
    with _db_lock:
        with conn:
            cur = conn.execute("UPDATE notifications SET is_read = 1 WHERE is_read = 0")
    return cur.rowcount


def resolve_location_notifications(location_path: str) -> None:
    """Clear unread notifications tied to a location once it's reachable again or has been relocated."""
    if not location_path:
        return
    conn = get_db_conn()
    with _db_lock:
        with conn:
            conn.execute(
                "UPDATE notifications SET is_read = 1 WHERE location_path = ? AND is_read = 0",
                (location_path,),
            )


def relocate_media_source(old_path: str, new_path: str) -> dict:
    """
    "Validate" a relocated location: for every indexed media record whose
    source_root matches old_path, check — one file at a time — whether a
    file of the same name exists at the corresponding relative path under
    new_path. Only records that actually resolve there get repointed; a
    record whose file still can't be found at the new location is left
    untouched (still flagged missing) rather than blindly rewritten, so a
    partial/incorrect folder pick doesn't silently mislink history.

    This assumes the folder's internal subdirectory structure was preserved
    by whatever moved/renamed it (the common case for a plain move/rename),
    since matching is done by relative path, not a recursive filename scan.

    IMPORTANT: old_path is matched as-is (rstripped, not re-resolved) — not
    re-run through Path.resolve(). old_path's folder is, by definition,
    gone (that's the whole reason this is being called), and resolving a
    path that no longer exists can't canonicalize through any symlink or
    mount point the way it could when the folder was scanned and its
    source_root first stored — so re-resolving here could produce a string
    that no longer matches any row at all, silently finding zero records.
    Matching on the raw configured path avoids that, and mirrors the same
    exact-OR-prefix comparison already used elsewhere for this (see
    count_media_by_source_root / delete_media_by_source_root). new_path, by
    contrast, is known to exist (checked by the caller), so it's safe to
    resolve.

    Also rewrites the matching entry in media.json so future syncs look in
    the new place. Returns {updated, still_missing, old_root, new_root}.
    """
    old_root = old_path.rstrip("/\\")
    new_root = str(Path(new_path.rstrip("/\\")).resolve())

    conn = get_db_conn()
    rows = conn.execute(
        "SELECT uniqueName, file_path, name, metadata_json, source_root FROM media "
        "WHERE source_root = ? OR source_root LIKE ?",
        (old_root, old_root + "/%"),
    ).fetchall()

    updated       = 0
    still_missing = 0
    matched_roots = set()   # the actual source_root value(s) of rows we relinked — see note below
    with _db_lock:
        with conn:
            for r in rows:
                row_root      = r["source_root"] or old_root
                old_file_path = r["file_path"] or ""
                if old_file_path == row_root:
                    new_file_path = new_root
                elif old_file_path.startswith(row_root):
                    new_file_path = new_root + old_file_path[len(row_root):]
                else:
                    new_file_path = old_file_path  # shouldn't happen — leave untouched

                candidate = os.path.join(new_file_path, r["name"] or "")
                if not os.path.isfile(candidate):
                    still_missing += 1
                    continue  # this specific file wasn't found at the new location

                try:
                    meta = json.loads(r["metadata_json"]) if r["metadata_json"] else {}
                except (TypeError, ValueError):
                    meta = {}
                file_meta = meta.setdefault("file", {})
                file_meta["source_root"] = new_root
                file_meta["path"] = new_file_path

                conn.execute(
                    "UPDATE media SET source_root = ?, file_path = ?, metadata_json = ? WHERE uniqueName = ?",
                    (new_root, new_file_path, json.dumps(meta, default=str), r["uniqueName"]),
                )
                updated += 1
                matched_roots.add(row_root)

    # Point media.json at the new location too, so the next sync (and the
    # Locations Manager) reflect it instead of flagging it missing again.
    # Only when at least one file was actually matched — if nothing matched,
    # the folder the user picked probably wasn't the right one, and leaving
    # the config path alone means the row stays red so they can try again
    # instead of the app silently pointing itself at the wrong place.
    if updated > 0:
        sources = load_json(MEDIA_JSON, [])

        # Match against old_root (what the caller sent) OR any source_root
        # value we actually just relinked rows from. The two are usually
        # the same string, but matching only against old_root risks a
        # silent no-op write if it doesn't line up exactly with a config
        # entry's stored path (e.g. a LIKE-prefix match found a deeper
        # source_root than old_root itself, or the entry was already
        # touched by an earlier attempt) — in that case rows would get
        # relinked in the database while media.json quietly stayed
        # unchanged, which is exactly the bug this guards against.
        candidates = {old_root} | matched_roots
        touched = False
        for s in sources:
            if (s.get("path") or "").rstrip("/\\") in candidates:
                s["path"] = new_path
                touched = True

        if not touched:
            # Path text didn't line up with any configured entry at all.
            # Fall back to whichever entry is currently pointing at a
            # directory that doesn't exist (and isn't already new_path) —
            # if there's exactly one, that's unambiguously the one being
            # fixed, so update it rather than leaving media.json stale.
            broken = [
                s for s in sources
                if (s.get("path") or "").rstrip("/\\") != new_root
                and not os.path.isdir(s.get("path") or "")
            ]
            if len(broken) == 1:
                broken[0]["path"] = new_path
                touched = True
                log.info("Relocate: matched config entry by elimination (single broken location)")

        if touched:
            save_json(MEDIA_JSON, sources)
        else:
            log.warning(
                "Relocate: %d record(s) relinked in the database, but no media.json "
                "entry could be matched to update — config may need manual editing.",
                updated,
            )

    return {"updated": updated, "still_missing": still_missing, "old_root": old_root, "new_root": new_root}


def relocate_media_subfolder(old_dir: str, new_dir: str) -> dict:
    """
    Like relocate_media_source, but scoped to a single subfolder inside a
    location rather than the whole configured location — used by the
    Locations Manager's "Resolve" flow.

    Why this exists as a separate function: a location like "/a/b" is
    scanned recursively, so every file under it (including "/a/b/c/...")
    shares the SAME source_root ("/a/b") — only file_path differs per
    subfolder. If the user later renames just "/a/b/c" to "/a/b/c-1", the
    location's own path ("/a/b") is still perfectly valid, so it never
    shows up red in the Locations Manager — only the individual files
    under the renamed subfolder start 404ing. relocate_media_source can't
    fix that: it matches rows by source_root, and source_root here never
    changed. This matches by file_path instead, so it can repoint just the
    affected subfolder's rows. media.json is never touched — the location's
    configured path is still correct, only rows are relinked.
    """
    old_root = old_dir.rstrip("/\\")
    new_root = str(Path(new_dir.rstrip("/\\")).resolve())

    conn = get_db_conn()
    rows = conn.execute(
        "SELECT uniqueName, file_path, name, metadata_json FROM media "
        "WHERE file_path = ? OR file_path LIKE ?",
        (old_root, old_root + "/%"),
    ).fetchall()

    updated       = 0
    still_missing = 0
    with _db_lock:
        with conn:
            for r in rows:
                old_file_path = r["file_path"] or ""
                if old_file_path == old_root:
                    new_file_path = new_root
                elif old_file_path.startswith(old_root):
                    new_file_path = new_root + old_file_path[len(old_root):]
                else:
                    new_file_path = old_file_path  # shouldn't happen — leave untouched

                candidate = os.path.join(new_file_path, r["name"] or "")
                if not os.path.isfile(candidate):
                    still_missing += 1
                    continue

                try:
                    meta = json.loads(r["metadata_json"]) if r["metadata_json"] else {}
                except (TypeError, ValueError):
                    meta = {}
                file_meta = meta.setdefault("file", {})
                file_meta["path"] = new_file_path

                conn.execute(
                    "UPDATE media SET file_path = ?, metadata_json = ? WHERE uniqueName = ?",
                    (new_file_path, json.dumps(meta, default=str), r["uniqueName"]),
                )
                updated += 1

    return {"updated": updated, "still_missing": still_missing, "old_path": old_root, "new_path": new_root}


def _find_missing_subfolder_groups(source_root: str) -> list:
    """
    Finds indexed-but-gone subfolders under a location and groups them by
    their topmost missing ancestor directory.

    Why grouping matters: /a/b/c1/d1 and /a/b/c1/d2 are two separate
    indexed leaf directories, but if the user renames "c1" to "c3", BOTH
    go missing together, and both come back the same way — by relocating
    "c1" itself, not d1 and d2 individually. relocate_media_subfolder()
    already matches rows by file_path *prefix*, so pointing it at the
    shared ancestor ("/a/b/c1" -> "/a/b/c3") relinks every leaf underneath
    it in one pass — d2 lands correctly for free as long as
    "/a/b/c3/d2" genuinely exists, since each row is still individually
    verified with os.path.isfile() before being relinked. Grouping here is
    what lets the Resolve popup surface that as ONE actionable item instead
    of one per affected leaf subfolder.

    Returns [{path, rel, count, leaf_count}] — one dict per missing
    ancestor. count is total indexed files affected, leaf_count is how many
    originally-separate indexed leaf directories that ancestor covers.
    """
    conn = get_db_conn()
    rows = conn.execute(
        "SELECT file_path, COUNT(*) as cnt FROM media "
        "WHERE (source_root = ? OR source_root LIKE ?) AND file_path != '' "
        "GROUP BY file_path",
        (source_root, source_root + "/%"),
    ).fetchall()

    root = Path(source_root)
    groups = {}   # ancestor path -> {"count": int, "leaves": int}
    for r in rows:
        leaf = (r["file_path"] or "").rstrip("/\\")
        if not leaf or os.path.isdir(leaf):
            continue   # still on disk — not missing

        # Climb from the location's root down toward the leaf and stop at
        # the first directory that doesn't exist — that's the highest
        # point a single relocate can fix in one shot.
        try:
            rel_parts = Path(leaf).relative_to(root).parts
        except ValueError:
            ancestor = leaf
        else:
            cur = root
            ancestor = leaf
            for part in rel_parts:
                cur = cur / part
                if not cur.is_dir():
                    ancestor = str(cur)
                    break

        g = groups.setdefault(ancestor, {"count": 0, "leaves": 0})
        g["count"]  += r["cnt"]
        g["leaves"] += 1

    result = []
    for ancestor, info in groups.items():
        rel = ancestor[len(source_root):].lstrip("/\\") if ancestor.startswith(source_root) else ancestor
        result.append({
            "path":       ancestor,
            "rel":        rel or ".",
            "count":      info["count"],
            "leaf_count": info["leaves"],
        })
    result.sort(key=lambda d: d["rel"])
    return result


def count_missing_subfolders(configured_path: str) -> int:
    """
    Cheap existence-only check for the "Resolve" button in the Locations
    Manager: how many *actionable* missing-subfolder groups does this
    location have right now? Grouped the same way
    _find_missing_subfolder_groups does, so this count always matches how
    many rows the Resolve popup will actually show — a rename higher up
    the tree that took several indexed subfolders with it still counts as
    one. Never walks the directory tree (no "new folders" check) — it's
    just a DB query plus an os.path.isdir() per already-indexed directory —
    so it's cheap enough to run for every location every time the
    Locations Manager is opened.
    """
    root = (configured_path or "").rstrip("/\\")
    if not root or not os.path.isdir(root):
        return 0
    source_root = str(Path(root).resolve())
    return len(_find_missing_subfolder_groups(source_root))


def diagnose_location_structure(configured_path: str) -> dict:
    """
    Compare what SQLite has indexed for a location against what's actually
    on disk right now, one directory at a time, to catch subfolder-level
    drift that the plain "does the location's own path still exist?" check
    can't see. This is exactly the "/a/b/c renamed to /a/b/c-1" case: "/a/b"
    (the configured location) is still there, so the location never shows
    red — but "/a/b/c"'s rows are now orphaned, and "/a/b/c-1" sits on disk
    completely unindexed. Read-only — makes no database or media.json changes.

    Returns:
      {
        root:    <resolved source_root>,
        missing: [{path, rel, count, leaf_count}],  # missing-ancestor groups — see _find_missing_subfolder_groups
        new:     [{path, rel, count}],               # subfolders on disk with media, not indexed at all
      }
    """
    root = (configured_path or "").rstrip("/\\")
    if not root or not os.path.isdir(root):
        return {"root": root, "missing": [], "new": []}
    source_root = str(Path(root).resolve())

    cfg = load_config()
    supported = (
        {f.lower() for f in cfg.get("supported_image_formats", [])} |
        {f.lower() for f in cfg.get("supported_video_formats", [])}
    )
    follow_links = cfg.get("follow_symlinks", True)
    skip_hidden  = cfg.get("skip_hidden_dirs", True)
    max_depth    = int(cfg.get("max_scan_depth", 0))
    source_depth = source_root.rstrip("/").count("/")

    missing = _find_missing_subfolder_groups(source_root)

    # Need the raw leaf directory set (not the grouped one) to know which
    # on-disk directories already correspond to something indexed, when
    # walking below for the "new" side.
    conn = get_db_conn()
    rows = conn.execute(
        "SELECT DISTINCT file_path FROM media "
        "WHERE (source_root = ? OR source_root LIKE ?) AND file_path != ''",
        (source_root, source_root + "/%"),
    ).fetchall()
    indexed_dirs = {(r["file_path"] or "").rstrip("/\\") for r in rows}

    # Walk the real tree looking for directories that hold qualifying media
    # but aren't represented in the index under this location at all —
    # either a genuinely new folder, or the new name a renamed one landed
    # under.
    new_dirs = []
    for cur_root, dirs, files in os.walk(root, followlinks=follow_links):
        if skip_hidden:
            dirs[:] = [d for d in dirs if not d.startswith(".")]
        if max_depth > 0:
            cur = str(Path(cur_root).resolve()).rstrip("/").count("/") - source_depth
            if cur >= max_depth:
                dirs.clear()
        resolved_cur = str(Path(cur_root).resolve()).rstrip("/\\")
        qualifying = [f for f in files if Path(f).suffix.lstrip(".").lower() in supported]
        if not qualifying or resolved_cur in indexed_dirs:
            continue
        rel = resolved_cur[len(source_root):].lstrip("/\\") if resolved_cur.startswith(source_root) else resolved_cur
        new_dirs.append({"path": resolved_cur, "rel": rel or ".", "count": len(qualifying)})

    missing.sort(key=lambda d: d["rel"])
    new_dirs.sort(key=lambda d: d["rel"])
    return {"root": source_root, "missing": missing, "new": new_dirs}


def rescan_subfolder(source_root: str, walk_dir: str) -> dict:
    """
    Index new media found under one subfolder of an already-configured
    location, without re-walking the entire location the way a full Sync
    would. Used by the Locations Manager's "Resolve" flow for a subfolder
    diagnose_location_structure() found on disk but not in the index at
    all (a genuinely new folder, or the new name a renamed one landed
    under). Deliberately kept as its own self-contained pass — mirroring
    the relevant slice of sync_library's inner loop — rather than folding
    this into sync_library itself, since that function is the hot path for
    the full multi-location Sync and isn't worth risking for a scoped,
    occasional operation like this.

    source_root is the location's own resolved root (kept as-is, exactly
    like a normal sync would set it) so relative_path/subfolder on any new
    rows are computed against the location, not against walk_dir — new
    rows behave identically to ones a full sync would have produced.
    """
    cfg = load_config()
    supported = (
        {f.lower() for f in cfg.get("supported_image_formats", [])} |
        {f.lower() for f in cfg.get("supported_video_formats", [])}
    )
    follow_links = cfg.get("follow_symlinks", True)
    skip_hidden  = cfg.get("skip_hidden_dirs", True)
    dedup_method = cfg.get("dedup_method", "both")

    existing_hashes, existing_paths = get_existing_hashes_and_paths()

    new_entries = []
    scanned = 0
    added   = 0

    for root, dirs, files in os.walk(walk_dir, followlinks=follow_links):
        if skip_hidden:
            dirs[:] = [d for d in dirs if not d.startswith(".")]
        for filename in sorted(files):
            ext = Path(filename).suffix.lstrip(".").lower()
            if ext not in supported:
                continue

            full_path     = os.path.join(root, filename)
            resolved_path = str(Path(full_path).resolve())
            scanned += 1

            if dedup_method in ("path", "both") and resolved_path in existing_paths:
                continue
            if dedup_method in ("hash", "both"):
                fhash = file_hash(full_path)
                if fhash in existing_hashes:
                    continue
            else:
                fhash = file_hash(full_path)

            media_type = "video" if is_video(full_path) else "image"
            try:
                meta = extract_metadata(full_path)
            except Exception as e:
                log.warning("Metadata failed for %s: %s", full_path, e)
                meta = {"file": {}, "date": {}}

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

    if new_entries:
        upsert_media_rows(new_entries)

    log.info("Rescanned subfolder %s (location %s) — scanned %d, added %d",
              walk_dir, source_root, scanned, added)
    return {"scanned": scanned, "added": added}


def get_existing_hashes_and_paths() -> tuple:
    """
    Return (set of hashes, set of resolved full paths) for dedup checks.
    Holds _db_lock for a consistent snapshot — sync reads this once at start.
    """
    conn = get_db_conn()
    with _db_lock:
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
    Core filtered/sorted/paginated query — done entirely in SQLite with indexes.
    Supports an optional 'album' filter which JOINs album_media so only members
    of that album are returned, in album membership order (position) by default.
    Returns (items: list[dict], total: int).
    """
    conn = get_db_conn()

    album_id = (filters.get("album") or "").strip()

    where = []
    args  = []

    hidden = (filters.get("hidden") or "").strip().lower()
    if hidden == "true":
        where.append("m.isHidden = 1")
    elif hidden != "include":
        where.append("m.isHidden = 0")

    fmt = (filters.get("format") or "").strip().upper()
    if fmt:
        where.append("m.format = ?")
        args.append(fmt)

    cam = (filters.get("camera") or "").strip()
    if cam:
        where.append("m.camera_label = ?")
        args.append(cam)

    loc = (filters.get("location") or "").strip().rstrip("/\\")
    if loc:
        where.append("(m.source_root = ? OR m.source_root LIKE ? OR m.file_path = ? OR m.file_path LIKE ?)")
        args.extend([loc, loc + "/%", loc, loc + "/%"])

    q = (filters.get("q") or "").strip().lower()
    if q:
        where.append("(LOWER(m.name) LIKE ? OR LOWER(m.camera_label) LIKE ? OR m.date_created LIKE ?)")
        like = f"%{q}%"
        args.extend([like, like, like])

    # Date range — dateFrom/dateTo are plain "YYYY-MM-DD" strings from the
    # frontend's <input type="date">. m.date_sort is stored as a full ISO
    # timestamp (see _row_to_media_dict), so a "YYYY-MM-DD" bound compares
    # correctly against it lexicographically (it's a shorter, equal-prefix
    # string) without needing to parse either side. dateTo gets a trailing
    # end-of-day timestamp appended so the whole selected end day is included
    # rather than only its midnight instant. Items with no known date
    # (date_sort == "") are excluded once either bound is set, since they
    # can't be meaningfully placed inside a date range.
    date_from = (filters.get("dateFrom") or "").strip()
    date_to   = (filters.get("dateTo") or "").strip()
    if date_from or date_to:
        where.append("m.date_sort != ''")
    if date_from:
        where.append("m.date_sort >= ?")
        args.append(date_from)
    if date_to:
        where.append("m.date_sort <= ?")
        args.append(date_to + "T23:59:59.999999")

    if album_id:
        # JOIN restricts results to album members only
        from_sql  = "FROM media m INNER JOIN album_media am ON m.uniqueName = am.uniqueName AND am.album_id = ?"
        join_args = [album_id]
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        if sort == "date-asc":
            order_sql = "ORDER BY m.date_sort ASC"
        elif sort == "name":
            order_sql = "ORDER BY m.name COLLATE NOCASE ASC"
        else:
            order_sql = "ORDER BY am.position ASC, m.date_sort DESC"
        count_sql = f"SELECT COUNT(*) {from_sql} {where_sql}"
        rows_sql  = f"SELECT m.* {from_sql} {where_sql} {order_sql} LIMIT ? OFFSET ?"
        all_args  = join_args + args
    else:
        from_sql  = "FROM media m"
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        if sort == "date-asc":
            order_sql = "ORDER BY m.date_sort ASC"
        elif sort == "name":
            order_sql = "ORDER BY m.name COLLATE NOCASE ASC"
        else:
            order_sql = "ORDER BY m.date_sort DESC"
        count_sql = f"SELECT COUNT(*) {from_sql} {where_sql}"
        rows_sql  = f"SELECT m.* {from_sql} {where_sql} {order_sql} LIMIT ? OFFSET ?"
        all_args  = args

    with _db_lock:
        total = conn.execute(count_sql, all_args).fetchone()[0]
        rows  = conn.execute(rows_sql, all_args + [limit, offset]).fetchall()

    return [_row_to_media_dict(r) for r in rows], total


def query_media_groups(filters, group_by, year=None):
    """
    Bucket the media table by year or month, entirely in SQL — used by the
    gallery's Years/Months grouping view. Never loads full result sets into
    Python just to bucket them: one aggregate query gets the counts, then one
    small LIMIT-4 query per bucket gets the "most recent" preview thumbnails
    for that stack card. This keeps the response tiny even for a library of
    tens of thousands of items — only up to (buckets * 4) media rows are ever
    read, never "all photos in the bucket".

    filters accepts the same format/camera/location/q/hidden params as
    query_media() (applied identically before bucketing). dateFrom/dateTo are
    intentionally NOT applied here — grouping IS the date navigation.

    group_by: 'year' -> buckets keyed "YYYY"
              'month' -> buckets keyed "YYYY-MM", optionally restricted to a
                         single `year` ("YYYY") so the frontend can show just
                         that year's months after a year card is clicked.

    Items with no known date (date_sort == '') can't be meaningfully placed
    in a year/month bucket, so — like the dateFrom/dateTo range filter in
    query_media() — they're excluded here. They still appear in the "All"
    (ungrouped) view.

    Returns: [ { "key": "YYYY" | "YYYY-MM", "count": int, "preview": [media, ...] }, ... ]
    ordered most-recent bucket first.
    """
    conn = get_db_conn()

    where = ["m.date_sort != ''"]
    args  = []

    hidden = (filters.get("hidden") or "").strip().lower()
    if hidden == "true":
        where.append("m.isHidden = 1")
    elif hidden != "include":
        where.append("m.isHidden = 0")

    fmt = (filters.get("format") or "").strip().upper()
    if fmt:
        where.append("m.format = ?")
        args.append(fmt)

    cam = (filters.get("camera") or "").strip()
    if cam:
        where.append("m.camera_label = ?")
        args.append(cam)

    loc = (filters.get("location") or "").strip().rstrip("/\\")
    if loc:
        where.append("(m.source_root = ? OR m.source_root LIKE ? OR m.file_path = ? OR m.file_path LIKE ?)")
        args.extend([loc, loc + "/%", loc, loc + "/%"])

    q = (filters.get("q") or "").strip().lower()
    if q:
        where.append("(LOWER(m.name) LIKE ? OR LOWER(m.camera_label) LIKE ? OR m.date_created LIKE ?)")
        like = f"%{q}%"
        args.extend([like, like, like])

    bucket_expr = "substr(m.date_sort,1,4)" if group_by == "year" else "substr(m.date_sort,1,7)"

    if group_by == "month" and year:
        where.append("substr(m.date_sort,1,4) = ?")
        args.append(year)

    where_sql = "WHERE " + " AND ".join(where)

    count_sql = f"""
        SELECT {bucket_expr} AS bucket, COUNT(*) AS cnt
        FROM media m
        {where_sql}
        GROUP BY bucket
        ORDER BY bucket DESC
    """

    groups = []
    with _db_lock:
        buckets = conn.execute(count_sql, args).fetchall()
        for b in buckets:
            bucket_key = b["bucket"]
            preview_sql = f"""
                SELECT m.* FROM media m
                {where_sql} AND {bucket_expr} = ?
                ORDER BY m.date_sort DESC
                LIMIT 4
            """
            preview_rows = conn.execute(preview_sql, args + [bucket_key]).fetchall()
            groups.append({
                "key":     bucket_key,
                "count":   b["cnt"],
                "preview": [_row_to_media_dict(r) for r in preview_rows],
            })

    return groups

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

def count_media_by_source_root(path: str) -> int:
    """
    How many media rows currently belong to a given location root — i.e.
    how many indexed files would be discarded if this location were removed.
    Matches the same (exact OR prefix) rule used everywhere else a location
    path is compared against source_root, so counts here always agree with
    what the location filter dropdown would show.
    """
    root = (path or "").rstrip("/\\")
    if not root:
        return 0
    conn = get_db_conn()
    row = conn.execute(
        "SELECT COUNT(*) FROM media WHERE source_root = ? OR source_root LIKE ?",
        (root, root + "/%"),
    ).fetchone()
    return row[0] if row else 0

def delete_media_by_source_root(path: str) -> int:
    """
    Permanently remove every media row (and cached thumbnails) belonging to
    a location that's being removed from the scan list — keeps the DB from
    holding onto records for files we've explicitly told Luminary to stop
    tracking. The original files on disk are never touched; this only
    discards Luminary's own index of them. Returns the number of rows removed.
    """
    root = (path or "").rstrip("/\\")
    if not root:
        return 0
    conn = get_db_conn()
    rows = conn.execute(
        "SELECT uniqueName FROM media WHERE source_root = ? OR source_root LIKE ?",
        (root, root + "/%"),
    ).fetchall()
    unique_names = [r["uniqueName"] for r in rows]
    return delete_media_records(unique_names)

def get_distinct_subdirs() -> list:
    """
    Return all unique directory paths including source roots and every
    subdirectory that contains at least one indexed file.
    Returns [{path, source_root, label, depth, is_root}] sorted by path.
    - depth 0 entries are source roots (path == source_root)
    - depth > 0 entries are subdirectories (path == file_path stripped of trailing slash)
    """
    conn = get_db_conn()

    # Get all unique (source_root, file_path) pairs
    rows = conn.execute(
        """SELECT DISTINCT source_root, file_path
           FROM media
           WHERE file_path != ''
           ORDER BY source_root, file_path"""
    ).fetchall()

    seen_paths  = set()
    seen_roots  = set()
    result      = []

    for r in rows:
        src  = (r["source_root"] or "").rstrip("/\\")
        path = (r["file_path"]   or "").rstrip("/\\")

        # Always emit the source root once as a depth-0 selectable entry
        if src and src not in seen_roots:
            seen_roots.add(src)
            seen_paths.add(src)
            result.append({
                "path":        src,
                "source_root": src,
                "label":       os.path.basename(src) or src,
                "depth":       0,
                "is_root":     True,
            })

        # Emit the subdirectory (file_path) if it differs from source root
        if path and path not in seen_paths and path != src:
            seen_paths.add(path)
            if src and path.startswith(src):
                rel = path[len(src):].lstrip("/\\")
            else:
                rel = os.path.basename(path) or path
            depth = path.replace("\\", "/").count("/") - src.replace("\\", "/").count("/")
            result.append({
                "path":        path,
                "source_root": src,
                "label":       rel if rel else os.path.basename(path),
                "depth":       max(1, depth),
                "is_root":     False,
            })

    return sorted(result, key=lambda x: x["path"].lower())

def get_media_count() -> int:
    conn = get_db_conn()
    return conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]

def get_media_stats() -> dict:
    """
    Aggregate counts used by the tray dashboard's Media tab (see dashbord.py):
    total items, a breakdown by type (image/video), and how many are
    currently hidden. Three cheap COUNT/GROUP BY queries — no full scan of
    metadata_json, same spirit as get_media_count()/get_distinct_formats().
    """
    conn = get_db_conn()
    total = conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]
    by_type = {
        row["type"]: row["c"]
        for row in conn.execute("SELECT type, COUNT(*) AS c FROM media GROUP BY type").fetchall()
    }
    hidden = conn.execute("SELECT COUNT(*) FROM media WHERE isHidden = 1").fetchone()[0]
    return {
        "total":  total,
        "images": by_type.get("image", 0),
        "videos": by_type.get("video", 0),
        "hidden": hidden,
    }


# ── Albums ──────────────────────────────────────────────────────────────────

def load_albums() -> list:
    """Return all albums as list[dict] — same shape as the old albums.json array,
    plus a 'folder_id' field (None when the album isn't inside a folder)."""
    conn  = get_db_conn()
    rows  = conn.execute("SELECT id, name, folder_id FROM albums").fetchall()
    out   = []
    for a in rows:
        media_rows = conn.execute(
            "SELECT uniqueName FROM album_media WHERE album_id = ? ORDER BY position",
            (a["id"],)
        ).fetchall()
        out.append({
            "id":        a["id"],
            "name":      a["name"],
            "folder_id": a["folder_id"],
            "media":     [m["uniqueName"] for m in media_rows],
        })
    return out

def save_albums(albums: list):
    """Replace all albums + their membership atomically under _db_lock.
    Preserves 'folder_id' on each album dict (None/absent = no folder)."""
    conn = get_db_conn()
    with _db_lock:
        with conn:
            conn.execute("DELETE FROM album_media")
            conn.execute("DELETE FROM albums")
            for a in albums:
                conn.execute(
                    "INSERT INTO albums (id, name, folder_id) VALUES (?, ?, ?)",
                    (a.get("id"), a.get("name", "Untitled"), a.get("folder_id"))
                )
                for pos, un in enumerate(a.get("media", [])):
                    conn.execute(
                        "INSERT OR IGNORE INTO album_media (album_id, uniqueName, position) VALUES (?, ?, ?)",
                        (a.get("id"), un, pos)
                    )
    log.info("Saved %d albums to SQLite", len(albums))

def create_album(name: str, folder_id: str = None) -> dict:
    conn  = get_db_conn()
    album = {"name": name, "id": "album_" + str(uuid.uuid4())[:8], "media": [], "folder_id": folder_id}
    with conn:
        conn.execute(
            "INSERT INTO albums (id, name, folder_id) VALUES (?, ?, ?)",
            (album["id"], album["name"], folder_id)
        )
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

def move_album_to_folder(album_id: str, folder_id: str = None):
    """
    Move an album into a folder, or out of any folder if folder_id is None.
    Returns 'ok', 'album_not_found', or 'folder_not_found'.
    """
    conn = get_db_conn()
    if not conn.execute("SELECT 1 FROM albums WHERE id = ?", (album_id,)).fetchone():
        return "album_not_found"
    if folder_id and not conn.execute("SELECT 1 FROM folders WHERE id = ?", (folder_id,)).fetchone():
        return "folder_not_found"
    with _db_lock:
        with conn:
            conn.execute("UPDATE albums SET folder_id = ? WHERE id = ?", (folder_id, album_id))
    return "ok"


# ── Folders ─────────────────────────────────────────────────────────────────
# A folder is a simple named container that groups albums for display in the
# sidebar (collapsible tree). An album can belong to at most one folder;
# a folder can hold any number of albums. Deleting a folder cascades (via the
# albums.folder_id FK) to delete the albums inside it and their album_media
# membership rows — the media table itself is never touched, so the actual
# files/records always survive a folder deletion.

def load_folders() -> list:
    """Return all folders as list[dict] {id, name}, ordered by name."""
    conn = get_db_conn()
    rows = conn.execute("SELECT id, name FROM folders ORDER BY name COLLATE NOCASE").fetchall()
    return [{"id": r["id"], "name": r["name"]} for r in rows]

def create_folder(name: str) -> dict:
    conn   = get_db_conn()
    folder = {"id": "folder_" + str(uuid.uuid4())[:8], "name": name}
    with conn:
        conn.execute("INSERT INTO folders (id, name) VALUES (?, ?)", (folder["id"], folder["name"]))
    return folder

def rename_folder(folder_id: str, name: str) -> bool:
    conn = get_db_conn()
    if not conn.execute("SELECT 1 FROM folders WHERE id = ?", (folder_id,)).fetchone():
        return False
    with conn:
        conn.execute("UPDATE folders SET name = ? WHERE id = ?", (name, folder_id))
    return True

def delete_folder(folder_id: str, force: bool = False) -> dict:
    """
    Delete a folder. If it still contains albums and force isn't set, don't
    delete anything — instead return the info the frontend needs to show a
    confirmation ("these N albums will also be deleted, media files won't be").
    Pass force=True to proceed anyway (albums + their album_media rows cascade
    automatically; the media table is never touched).
    """
    conn = get_db_conn()
    folder = conn.execute("SELECT id, name FROM folders WHERE id = ?", (folder_id,)).fetchone()
    if not folder:
        return {"ok": False, "error": "Folder not found"}

    albums_inside = conn.execute(
        "SELECT id, name FROM albums WHERE folder_id = ?", (folder_id,)
    ).fetchall()

    if albums_inside and not force:
        return {
            "ok": False,
            "needs_confirmation": True,
            "album_count": len(albums_inside),
            "album_names": [a["name"] for a in albums_inside],
        }

    with _db_lock:
        with conn:
            conn.execute("DELETE FROM folders WHERE id = ?", (folder_id,))  # cascades to albums → album_media

    return {"ok": True, "deleted_albums": len(albums_inside)}

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
#  SYNC STATE  —  shared between the sync background thread and the API
# ══════════════════════════════════════════════════════════════════════════════

_sync_lock  = threading.Lock()
_sync_state = {
    "running":        False,
    "scanned":        0,
    "added":          0,
    "removed":        0,
    "total_at_start": 0,
    "current_file":   "",
    "current_source": "",
    "log":            deque(maxlen=200),  # last 200 log lines
    "done":           False,
    "result":         None,    # {added, scanned, total} on completion
    "error":          None,
}

def _sync_update(**kwargs):
    """Thread-safe update of sync state fields."""
    with _sync_lock:
        for k, v in kwargs.items():
            if k == "log":
                _sync_state["log"].append(v)
            else:
                _sync_state[k] = v

def _sync_snapshot() -> dict:
    """Return a JSON-serialisable snapshot of the current sync state."""
    with _sync_lock:
        return {
            "running":        _sync_state["running"],
            "scanned":        _sync_state["scanned"],
            "added":          _sync_state["added"],
            "removed":        _sync_state["removed"],
            "total_at_start": _sync_state["total_at_start"],
            "current_file":   _sync_state["current_file"],
            "current_source": _sync_state["current_source"],
            "log":            list(_sync_state["log"]),
            "done":           _sync_state["done"],
            "result":         _sync_state["result"],
            "error":          _sync_state["error"],
        }


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
        r = subprocess.run(cmd, capture_output=True, timeout=15, **_SUBPROCESS_NO_WINDOW_KWARGS)
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
        import tempfile
        for cmd_fn in [
            lambda s, d: ["magick", s, d],
            lambda s, d: ["convert", s, d],
            lambda s, d: ["ffmpeg", "-y", "-i", s, d],
        ]:
            try:
                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False, dir=str(app_paths.CACHE_DIR)) as tmp:
                    tmp_path = tmp.name
                r = subprocess.run(cmd_fn(filepath, tmp_path),
                                   capture_output=True, timeout=30, **_SUBPROCESS_NO_WINDOW_KWARGS)
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
        "style":                    "classic",    # classic | modern
        "font_size":                "small",      # small | medium | large
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


# ══════════════════════════════════════════════════════════════════════════════
#  SYNC STATE  — thread-safe progress tracking for background sync
# ══════════════════════════════════════════════════════════════════════════════



def sync_library(progress=None) -> dict:
    """
    Scan configured directories and incrementally index new media into SQLite.
    progress: optional callable(event, **kwargs) for real-time status updates.
    """
    def emit(event, **kw):
        if progress:
            progress(event, **kw)

    cfg     = load_config()
    sources = load_json(MEDIA_JSON, [])

    supported = (
        {f.lower() for f in cfg.get("supported_image_formats", [])} |
        {f.lower() for f in cfg.get("supported_video_formats", [])}
    )

    # ── Phase 0: garbage-collect records left behind under a source_root
    # that's no longer configured at all (e.g. a relocate that couldn't
    # confirm every file at the new location — see
    # _remove_orphaned_source_records() for the full scenario). Deliberately
    # runs BEFORE the missing-file check and the scan/add phase below, so a
    # plain Sync click also clears out exactly this kind of leftover.
    removed = _remove_orphaned_source_records(sources, emit=emit)
    if removed:
        emit("progress", removed=removed)

    # ── Phase 1: figure out which sources are reachable right now, and check
    # for any DB records under them whose file is gone — BEFORE computing the
    # dedup hash/path snapshot below. This ordering matters: on a rename,
    # the new filename hashes identically to the old (now-stale) row. If we
    # checked *after* scanning, that stale row would still be in the dedup
    # snapshot, the renamed file would look like a duplicate and get
    # skipped, and the add would only happen on the *next* sync. Checking
    # first means the rename is detected as new content in this same run.
    reachable_roots = []
    for source in sources:
        if not source.get("visibility", True):
            continue
        dir_path = source.get("path", "").rstrip("/\\")
        if os.path.isdir(dir_path):
            reachable_roots.append(str(Path(dir_path).resolve()))
            # Directory is back — clear any stale "can't find this location"
            # notification from a previous run.
            resolve_location_notifications(dir_path)

    missing = _check_missing_media(reachable_roots, emit=emit) if reachable_roots else 0

    existing_hashes, existing_paths = get_existing_hashes_and_paths()

    added       = 0
    scanned     = 0
    new_entries = []

    for source in sources:
        if not source.get("visibility", True):
            emit("log", msg=f"Skipped (hidden): {source.get('name', source.get('path'))}")
            log.info("Skipping hidden source: %s", source.get("name"))
            continue

        dir_path = source.get("path", "").rstrip("/\\")
        src_name = source.get("name", dir_path)

        if not os.path.isdir(dir_path):
            emit("log", msg=f"Not found: {dir_path}")
            log.warning("Directory not found: %s", dir_path)
            create_notification(
                "location_missing",
                title=f"Location unreachable: {src_name}",
                message=(
                    f"Luminary can't find \"{dir_path}\". If you moved or renamed "
                    f"this folder, relocate it to keep your synced photos linked."
                ),
                location_path=dir_path,
                action="relocate",
                action_label="Relocate",
            )
            continue

        source_root  = str(Path(dir_path).resolve())
        follow_links = cfg.get("follow_symlinks", True)
        skip_hidden  = cfg.get("skip_hidden_dirs", True)
        max_depth    = int(cfg.get("max_scan_depth", 0))
        dedup_method = cfg.get("dedup_method", "both")
        source_depth = source_root.rstrip("/").count("/")

        emit("source", source=src_name, msg=f"Scanning: {src_name}")
        log.info("Scanning: %s → %s", src_name, source_root)

        for root, dirs, files in os.walk(dir_path, followlinks=follow_links):
            if skip_hidden:
                dirs[:] = [d for d in dirs if not d.startswith(".")]
            if max_depth > 0:
                cur = str(Path(root).resolve()).rstrip("/").count("/") - source_depth
                if cur >= max_depth:
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
                        continue

                if dedup_method in ("hash", "both"):
                    fhash = file_hash(full_path)
                    if fhash in existing_hashes:
                        continue
                else:
                    fhash = file_hash(full_path)

                media_type = "video" if is_video(full_path) else "image"
                try:
                    meta = extract_metadata(full_path)
                except Exception as e:
                    log.warning("Metadata failed for %s: %s", full_path, e)
                    meta = {"file": {}, "date": {}}

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
                emit("progress", scanned=scanned, added=added, file=filename,
                     msg=f"[{media_type}] {meta['file'].get('relative_path', filename)}")
                log.info("Indexed [%s]: %s", media_type, meta["file"].get("relative_path", filename))

                if len(new_entries) >= 200:
                    upsert_media_rows(new_entries)
                    new_entries = []
                    emit("progress", scanned=scanned, added=added)

        emit("log", msg=f"Done: {src_name}")

    if new_entries:
        upsert_media_rows(new_entries)

    total = get_media_count()
    log.info("Sync complete — scanned %d, added %d, removed %d, missing %d, total %d",
              scanned, added, removed, missing, total)
    emit("log", msg=f"Sync complete — {added} new, {removed} removed, {missing} missing, {total} total")
    return {"added": added, "scanned": scanned, "removed": removed, "missing": missing, "total": total}


# ══════════════════════════════════════════════════════════════════════════════
#  FLASK API
# ══════════════════════════════════════════════════════════════════════════════

if FLASK_AVAILABLE:
    frontend_dir = app_paths.RESOURCES_DIR
    app = Flask(__name__, static_folder=str(frontend_dir), static_url_path="")
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
        """Save updated configuration to config/configuration.json."""
        data = request.get_json(force=True)
        if not isinstance(data, dict):
            return jsonify({"error": "Expected a JSON object"}), 400
        # Merge with existing — never wipe keys we don't know about
        cfg = load_config()
        cfg.update(data)
        save_json(CONFIG_JSON, cfg)
        log.info("configuration.json updated")
        return jsonify({"ok": True})

    @app.route("/api/media/by-id/<unique_name>", methods=["GET"])
    def api_media_by_id(unique_name):
        """Return a single full media record by uniqueName. Used by the map panel."""
        item = get_media_by_unique_name(unique_name)
        if not item:
            return jsonify({"error": "Not found"}), 404
        return jsonify(item)

    @app.route("/api/media/count", methods=["GET"])
    def api_media_count():
        """Return total number of indexed media items — single indexed COUNT(*)."""
        return jsonify({"total": get_media_count()})

    @app.route("/api/media/stats", methods=["GET"])
    def api_media_stats():
        """
        Aggregate media counts for the tray dashboard's Media tab (see
        dashbord.py): total, per-type (image/video), and hidden count.
        """
        return jsonify(get_media_stats())

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

    @app.route("/api/media/subdirs", methods=["GET"])
    def api_media_subdirs():
        """
        Return all distinct directories (source roots + every subdirectory
        that contains at least one indexed file) as a tree-friendly list.
        Response: [{path, source_root, label, depth}] sorted by path.
        'depth' is 0 for source root, 1 for first-level subdir, etc.
        Used to populate the location/subfolder dropdown in the photo picker.
        """
        return jsonify(get_distinct_subdirs())

    @app.route("/api/media/gps", methods=["GET"])
    def api_media_gps():
        """
        Return a lightweight list of all non-hidden media that has GPS coordinates.
        Each item contains only what the map needs — no full metadata blob.
        Response: {
          items: [{uniqueName, name, type, lat, lng, date}],
          total: N,          -- total media in db (including those without GPS)
          gps_count: N       -- number that have GPS coords
        }
        """
        conn = get_db_conn()
        with _db_lock:
            total = conn.execute("SELECT COUNT(*) FROM media WHERE isHidden = 0").fetchone()[0]
            rows  = conn.execute(
                "SELECT uniqueName, name, type, date_sort, metadata_json FROM media WHERE isHidden = 0"
            ).fetchall()

        items = []
        for r in rows:
            try:
                meta = json.loads(r["metadata_json"]) if r["metadata_json"] else {}
                loc  = meta.get("location", {}) or {}
                lat  = loc.get("latitude")
                lng  = loc.get("longitude")
                if lat is None or lng is None:
                    continue
                lat = float(lat)
                lng = float(lng)
                if lat == 0.0 and lng == 0.0:
                    continue
                items.append({
                    "uniqueName": r["uniqueName"],
                    "name":       r["name"],
                    "type":       r["type"],
                    "lat":        lat,
                    "lng":        lng,
                    "date":       r["date_sort"] or "",
                })
            except (ValueError, TypeError, json.JSONDecodeError):
                continue

        return jsonify({
            "items":     items,
            "total":     total,
            "gps_count": len(items),
        })

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
          dateFrom (str,    optional)           — "YYYY-MM-DD", inclusive lower bound on date_sort
          dateTo   (str,    optional)           — "YYYY-MM-DD", inclusive upper bound on date_sort
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

    @app.route("/api/media/groups", methods=["GET"])
    def api_media_groups():
        """
        Bucket media into year or month stacks for the gallery's grouping view
        (All / Years / Months). SQL-backed via query_media_groups() — only
        counts plus up to 4 preview thumbnails per bucket are ever loaded, so
        this stays cheap no matter how large the library is.

        Query params:
          groupBy  (str, required) — 'year' | 'month'
          year     (str, optional) — with groupBy=month, restricts results to
                                      the months within this year ("YYYY").
                                      Omitted -> months across all years.
          Also accepts the same format/camera/location/q/hidden filters as
          /api/media, applied identically before bucketing.
        Response:
          { groups: [ { key, count, preview: [...] }, ... ], group_by }
        """
        group_by = (request.args.get("groupBy") or "").strip().lower()
        if group_by not in ("year", "month"):
            return jsonify({"error": "groupBy must be 'year' or 'month'"}), 400
        year = (request.args.get("year") or "").strip() or None
        groups = query_media_groups(request.args, group_by, year=year)
        return jsonify({"groups": groups, "group_by": group_by})

    # ── shared image helpers ──────────────────────────────────────────────────

    def _resolve_path(unique_name):
        """
        Return (full_path, item) or raise 404. Uses indexed SQLite primary key lookup.

        No longer self-healing by deletion: if the DB record exists but the
        underlying file can't be found (deleted/renamed/moved since the last
        sync), the record is kept — only a notification is raised (with a
        "Relocate" action pointing at the owning location) so the user can
        fix it from the bell icon instead of silently losing the index entry
        the moment they happen to browse to it.
        """
        from flask import abort
        item = get_media_by_unique_name(unique_name)
        if not item:
            abort(404)
        file_meta = item.get("metadata", {}).get("file", {})
        file_dir  = file_meta.get("path", "")
        full_path = os.path.join(file_dir, item["name"])
        if not os.path.isfile(full_path):
            source_root = file_meta.get("source_root", "")
            if source_root and not os.path.isdir(source_root):
                raw_path = _configured_path_for_source_root(source_root)
                log.warning("File not found and source root unreachable: %s", full_path)
                create_notification(
                    "location_missing",
                    title=f"Location unreachable: {os.path.basename(raw_path) or raw_path}",
                    message=(
                        f"Luminary can't find \"{raw_path}\". If you moved or renamed "
                        f"this folder, relocate it to keep your synced photos linked."
                    ),
                    location_path=raw_path,
                    action="relocate",
                    action_label="Relocate",
                )
            else:
                raw_path = _configured_path_for_source_root(source_root) if source_root else None
                log.warning("File not found on disk — keeping record, notifying: %s", full_path)
                create_notification(
                    "file_missing",
                    title=f"\"{item['name']}\" could not be found",
                    message=(
                        f"Luminary could not find this file on disk. "
                        f"It's still in your library — relocate the folder if it moved."
                    ),
                    location_path=raw_path,
                    unique_name=unique_name,
                    action="relocate" if raw_path else None,
                    action_label="View Location" if raw_path else None,
                )
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
        """
        Thumbnail cache dir is fixed at app_paths.THUMB_DIR — not user-configurable.
        Kept as a function (rather than referencing THUMB_DIR directly at call
        sites) so callers don't care whether the location is fixed or derived.
        """
        THUMB_DIR.mkdir(parents=True, exist_ok=True)
        return THUMB_DIR

    def _thumb_cache_path(unique_name, size, quality):
        return _get_thumb_cache_dir() / f"{unique_name}_{size}q{quality}.jpg"

    # ── cache/thumbnail size + clear ──────────────────────────────────────────
    def _dir_size_bytes(path: Path) -> int:
        """Recursively sum file sizes under path. Missing dir → 0."""
        if not path.is_dir():
            return 0
        total = 0
        for item in path.rglob("*"):
            if item.is_file():
                try:
                    total += item.stat().st_size
                except OSError:
                    pass  # file vanished mid-scan or unreadable — skip it
        return total

    def _clear_dir_contents(path: Path) -> int:
        """
        Delete every file/subdirectory inside path, leaving path itself in
        place (so the app doesn't have to recreate it before the next write).
        Best-effort — an item that fails to delete (e.g. currently in use)
        is skipped rather than aborting the whole clear. Returns bytes freed.
        """
        if not path.is_dir():
            return 0
        freed = 0
        for item in path.iterdir():
            try:
                if item.is_dir():
                    freed += _dir_size_bytes(item)  # measure before rmtree
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    freed += item.stat().st_size
                    item.unlink()
            except OSError as e:
                log.warning("Could not delete %s: %s", item, e)
        return freed

    @app.route("/api/cache/size", methods=["GET"])
    def api_cache_size():
        """
        Total on-disk size of the thumbnail cache + temp cache directories,
        in bytes and MB. Used by the Settings panel's "Clear Cache" section.
        """
        thumb_bytes = _dir_size_bytes(_get_thumb_cache_dir())
        cache_bytes = _dir_size_bytes(app_paths.CACHE_DIR)
        total_bytes = thumb_bytes + cache_bytes
        return jsonify({
            "total_bytes": total_bytes,
            "total_mb":    round(total_bytes / (1024 * 1024), 2),
        })

    @app.route("/api/cache/clear", methods=["POST"])
    def api_cache_clear():
        """
        Delete all contents of the thumbnail cache + temp cache directories.
        Safe to call any time — thumbnails and temp files are regenerated
        on demand the next time they're needed. Does not touch the SQLite
        DB, config, logs, or the user's original media files.
        """
        freed = 0
        freed += _clear_dir_contents(_get_thumb_cache_dir())
        freed += _clear_dir_contents(app_paths.CACHE_DIR)
        log.info("Cache cleared via /api/cache/clear — freed %d bytes", freed)
        return jsonify({
            "ok":         True,
            "freed_bytes": freed,
            "freed_mb":    round(freed / (1024 * 1024), 2),
        })

    # ── /api/thumb/<unique_name>  — small, cached, for grid ──────────────────
    @app.route("/api/thumb/<unique_name>", methods=["GET"])
    def api_thumb(unique_name):
        """
        Return a small thumbnail (default 400px max-side, quality 60).
        For videos: extracts a frame with ffmpeg (at 10% of duration or 1s).
        Result is cached to disk so decoding only happens once.
        """
        from flask import Response, abort
        import tempfile
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

                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False, dir=str(app_paths.CACHE_DIR)) as tmp:
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
                r = subprocess.run(cmd, capture_output=True, timeout=30, **_SUBPROCESS_NO_WINDOW_KWARGS)
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

    # ── HEVC/H.265 transcode-on-demand for browser playback ────────────────
    # Firefox ships no HEVC decoder at all (Chrome sometimes gets away with
    # it via an OS/hardware decoder, which is why this only shows up as a
    # cross-browser bug report). There's no client-side fix for that — the
    # only real option is converting the video server-side. We transcode
    # once on first playback request and cache the H.264/AAC MP4 result on
    # disk (under app_paths.CACHE_DIR — already covered by Settings' cache
    # size/clear UI), so every request after that just serves the cached
    # file directly: full Range/seek support, no repeated ffmpeg runs.
    _VIDEO_TRANSCODE_DIR     = app_paths.CACHE_DIR / "video_transcode"
    _CODECS_NEEDING_TRANSCODE = {"HEVC", "H265"}
    _transcode_locks_guard   = threading.Lock()
    _transcode_locks         = {}

    def _probe_codec_quick(full_path: str) -> str:
        """
        Fast standalone codec probe, used only as a fallback for items
        indexed before per-item codec metadata existed (or synced with
        ffprobe unavailable at the time) — so older libraries still get the
        fix without needing a full re-sync. Returns "" on any failure,
        which is treated as "don't transcode" (i.e. previous behavior).
        """
        try:
            cmd = [
                "ffprobe", "-v", "quiet",
                "-select_streams", "v:0",
                "-show_entries", "stream=codec_name",
                "-of", "csv=p=0",
                str(full_path),
            ]
            r = subprocess.run(cmd, capture_output=True, timeout=8, **_SUBPROCESS_NO_WINDOW_KWARGS)
            if r.returncode == 0:
                return r.stdout.decode(errors="ignore").strip().upper()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return ""

    def _needs_transcode(item, full_path: str) -> bool:
        codec = (item.get("metadata", {}).get("video", {}).get("codec") or "").upper()
        if not codec:
            codec = _probe_codec_quick(full_path)
        return codec in _CODECS_NEEDING_TRANSCODE

    def _transcode_lock_for(key: str) -> threading.Lock:
        with _transcode_locks_guard:
            lock = _transcode_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                _transcode_locks[key] = lock
            return lock

    def _get_transcoded_video_path(unique_name: str, full_path: str) -> Path:
        """
        Return a cached, browser-playable H.264/AAC MP4 for an HEVC source,
        transcoding on first request and reusing the cached copy after
        that. Cache is keyed off the source file's mtime, so replacing the
        source triggers a fresh transcode automatically.

        Raises on failure (missing ffmpeg, bad file, timeout) — caller
        falls back to serving the original file rather than erroring the
        request.
        """
        _VIDEO_TRANSCODE_DIR.mkdir(parents=True, exist_ok=True)
        cache_name = hashlib.sha1(unique_name.encode("utf-8")).hexdigest() + ".mp4"
        cache_path = _VIDEO_TRANSCODE_DIR / cache_name
        src_mtime  = os.path.getmtime(full_path)

        if cache_path.is_file() and cache_path.stat().st_mtime >= src_mtime:
            return cache_path

        # Only one transcode of a given file at a time — a scrubbing
        # browser issues several near-simultaneous range requests on first
        # open, and without this they'd each kick off their own ffmpeg run.
        lock = _transcode_lock_for(str(cache_path))
        with lock:
            if cache_path.is_file() and cache_path.stat().st_mtime >= src_mtime:
                return cache_path  # another request already finished it

            tmp_path = cache_path.with_suffix(".mp4.tmp")
            cmd = [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", str(full_path),
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-c:a", "aac", "-b:a", "160k",
                "-movflags", "+faststart",
                "-f", "mp4",
                str(tmp_path),
            ]
            log.info("Transcoding HEVC video for browser playback: %s", full_path)
            r = subprocess.run(cmd, capture_output=True, timeout=1800, **_SUBPROCESS_NO_WINDOW_KWARGS)
            if r.returncode != 0 or not tmp_path.is_file():
                if tmp_path.is_file():
                    tmp_path.unlink(missing_ok=True)
                log.warning(
                    "Video transcode failed for %s: %s",
                    full_path, r.stderr.decode(errors="ignore")[-500:]
                )
                raise RuntimeError(f"ffmpeg transcode failed for {full_path}")

            os.replace(tmp_path, cache_path)
            return cache_path

    # ── /api/video/<unique_name>  — byte-range streaming for <video> ──────────
    @app.route("/api/video/<unique_name>", methods=["GET", "HEAD"])
    def api_video(unique_name):
        """
        Stream video with proper HTTP range-request support.
        Uses send_file for simple requests; manual range slicing for seek requests.
        """
        import mimetypes
        from flask import Response, abort, send_file

        full_path, item = _resolve_path(unique_name)

        if not is_video(full_path):
            abort(415)

        # MIME type of the original file — HEAD always reports this, since
        # it's only ever used by the frontend's own pre-flight existence
        # check, not by the browser's media engine.
        ext  = Path(full_path).suffix.lower()
        orig_mime = {
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

        # ── HEAD request ──────────────────────────────────────────────────────
        if request.method == "HEAD":
            return Response(status=200, headers={
                "Accept-Ranges":  "bytes",
                "Content-Length": str(os.path.getsize(full_path)),
                "Content-Type":   orig_mime,
            })

        # ── GET: serve a cached H.264/AAC MP4 for codecs a lot of browsers
        # (Firefox especially) can't decode natively — HEVC/H.265 is the
        # main offender. Only actually transcodes when the requesting
        # browser told us (via ?client_hevc=1, set from a real
        # canPlayType() capability check in app.js, not a UA guess) that it
        # can't already play HEVC itself — so browsers/OSes with native
        # HEVC support skip the transcode (and its cache) entirely.
        # Transcodes once on first request that needs it and reuses the
        # cached file after that (see _get_transcoded_video_path above), so
        # this still gets full Range/seek support like any other file.
        # Falls back to the original file if transcoding isn't possible
        # (e.g. ffmpeg missing or the run itself failed).
        client_has_hevc = request.args.get("client_hevc") == "1"
        serve_path = full_path
        mime       = orig_mime
        if not client_has_hevc and _needs_transcode(item, full_path):
            try:
                serve_path = str(_get_transcoded_video_path(unique_name, full_path))
                mime       = "video/mp4"
            except Exception:
                serve_path = full_path
                mime       = orig_mime

        file_size    = os.path.getsize(serve_path)
        range_header = request.headers.get("Range", None)

        # ── No Range header: full file, 200 ──────────────────────────────────
        if not range_header:
            resp = send_file(serve_path, mimetype=mime, conditional=False)
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
            resp = send_file(serve_path, mimetype=mime, conditional=False)
            resp.headers["Accept-Ranges"] = "bytes"
            return resp

        start  = max(0, start)
        end    = min(end, file_size - 1)
        length = end - start + 1

        # Read the exact requested byte range into memory
        # (safe for typical browser chunks of 256 KB – 2 MB)
        with open(serve_path, "rb") as f:
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

    @app.route("/api/folders", methods=["GET"])
    def api_folders():
        return jsonify(load_folders())

    @app.route("/api/locations", methods=["GET"])
    def api_locations_get():
        """
        Return current contents of media.json.
        Each entry: { name, path, visibility, root, label, synced_count, exists, missing_subfolder_count }
        'root' and 'label' aliases are included so this endpoint can be used
        directly by the location filter dropdowns without a separate /api/media/locations call.
        'synced_count' is how many media rows are currently indexed under
        that path — the frontend uses it to decide whether removing the
        location needs the stronger "this will discard indexed files"
        confirmation, and to show the count inside that message.
        'exists' is whether the configured path can currently be found on
        disk — the frontend uses this to flag the row red and offer a
        "Relocate" action when a watched folder has been moved or renamed.
        'missing_subfolder_count' is how many of this (otherwise valid)
        location's indexed subfolders are no longer on disk — the frontend
        only shows the "Resolve" button, and flags the row, when this is
        greater than 0.
        """
        sources = load_json(MEDIA_JSON, [])
        result  = []
        for s in sources:
            path  = (s.get("path") or "").rstrip("/\\")
            name  = (s.get("name") or "").strip()
            label = name or (path.split("/")[-1] if path else path)
            exists = bool(path) and os.path.isdir(path)
            result.append({
                "name":                     name,
                "path":                     s.get("path", ""),
                "visibility":               s.get("visibility", True),
                "root":                     path,
                "label":                    label,
                "synced_count":             count_media_by_source_root(path),
                "exists":                   exists,
                # Only worth checking (and only makes sense to show Resolve
                # for) when the location's own path is fine — if it's
                # missing entirely, the existing whole-location Relocate
                # flow already covers it.
                "missing_subfolder_count":  count_missing_subfolders(path) if exists else 0,
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

    @app.route("/api/location/delete", methods=["POST"])
    def api_location_delete():
        """
        Remove one media location for good. Body: { path: str }

        Unlike /api/locations (POST), which just overwrites the whole list
        with whatever the frontend currently has, this endpoint does the two
        things a location removal actually needs to guarantee together, in
        one atomic step:
          1. Discards every already-indexed media row under that path from
             the DB (and their cached thumbnails) — nothing is left behind
             pointing at a location the user just said to stop tracking.
          2. Removes the entry from media.json so it's never scanned again.
        The original files on disk are never touched — only Luminary's own
        index of them. Returns { ok: true, deleted: N }.
        """
        body = request.get_json(force=True) or {}
        path = (body.get("path") or "").strip()
        if not path:
            return jsonify({"error": "path required"}), 400

        deleted = delete_media_by_source_root(path)

        sources  = load_json(MEDIA_JSON, [])
        root     = path.rstrip("/\\")
        remaining = [s for s in sources if (s.get("path") or "").rstrip("/\\") != root]
        save_json(MEDIA_JSON, remaining)

        log.info("Location removed: %s — discarded %d indexed record(s)", path, deleted)
        return jsonify({"ok": True, "deleted": deleted})

    @app.route("/api/location/relocate", methods=["POST"])
    def api_location_relocate():
        """
        Validate a relocated location against a new folder on disk.
        Body: { old_path, new_path }

        Used by the "Validate" action in the Locations Manager (after
        "Relocate" is clicked and a new folder is picked), or from a
        notification's action button. Every already-indexed media row under
        old_path is checked one file at a time against new_path — only rows
        whose file actually resolves there get repointed (source_root,
        file_path, and the embedded metadata); everything else is left
        as-is. Also rewrites media.json so nothing needs to be re-scanned.
        Returns { ok: true, updated, still_missing, old_root, new_root }.
        """
        body     = request.get_json(force=True) or {}
        old_path = (body.get("old_path") or "").strip()
        new_path = (body.get("new_path") or "").strip()
        if not old_path or not new_path:
            return jsonify({"error": "old_path and new_path are required"}), 400
        if not os.path.isdir(new_path):
            return jsonify({"error": f"Not a directory: {new_path}"}), 400

        result = relocate_media_source(old_path, new_path)
        resolve_location_notifications(old_path)

        if result["still_missing"] > 0:
            create_notification(
                "file_missing",
                title=f"{result['still_missing']} file(s) still not found",
                message=(
                    f"After relocating to \"{new_path}\", {result['still_missing']} "
                    f"file(s) could not be matched there and remain unlinked."
                ),
                location_path=new_path,
                action="relocate",
                action_label="View Location",
            )

        log.info(
            "Relocated location %s -> %s (%d matched, %d still missing)",
            old_path, new_path, result["updated"], result["still_missing"],
        )
        return jsonify({"ok": True, **result})

    @app.route("/api/location/resolve", methods=["POST"])
    def api_location_resolve():
        """
        Diagnostic scan for one configured location. Body: { path }

        Compares what's indexed in SQLite against what's actually on disk
        one directory at a time, to catch subfolder-level drift a plain
        "does the location's own path still exist?" check can't see — e.g.
        a subfolder renamed/moved while the location's own top-level path
        stayed valid. Read-only; makes no changes.
        Returns { root, missing: [{path, rel, count}], new: [{path, rel, count}] }.
        """
        body = request.get_json(force=True) or {}
        path = (body.get("path") or "").strip()
        if not path:
            return jsonify({"error": "path is required"}), 400
        return jsonify(diagnose_location_structure(path))

    @app.route("/api/location/relocate-subfolder", methods=["POST"])
    def api_location_relocate_subfolder():
        """
        Repoint just one subfolder's indexed rows at a new folder — the
        subfolder-scoped counterpart to /api/location/relocate, used when a
        subfolder was renamed/moved but its parent location's own path is
        still valid. Body: { old_path, new_path }.
        Returns { ok: true, updated, still_missing, old_path, new_path }.
        """
        body     = request.get_json(force=True) or {}
        old_path = (body.get("old_path") or "").strip()
        new_path = (body.get("new_path") or "").strip()
        if not old_path or not new_path:
            return jsonify({"error": "old_path and new_path are required"}), 400
        if not os.path.isdir(new_path):
            return jsonify({"error": f"Not a directory: {new_path}"}), 400

        result = relocate_media_subfolder(old_path, new_path)
        if result["still_missing"] > 0:
            create_notification(
                "file_missing",
                title=f"{result['still_missing']} file(s) still not found",
                message=(
                    f"After relocating to \"{new_path}\", {result['still_missing']} "
                    f"file(s) could not be matched there and remain unlinked."
                ),
                location_path=new_path,
                action="relocate",
                action_label="View Location",
            )
        log.info(
            "Relocated subfolder %s -> %s (%d matched, %d still missing)",
            old_path, new_path, result["updated"], result["still_missing"],
        )
        return jsonify({"ok": True, **result})

    @app.route("/api/location/rescan-subfolder", methods=["POST"])
    def api_location_rescan_subfolder():
        """
        Index new media found under one subfolder of a configured location,
        without re-walking the whole location. Body: { root, path } where
        root is the location's configured path and path is the subfolder
        (as returned by /api/location/resolve's "new" list) to scan.
        Returns { ok: true, scanned, added }.
        """
        body      = request.get_json(force=True) or {}
        root      = (body.get("root") or "").strip()
        subfolder = (body.get("path") or "").strip()
        if not root or not subfolder:
            return jsonify({"error": "root and path are required"}), 400
        if not os.path.isdir(root):
            return jsonify({"error": f"Not a directory: {root}"}), 400
        if not os.path.isdir(subfolder):
            return jsonify({"error": "That folder is no longer there"}), 400

        source_root = str(Path(root).resolve())
        result = rescan_subfolder(source_root, subfolder)
        return jsonify({"ok": True, **result})

    @app.route("/api/notifications", methods=["GET"])
    def api_notifications_get():
        """Return recent notifications (unread first) and the current unread count."""
        return jsonify({
            "items":        get_notifications(limit=100),
            "unread_count": get_unread_notification_count(),
        })

    @app.route("/api/notifications/<int:notif_id>/read", methods=["POST"])
    def api_notification_read(notif_id):
        ok = mark_notification_read(notif_id)
        return jsonify({"ok": ok, "unread_count": get_unread_notification_count()})

    @app.route("/api/notifications/read-all", methods=["POST"])
    def api_notifications_read_all():
        updated = mark_all_notifications_read()
        return jsonify({"ok": True, "updated": updated, "unread_count": 0})

    def _nearest_existing_ancestor(path_str: str) -> tuple:
        """
        Walk up from path_str until a directory that actually exists is
        found. Used by /api/browse so opening the folder picker on an old,
        now-gone location (the common "Relocate" starting point) doesn't
        dead-end at a bare error — it starts the user at the nearest
        ancestor folder that's still there, e.g. "a/b/c/d/photos" -> ...
        -> "a/b/c/d" -> "a/b/c" -> "a/b" -> "a" (stopping at the first one
        that exists). Returns (existing_path, original_path_if_different_else_None).
        """
        p = Path(path_str)
        requested = p
        while p.parent != p:
            try:
                if p.exists():
                    break
            except OSError:
                pass  # can't stat this component either — keep walking up
            p = p.parent
        return p, (requested if p != requested else None)

    @app.route("/api/browse", methods=["GET"])
    def api_browse():
        """
        List the subdirectories of a given path — powers the folder-browser
        popup next to the "Absolute path" field in Media Locations. Only
        directories are returned; files are irrelevant when picking a
        media root.

        Query params:
          path           — absolute directory to list. Omitted/blank starts
                           at the user's home directory (or, on Windows,
                           returns the drive list so the user has somewhere
                           to start from).
          allow_fallback — "1" to walk up to the nearest existing ancestor
                           instead of erroring when `path` doesn't exist,
                           reporting the fallback via "fallback_from" in the
                           response. Used only when the folder browser is
                           opened from "Relocate" on an already-broken
                           location (its old folder is expected to be gone
                           — that's the point of relocating it), so the user
                           lands somewhere useful instead of a dead end.
                           Omitted/"0" for the plain "Browse…" used when
                           adding/editing a location path, where a
                           nonexistent path is very likely just a typo and
                           should surface as a clear error instead of
                           silently jumping to a different folder.
        """
        raw            = request.args.get("path", "").strip()
        allow_fallback = request.args.get("allow_fallback", "").strip() == "1"

        if not raw:
            if platform.system() == "Windows":
                drives = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
                return jsonify({
                    "path":   "",
                    "parent": None,
                    "dirs":   [{"name": d, "path": d} for d in drives],
                })
            raw = str(Path.home())

        try:
            target = Path(raw).resolve(strict=False)
        except (OSError, RuntimeError):
            return jsonify({"error": f"Invalid path: {raw}"}), 400

        fallback_from = None
        if allow_fallback and not target.exists():
            target, requested = _nearest_existing_ancestor(target)
            if requested is not None:
                fallback_from = str(requested)

        if not target.exists():
            return jsonify({"error": f"Path does not exist: {target}"}), 404
        if not target.is_dir():
            return jsonify({"error": f"Not a directory: {target}"}), 400

        names = []
        try:
            with os.scandir(target) as it:
                for entry in it:
                    if entry.name.startswith("."):
                        continue  # hidden dirs clutter the picker without adding value
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            names.append(entry.name)
                    except OSError:
                        continue  # broken symlink or a stat we can't do — skip it
        except PermissionError:
            return jsonify({"error": f"Permission denied: {target}"}), 403

        names.sort(key=str.lower)

        parent = target.parent
        # No "Up" once we're at a filesystem root: POSIX '/' is its own
        # parent, and a Windows drive root (e.g. 'C:\\') has no useful parent.
        at_root = (parent == target) or (
            platform.system() == "Windows" and str(target).rstrip("\\").endswith(":")
        )
        return jsonify({
            "path":          str(target),
            "parent":        None if at_root else str(parent),
            "dirs":          [{"name": name, "path": str(target / name)} for name in names],
            "fallback_from": fallback_from,
        })

    @app.route("/api/sync", methods=["POST"])
    def api_sync():
        """
        Start a background sync. Returns 202 immediately.
        Returns 409 if a sync is already running.
        Poll GET /api/sync/status for live progress,
        or open GET /api/sync/stream for Server-Sent Events.
        """
        with _sync_lock:
            if _sync_state["running"]:
                return jsonify({"error": "Sync already in progress"}), 409
            # Reset state for new run
            _sync_state["running"]        = True
            _sync_state["done"]           = False
            _sync_state["error"]          = None
            _sync_state["result"]         = None
            _sync_state["scanned"]        = 0
            _sync_state["added"]          = 0
            _sync_state["removed"]        = 0
            _sync_state["current_file"]   = ""
            _sync_state["current_source"] = ""
            _sync_state["total_at_start"] = get_media_count()
            _sync_state["log"]            = deque(maxlen=200)

        def _progress(event, **kw):
            if event == "source":
                _sync_update(current_source=kw.get("source", ""),
                             log=kw.get("msg", ""))
            elif event == "progress":
                updates = {}
                if kw.get("scanned") is not None: updates["scanned"] = kw["scanned"]
                if kw.get("added")   is not None: updates["added"]   = kw["added"]
                if kw.get("removed") is not None: updates["removed"] = kw["removed"]
                if kw.get("file"):                updates["current_file"] = kw["file"]
                if kw.get("msg"):                 updates["log"] = kw["msg"]
                if updates:
                    _sync_update(**updates)
            elif event == "log":
                _sync_update(log=kw.get("msg", ""))

        def _run():
            try:
                result = sync_library(progress=_progress)
                _sync_update(
                    running=False, done=True,
                    result=result,
                    current_file="", current_source="",
                    log=f"✓ Complete — {result['added']} new, {result.get('removed', 0)} removed, "
                        f"{result.get('missing', 0)} missing, {result['total']} total",
                )
            except Exception as e:
                log.exception("Background sync failed")
                _sync_update(
                    running=False, done=True,
                    error=str(e),
                    log=f"✗ Sync error: {e}",
                )

        threading.Thread(target=_run, daemon=True, name="luminary-sync").start()
        return jsonify({"status": "started"}), 202

    @app.route("/api/sync/status", methods=["GET"])
    def api_sync_status():
        """
        Return the current sync state as JSON (for polling).
        {running, done, scanned, added, total_at_start,
         current_file, current_source, log[last 50], result, error}
        """
        snap = _sync_snapshot()
        snap["log"] = snap["log"][-50:]   # send only last 50 lines to keep response small
        return jsonify(snap)

    @app.route("/api/sync/stream", methods=["GET"])
    def api_sync_stream():
        """
        Server-Sent Events stream — pushes sync progress to the browser in real time.
        Events: connected | progress | log | complete | heartbeat
        Stream closes automatically when sync finishes.
        """
        from flask import Response
        import time

        def generate():
            yield "event: connected\ndata: {}\n\n"
            prev_log_len = 0
            prev_added   = -1
            prev_scanned = -1

            while True:
                snap = _sync_snapshot()

                if snap["added"] != prev_added or snap["scanned"] != prev_scanned:
                    prev_added   = snap["added"]
                    prev_scanned = snap["scanned"]
                    payload = json.dumps({
                        "scanned":        snap["scanned"],
                        "added":          snap["added"],
                        "total_at_start": snap["total_at_start"],
                        "current_source": snap["current_source"],
                        "current_file":   snap["current_file"],
                    })
                    yield f"event: progress\ndata: {payload}\n\n"

                new_lines = list(snap["log"])[prev_log_len:]
                for line in new_lines:
                    yield f"event: log\ndata: {json.dumps(line)}\n\n"
                prev_log_len = len(snap["log"])

                if snap["done"]:
                    payload = json.dumps({
                        "added":   snap["added"],
                        "scanned": snap["scanned"],
                        "result":  snap["result"],
                        "error":   snap["error"],
                    })
                    yield f"event: complete\ndata: {payload}\n\n"
                    return

                yield "event: heartbeat\ndata: {}\n\n"
                time.sleep(1.5)

        return Response(
            generate(),
            mimetype="text/event-stream",
            headers={
                "Cache-Control":     "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

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
        albums  = load_albums()
        folders = load_folders()
        return jsonify({
            "media":    items,
            "albums":   albums,
            "folders":  folders,
            "total":    total,
            "offset":   offset,
            "has_more": (offset + limit) < total,
        })

    @app.route("/api/db", methods=["POST"])
    def api_db_post():
        """
        Save updated albums and/or individual media record changes from the frontend.
        Media records are UPSERTED (never full-table replaced) to prevent data loss
        when the frontend only has a partial page of records loaded.
        Albums are always replaced in full (they're small and always fully loaded).
        """
        data = request.get_json(force=True)
        if not data:
            return jsonify({"error": "No data"}), 400

        if "media" in data and data["media"]:
            # Upsert — update only the records sent, leave all others untouched
            upsert_media_rows(data["media"])

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

    @app.route("/api/album/move", methods=["POST"])
    def api_album_move():
        """
        Move an album into a folder, or out of any folder.
        Body: { albumId: str, folderId: str|None }
        """
        body      = request.get_json(force=True) or {}
        album_id  = body.get("albumId")
        folder_id = body.get("folderId") or None
        if not album_id:
            return jsonify({"error": "albumId required"}), 400
        result = move_album_to_folder(album_id, folder_id)
        if result == "album_not_found":
            return jsonify({"error": "Album not found"}), 404
        if result == "folder_not_found":
            return jsonify({"error": "Folder not found"}), 404
        return jsonify({"ok": True})

    @app.route("/api/folder/create", methods=["POST"])
    def api_folder_create():
        body   = request.get_json(force=True) or {}
        name   = (body.get("name") or "Untitled").strip() or "Untitled"
        folder = create_folder(name)
        return jsonify(folder)

    @app.route("/api/folder/rename", methods=["POST"])
    def api_folder_rename():
        body      = request.get_json(force=True) or {}
        folder_id = body.get("folderId")
        name      = (body.get("name") or "").strip()
        if not folder_id or not name:
            return jsonify({"error": "folderId and name required"}), 400
        ok = rename_folder(folder_id, name)
        if not ok:
            return jsonify({"error": "Folder not found"}), 404
        return jsonify({"ok": True})

    @app.route("/api/folder/delete", methods=["POST"])
    def api_folder_delete():
        """
        Delete a folder. Body: { folderId: str, force: bool }
        Without force, if the folder still contains albums, returns 409 with
        {needs_confirmation: true, album_count, album_names} instead of
        deleting anything — the frontend shows a warning and re-calls with
        force:true to proceed. Media files are never affected either way.
        """
        body      = request.get_json(force=True) or {}
        folder_id = body.get("folderId")
        force     = bool(body.get("force", False))
        if not folder_id:
            return jsonify({"error": "folderId required"}), 400
        result = delete_folder(folder_id, force=force)
        if not result.get("ok"):
            if result.get("needs_confirmation"):
                return jsonify(result), 409
            return jsonify({"error": result.get("error", "Delete failed")}), 404
        return jsonify(result)

    @app.route("/api/album/add-bulk", methods=["POST"])
    def api_album_add_bulk():
        """
        Add a list of uniqueNames to an album in a single transaction.
        Body: { albumId: str, uniqueNames: [str, ...] }
        Returns { ok: true, added: N } where N is how many were newly inserted.
        """
        body         = request.get_json(force=True) or {}
        album_id     = body.get("albumId")
        unique_names = body.get("uniqueNames", [])
        if not album_id:
            return jsonify({"error": "albumId required"}), 400
        conn = get_db_conn()
        album_exists = conn.execute("SELECT 1 FROM albums WHERE id = ?", (album_id,)).fetchone()
        if not album_exists:
            return jsonify({"error": "Album not found"}), 404
        with _db_lock:
            with conn:
                max_pos = conn.execute(
                    "SELECT COALESCE(MAX(position), -1) FROM album_media WHERE album_id = ?",
                    (album_id,)
                ).fetchone()[0]
                added = 0
                for un in unique_names:
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO album_media (album_id, uniqueName, position) VALUES (?, ?, ?)",
                        (album_id, un, max_pos + 1 + added)
                    )
                    added += cur.rowcount
        return jsonify({"ok": True, "added": added})

    @app.route("/api/media/hide", methods=["POST"])
    def api_media_hide():
        body        = request.get_json(force=True) or {}
        unique_name = body.get("uniqueName")
        hidden      = body.get("hidden", True)
        ok = set_media_hidden(unique_name, hidden)
        if ok:
            return jsonify({"ok": True})
        return jsonify({"error": "Not found"}), 404

    @app.route("/api/media/hide-bulk", methods=["POST"])
    def api_media_hide_bulk():
        """
        Set hidden/unhidden for a batch of media in one request.
        Body: { uniqueNames: [str, ...], hidden: bool }
        Returns { ok: true, updated: N }.
        """
        body         = request.get_json(force=True) or {}
        unique_names = body.get("uniqueNames", [])
        hidden       = body.get("hidden", True)
        if not unique_names:
            return jsonify({"error": "uniqueNames required"}), 400
        updated = set_media_hidden_bulk(unique_names, hidden)
        return jsonify({"ok": True, "updated": updated})



    def run_server(port: int = 5000):
        if WAITRESS_AVAILABLE:
            log.info("Starting Luminary backend (Waitress) on http://0.0.0.0:%d", port)
            # threads=16: a media gallery fires many concurrent thumbnail/image
            # requests per page load (browsers open several connections at once),
            # and /api/sync/stream holds one thread open for its entire duration
            # while a sync is running. 4 threads queued up almost immediately
            # ("Task queue depth" warnings) — 16 gives real headroom. The shared
            # SQLite connection is thread-local (see get_db_conn) and writes are
            # serialised via _db_lock, so more worker threads is safe.
            _waitress_serve(app, host="0.0.0.0", port=port, threads=16)
        else:
            log.warning("Waitress not installed — using Flask's development server "
                        "(not recommended for production). Run: pip install waitress")
            # threaded=False: serialises all requests through one thread so the single
            # shared SQLite connection is never accessed concurrently. The sync runs in
            # its own daemon thread but only writes between request cycles. WAL mode
            # means reads (most requests) never block on the sync write thread.
            app.run(host="0.0.0.0", port=port, debug=False, threaded=True,
                    use_reloader=False)


def _cleanup_video_transcode_cache():
    """
    Delete the video-transcode cache directory (and everything in it) on
    shutdown, so converted HEVC→H.264 copies never persist between runs —
    only the thumbnail cache and other scratch cache survive a restart.
    Registered with atexit below, which covers normal interpreter exit
    (Ctrl+C in dev mode, tray Quit, sys.exit()) as well as SIGTERM once
    that's translated into a normal exit by the signal handler in the
    entry point below (relevant for `systemctl stop luminary` on a
    headless install). Best-effort: doesn't raise if a file is still in
    use or the directory doesn't exist.
    """
    transcode_dir = app_paths.CACHE_DIR / "video_transcode"
    if not transcode_dir.is_dir():
        return
    try:
        shutil.rmtree(transcode_dir, ignore_errors=True)
        log.info("Cleared video-transcode cache on shutdown: %s", transcode_dir)
    except Exception as e:
        log.warning("Could not fully clear video-transcode cache %s: %s", transcode_dir, e)


atexit.register(_cleanup_video_transcode_cache)


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Luminary backend")
    parser.add_argument("--sync-only", action="store_true", help="Run sync and exit without starting server")
    parser.add_argument("--port", type=int, default=5000, help="API port (default 5000)")
    parser.add_argument("--no-tray", action="store_true",
                         help="Force plain console mode even in an installed build "
                              "(skips the system tray icon; useful for debugging)")
    parser.add_argument("--allow-multiple-instances", action="store_true",
                         help="Skip the single-instance check (advanced/dev use only — "
                              "e.g. intentionally running two dev servers on different "
                              "--port values at once)")
    args = parser.parse_args()

    if args.sync_only:
        result = sync_library()
        print(json.dumps(result, indent=2))
        sys.exit(0)

    if not FLASK_AVAILABLE:
        print("ERROR: Flask not installed. Run: pip install flask flask-cors")
        sys.exit(1)

    # Translate SIGTERM (e.g. `systemctl stop luminary` on a headless
    # install, or Task Manager's "End task" on Windows) into a normal
    # Python exit, the same as Ctrl+C already does — otherwise the process
    # is torn down without ever reaching the try/finally below or the
    # atexit-registered cache cleanup above.
    def _handle_sigterm(signum, frame):
        log.info("Received SIGTERM — shutting down.")
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, _handle_sigterm)

    # ── single-instance guard ────────────────────────────────────────────
    # Applies in every environment (dev console, installed tray app, headless
    # server) — see instance_lock.py for how the lock actually works and why
    # it's crash-safe. Skipped entirely for --sync-only above since that's a
    # short-lived one-off command, not "the app running".
    instance_lock = None
    if not args.allow_multiple_instances:
        from instance_lock import SingleInstance
        instance_lock = SingleInstance(port=args.port)
        if instance_lock.already_running:
            existing_port = instance_lock.existing_port or args.port
            msg = (
                f"Luminary is already running (PID {instance_lock.existing_pid}, "
                f"http://localhost:{existing_port}/) — not starting a second instance."
            )
            print(msg)
            log.info(msg)
            try:
                from tray import is_display_available
                if is_display_available():
                    import webbrowser
                    webbrowser.open(f"http://localhost:{existing_port}/")
            except Exception:
                pass  # best-effort only — a headless box has nothing to open anyway
            sys.exit(0)

    # Dev mode (`python3 app.py` / `./run.sh`, i.e. NOT a frozen PyInstaller
    # build) always runs the plain console server, exactly as before — you
    # keep seeing normal terminal output while developing.
    #
    # A frozen/installed build (double-clicked from the Start Menu / desktop
    # launcher) instead runs as a system tray application: no console window,
    # an icon in the tray with Open/About/Start-Stop/Quit, and the server
    # itself running on a background thread that the tray controls.
    #
    # Exception: a headless Linux install (e.g. a Raspberry Pi set up over
    # SSH with no desktop environment) has no display for a tray icon to
    # attach to at all — that's auto-detected below and falls back to the
    # same plain background server dev mode uses, with no action needed from
    # the user (see README.md's "Headless / Server Mode" section for running
    # it as a systemd service in that case).
    run_as_tray = app_paths.is_frozen() and not args.no_tray

    try:
        if run_as_tray:
            try:
                from tray import run_tray, is_display_available
                if not is_display_available():
                    log.info(
                        "No graphical session detected (DISPLAY/WAYLAND_DISPLAY not "
                        "set — likely a headless Linux install, e.g. a Raspberry Pi "
                        "without a desktop environment). Running Luminary as a plain "
                        "background server instead of a system tray app."
                    )
                    run_server(port=args.port)
                else:
                    run_tray(app, port=args.port)
            except Exception:
                log.exception("Failed to start the system tray — falling back to console mode.")
                run_server(port=args.port)
        else:
            run_server(port=args.port)
    finally:
        # Releases the OS-level lock so the next launch can acquire it —
        # reached on normal shutdown (Quit from the tray, Ctrl+C in dev
        # mode) as well as on any unhandled exception above. If the lock
        # was never acquired (already_running, or --allow-multiple-instances
        # was passed), this is a harmless no-op.
        if instance_lock is not None:
            instance_lock.release()