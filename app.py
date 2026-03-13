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
MEDIA_JSON     = BASE_DIR / "media.json"
DB_JSON        = BASE_DIR / "db.json"
CONFIG_JSON    = BASE_DIR / "configuration.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger("luminary")


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def load_json(path: Path, default=None):
    if default is None:
        default = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        log.warning("Could not load %s: %s", path, e)
        return default

def save_json(path: Path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    log.info("Saved %s", path)

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

def extract_metadata(filepath: str) -> dict:
    """Extract metadata from an image file."""
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
        with Image.open(filepath) as img:
            w, h = img.size
            orientation = "landscape" if w >= h else "portrait"
            meta["image"] = {
                "width":       w,
                "height":      h,
                "resolution":  f"{w}x{h}",
                "color_space": img.mode,
                "orientation": orientation,
            }

            exif_data = img._getexif() if hasattr(img, "_getexif") else None
            if exif_data:
                for tag_id, value in exif_data.items():
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
                    elif tag == "GPSInfo":
                        gps = {}
                        for gps_id, gps_val in value.items():
                            gps[gps_id] = gps_val
                        coords = extract_gps(gps)
                        meta["location"].update(coords)

    except Exception as e:
        log.warning("Metadata extraction failed for %s: %s", filepath, e)

    return meta


# ══════════════════════════════════════════════════════════════════════════════
#  SCANNER / SYNC
# ══════════════════════════════════════════════════════════════════════════════

def load_config() -> dict:
    defaults = {
        "supported_formats": ["jpg", "jpeg", "png", "heic", "webp", "tiff", "bmp", "gif"],
        "thumbnail_size": 400,
        "thumbnail_quality": 60,
        "thumbnail_cache_path": "",
        "lazy_load_batch": 50,
        "show_hidden_default": False,
        "api_port": 5000,
    }
    cfg = load_json(CONFIG_JSON, defaults)
    for k, v in defaults.items():
        cfg.setdefault(k, v)
    return cfg

def sync_library() -> dict:
    """Scan configured directories and update db.json with new media."""
    cfg      = load_config()
    sources  = load_json(MEDIA_JSON, [])
    db       = load_json(DB_JSON, {"media": [], "albums": []})

    if "media"  not in db: db["media"]  = []
    if "albums" not in db: db["albums"] = []

    supported = {f.lower() for f in cfg.get("supported_formats", [])}

    # Build lookup maps
    existing_hashes = {m.get("hash") for m in db["media"] if m.get("hash")}
    existing_paths  = {(m["metadata"]["file"]["path"] + m["name"]) for m in db["media"]
                       if m.get("metadata", {}).get("file", {}).get("path")}

    added = 0
    scanned = 0

    for source in sources:
        if not source.get("visibility", True):
            log.info("Skipping hidden source: %s", source.get("name"))
            continue

        dir_path = source.get("path", "")
        if not os.path.isdir(dir_path):
            log.warning("Directory not found: %s", dir_path)
            continue

        log.info("Scanning: %s (%s)", source.get("name"), dir_path)

        for root, _, files in os.walk(dir_path):
            for filename in files:
                ext = Path(filename).suffix.lstrip(".").lower()
                if ext not in supported:
                    continue

                full_path = os.path.join(root, filename)
                scanned += 1

                # Dedup by path
                if full_path in existing_paths:
                    continue

                # Dedup by hash
                fhash = file_hash(full_path)
                if fhash in existing_hashes:
                    log.debug("Duplicate (hash): %s", filename)
                    continue

                # New file — extract and index
                meta = extract_metadata(full_path)
                entry = {
                    "name":       filename,
                    "uniqueName": str(uuid.uuid4()),
                    "hash":       fhash,
                    "isHidden":   False,
                    "metadata":   meta,
                }
                db["media"].append(entry)
                existing_hashes.add(fhash)
                existing_paths.add(full_path)
                added += 1
                log.info("Indexed: %s", filename)

    save_json(DB_JSON, db)
    log.info("Sync complete — scanned %d, added %d, total %d", scanned, added, len(db["media"]))
    return {"added": added, "scanned": scanned, "total": len(db["media"])}


# ══════════════════════════════════════════════════════════════════════════════
#  FLASK API
# ══════════════════════════════════════════════════════════════════════════════

if FLASK_AVAILABLE:
    app = Flask(__name__, static_folder=".", static_url_path="")
    CORS(app)

    @app.route("/")
    def serve_index():
        return app.send_static_file("index.html")

    @app.route("/api/media", methods=["GET"])
    def api_media():
        db = load_json(DB_JSON, {"media": [], "albums": []})
        return jsonify(db.get("media", []))

    # ── shared image helpers ──────────────────────────────────────────────────

    def _resolve_path(unique_name):
        """Return (full_path, item) or raise 404."""
        from flask import abort
        db = load_json(DB_JSON, {"media": [], "albums": []})
        item = next((m for m in db.get("media", []) if m["uniqueName"] == unique_name), None)
        if not item:
            abort(404)
        file_dir  = item.get("metadata", {}).get("file", {}).get("path", "")
        full_path = os.path.join(file_dir, item["name"])
        if not os.path.isfile(full_path):
            log.warning("File not found on disk: %s", full_path)
            abort(404)
        return full_path, item

    def _open_as_pil(full_path):
        """
        Open any supported image (including HEIC/HEIF) and return a PIL Image.
        Tries pillow-heif → pyheif → ImageMagick/ffmpeg for HEIC.
        Returns PIL Image or raises RuntimeError.
        """
        from PIL import Image as PilImage
        ext = Path(full_path).suffix.lower()

        if ext in (".heic", ".heif"):
            # Strategy 1: pillow-heif
            try:
                import pillow_heif
                pillow_heif.register_heif_opener()
                return PilImage.open(full_path).copy()
            except ImportError:
                pass

            # Strategy 2: pyheif
            try:
                import pyheif
                heif_file = pyheif.read(full_path)
                return PilImage.frombytes(
                    heif_file.mode, heif_file.size, heif_file.data,
                    "raw", heif_file.mode, heif_file.stride,
                )
            except ImportError:
                pass

            # Strategy 3: system tools → temp JPEG → re-open
            import subprocess, tempfile
            for cmd_fn in [
                lambda s, d: ["magick", s, d],
                lambda s, d: ["convert", s, d],
                lambda s, d: ["ffmpeg", "-y", "-i", s, d],
            ]:
                try:
                    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                        tmp_path = tmp.name
                    r = subprocess.run(cmd_fn(full_path, tmp_path),
                                       capture_output=True, timeout=15)
                    if r.returncode == 0 and os.path.isfile(tmp_path):
                        img = PilImage.open(tmp_path).copy()
                        os.unlink(tmp_path)
                        return img
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    continue

            raise RuntimeError("No HEIC decoder found — run: pip install pillow-heif")

        # Standard formats
        return PilImage.open(full_path).copy()

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
        Result is cached to disk so HEIC decode only happens once.
        """
        from flask import Response, abort
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

        full_path, _ = _resolve_path(unique_name)
        try:
            img  = _open_as_pil(full_path)
            resp = _jpeg_response(img, quality=quality, max_side=size)
            # Write to disk cache
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

    @app.route("/api/albums", methods=["GET"])
    def api_albums():
        db = load_json(DB_JSON, {"media": [], "albums": []})
        return jsonify(db.get("albums", []))

    @app.route("/api/sync", methods=["POST"])
    def api_sync():
        result = sync_library()
        return jsonify(result)

    @app.route("/api/db", methods=["GET"])
    def api_db_get():
        db = load_json(DB_JSON, {"media": [], "albums": []})
        return jsonify(db)

    @app.route("/api/db", methods=["POST"])
    def api_db_post():
        """Receive updated db from frontend (albums, hidden flags, etc.)."""
        data = request.get_json(force=True)
        if not data:
            return jsonify({"error": "No data"}), 400
        # Preserve hashes from existing records
        existing = load_json(DB_JSON, {"media": [], "albums": []})
        hash_map = {m["uniqueName"]: m.get("hash") for m in existing.get("media", [])}
        for m in data.get("media", []):
            if m.get("uniqueName") in hash_map:
                m.setdefault("hash", hash_map[m["uniqueName"]])
        save_json(DB_JSON, data)
        return jsonify({"ok": True})

    @app.route("/api/album/create", methods=["POST"])
    def api_album_create():
        body = request.get_json(force=True) or {}
        name = body.get("name", "Untitled")
        db = load_json(DB_JSON, {"media": [], "albums": []})
        album = {"name": name, "id": "album_" + str(uuid.uuid4())[:8], "media": []}
        db.setdefault("albums", []).append(album)
        save_json(DB_JSON, db)
        return jsonify(album)

    @app.route("/api/album/add", methods=["POST"])
    def api_album_add():
        body = request.get_json(force=True) or {}
        album_id   = body.get("albumId")
        unique_name = body.get("uniqueName")
        db = load_json(DB_JSON, {"media": [], "albums": []})
        for a in db.get("albums", []):
            if a["id"] == album_id:
                if unique_name not in a["media"]:
                    a["media"].append(unique_name)
                save_json(DB_JSON, db)
                return jsonify({"ok": True})
        return jsonify({"error": "Album not found"}), 404

    @app.route("/api/media/hide", methods=["POST"])
    def api_media_hide():
        body = request.get_json(force=True) or {}
        unique_name = body.get("uniqueName")
        hidden      = body.get("hidden", True)
        db = load_json(DB_JSON, {"media": [], "albums": []})
        for m in db.get("media", []):
            if m["uniqueName"] == unique_name:
                m["isHidden"] = hidden
                save_json(DB_JSON, db)
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