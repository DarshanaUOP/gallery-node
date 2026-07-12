# Luminary — Local Media Gallery

A lightweight, local-first photo and video gallery. Index media from your filesystem, browse with a refined dark UI, organise into albums, view geotagged photos on a map, and manage everything without any cloud dependency, accounts, or tracking.

---

## Requirements

- Python 3.8+
- ffmpeg — for video thumbnails and metadata (`apt install ffmpeg` or `brew install ffmpeg`)
- Internet connection for the **Map** view — Leaflet, the marker-clustering plugin, and OpenStreetMap tiles are loaded from a CDN at runtime. Everything else works fully offline.

---

## Installation

```bash
# 1. Clone / download the project folder
cd luminary

# 2. Install Python dependencies
pip install -r requirements.txt
```

`requirements.txt` includes `pillow-heif` for HEIC/HEIF support. It's optional but strongly recommended — without it, Luminary falls back to system ImageMagick or ffmpeg for HEIC decoding.

---

## Quick Start

```bash
chmod +x run.sh
./run.sh
```

Then open **http://localhost:5000** in your browser.

```bash
./run.sh --sync                            # run a sync pass then start the server
python3 app/src/backend/app.py              # start server on port 5000
python3 app/src/backend/app.py --port 8080  # custom port
python3 app/src/backend/app.py --sync-only  # scan directories and exit, no server
```

---

## Project Structure

```
luminary/
├── README.md
├── requirements.txt              # Python dependencies
├── run.sh                        # Startup script
└── app/
    └── src/
        ├── backend/
        │   ├── app.py                 # Flask backend — API, scanner, metadata extractor
        │   ├── config/                # App settings — tracked in git
        │   │   └── configuration.json # All configurable settings (edit this)
        │   ├── data/                  # Runtime data — auto-created, git-ignored
        │   │   ├── media.json         # Source directory list
        │   │   ├── luminary.db        # Media + albums — SQLite database
        │   │   ├── luminary.db-wal    # SQLite write-ahead log (transient)
        │   │   └── luminary.db-shm    # SQLite shared memory (transient)
        │   ├── logs/                  # Daily rotating logs — git-ignored
        │   │   └── log-YYYY-MM-DD.log
        │   └── thumb/                 # Thumbnail cache — auto-created, git-ignored
        └── frontend/
            ├── index.html             # Frontend markup
            └── static/
                ├── app.js             # Frontend logic
                └── style.css          # Frontend styles
```

On first run `app.py` automatically creates `backend/data/media.json` with a default entry pointing at `~/Pictures`. `backend/config/configuration.json` is shipped with the project and tracked in git — edit it directly to change settings.

`index.html` is served from `app/src/frontend/`, with `app.js` and `style.css` served from `app/src/frontend/static/` — no build step or bundler required, just edit and refresh.

### Why SQLite

Media metadata is stored in `data/luminary.db` (SQLite) rather than JSON. SQLite is built into Python — no installation needed, no server process, one file. Frequently queried fields (format, camera, source directory, hidden flag, sort date) are stored as indexed columns so filtering and sorting thousands of photos is done entirely inside SQLite in milliseconds. WAL mode allows the gallery to keep reading while a sync writes in the background.

Upgrading from an older version that used `db.json`/`albums.json`? Those files are automatically detected and migrated into SQLite on first run, then renamed to `*.json.migrated`.

---

## Configuration

### `data/media.json` — Source directories

Defines which directories Luminary scans. Luminary recurses into all subdirectories automatically.

```json
[
  {
    "name": "Travel Photos",
    "path": "/home/you/Pictures/Travel",
    "visibility": true
  },
  {
    "name": "Family",
    "path": "/home/you/Pictures/Family",
    "visibility": true
  },
  {
    "name": "Old Archive",
    "path": "/mnt/backup/Photos",
    "visibility": false
  }
]
```

| Field | Description |
|---|---|
| `name` | Label shown in the Locations filter dropdown |
| `path` | Absolute path to the root directory |
| `visibility` | `false` skips this source during Sync without removing indexed media |

Manage this file through the UI via **⊞ Locations** in the top bar.

### `config/configuration.json` — App settings

All fields with their defaults:

```json
{
  "theme": "dark",
  "grid_columns": 4,
  "card_size": "medium",
  "show_filename_on_card": true,
  "show_date_on_card": true,
  "show_subfolder_on_card": true,

  "default_sort": "date-desc",
  "default_date_field": "modified",
  "show_hidden_default": false,

  "lazy_load_batch": 50,
  "media_page_size": 500,
  "thumbnail_size": 400,
  "thumbnail_quality": 60,
  "thumbnail_cache_path": "thumb",

  "supported_image_formats": ["jpg","jpeg","png","heic","heif","webp","tiff","bmp","gif"],
  "supported_video_formats": ["mp4","mov","avi","mkv","webm","m4v","3gp","wmv","flv","ts","mts"],
  "video_autoplay": false,
  "video_preload": "metadata",

  "follow_symlinks": true,
  "skip_hidden_dirs": true,
  "max_scan_depth": 0,
  "dedup_method": "both",

  "show_gps_in_metadata": true,
  "extract_video_metadata": true,

  "api_port": 5000,
  "log_level": "INFO",
  "log_retention_days": 30
}
```

**Appearance**

| Key | Default | Description |
|---|---|---|
| `theme` | `dark` | `dark` \| `light` \| `system` |
| `grid_columns` | `4` | `2` \| `3` \| `4` \| `auto` |
| `card_size` | `medium` | `small` \| `medium` \| `large` — controls card height |
| `show_filename_on_card` | `true` | Show filename label on each card |
| `show_date_on_card` | `true` | Show date label on each card |
| `show_subfolder_on_card` | `true` | Show subfolder path badge on each card |

**Sorting & Filtering**

| Key | Default | Description |
|---|---|---|
| `default_sort` | `date-desc` | `date-desc` \| `date-asc` \| `name` |
| `default_date_field` | `modified` | `modified` \| `created` — which date is used for sorting |
| `show_hidden_default` | `false` | Show hidden media on initial load |

**Performance**

| Key | Default | Description |
|---|---|---|
| `lazy_load_batch` | `50` | Cards rendered per scroll batch |
| `media_page_size` | `500` | Records fetched per API page |
| `thumbnail_size` | `400` | Max thumbnail dimension in pixels |
| `thumbnail_quality` | `60` | Thumbnail JPEG quality (1–95) |
| `thumbnail_cache_path` | `thumb` | Relative paths resolve from project root; absolute paths used as-is |

**Media Types**

| Key | Default | Description |
|---|---|---|
| `supported_image_formats` | see above | Image extensions to index during Sync |
| `supported_video_formats` | see above | Video extensions to index during Sync |
| `video_autoplay` | `false` | Auto-play videos when opened in the viewer |
| `video_preload` | `metadata` | `none` \| `metadata` \| `auto` |

**Sync Behaviour**

| Key | Default | Description |
|---|---|---|
| `follow_symlinks` | `true` | Follow symlinked subdirectories during scan |
| `skip_hidden_dirs` | `true` | Skip directories whose name starts with `.` |
| `max_scan_depth` | `0` | Max recursion depth; `0` = unlimited |
| `dedup_method` | `both` | `path` \| `hash` \| `both` — how duplicates are detected |

**Metadata**

| Key | Default | Description |
|---|---|---|
| `show_gps_in_metadata` | `true` | Show GPS coordinates in the metadata panel |
| `extract_video_metadata` | `true` | Run ffprobe to extract video codec, duration, resolution |

**Server**

| Key | Default | Description |
|---|---|---|
| `api_port` | `5000` | Port the backend listens on (requires restart to change) |
| `log_level` | `INFO` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` |
| `log_retention_days` | `30` | Days of log files to keep |

All settings can be changed through **⚙ Settings** in the sidebar without editing the file directly. Changes are written back to `config/configuration.json`.

---

## Syncing Media

Click **↻ Sync** in the top bar, or trigger from the command line:

```bash
curl -X POST http://localhost:5000/api/sync
```

The backend will:

1. Read all visible sources from `data/media.json`
2. Walk each directory recursively (all subdirectories, following symlinks if enabled)
3. Skip already-indexed files using resolved path and MD5 hash deduplication
4. Extract EXIF metadata for images and ffprobe metadata for videos
5. Incrementally insert new records into `data/luminary.db` in batches of 200 (existing records are untouched)

New media appears in the gallery immediately — no page refresh needed.

---

## Features

### Gallery
- **CSS Grid layout** — 4 columns desktop, scales down to 2 on mobile; column count and card height configurable in Settings
- **Server-side pagination** — first page loads immediately; subsequent pages are fetched on scroll via the IntersectionObserver, one page at a time
- **Scroll date indicator** — floating pill shows the date range of currently visible cards while scrolling
- **Subdirectory path badge** — shows which subfolder a file came from, relative to its source root

### Filtering & Sorting
- **Sort** — Newest First / Oldest First (by modified date, falling back to created) / Name; sorting is server-side so all records are sorted correctly regardless of which page has loaded
- **Filter dropdowns** — Format, Camera, Location; populated from the full database on load via dedicated indexed API endpoints, not from the currently loaded page
- **Search** — full-text across filename, camera make/model, and date
- All filters are applied server-side; each filter change re-fetches from the server at offset 0

### Media Viewer
- **Two-column lightbox** — media fills the left pane; filename, navigation, zoom controls, and full metadata panel are in the right panel
- **Progressive image loading** — blurred thumbnail shown immediately while full resolution loads, replaced with a smooth fade
- **Zoom** — mouse wheel, +/− buttons, 1:1 actual size, fit-to-screen, drag to pan, pinch-to-zoom on touch, double-click to toggle; keyboard: `+` `-` `0` `1`
- **Keyboard navigation** — `←` `→` to navigate, `Esc` to close
- **Video player** — native `<video>` element with HTTP range-request streaming so the browser loads only what it needs; seeking works without downloading the full file
- **HEIC/HEIF** — decoded on the fly via pillow-heif → pyheif → ImageMagick → ffmpeg fallback chain
- **Unsupported formats** — clear error message with a direct download link

### Albums
- Create, rename (inline click on title or pencil icon in sidebar), and delete albums
- **Add Photos picker** — full-screen thumbnail grid, server-paginated, with search and location filter; shows "Added" badge on photos already in the album; scrolls to load more; **Add All** button bulk-adds every photo matching the current location filter in a single request
- Add individual photos via the per-card context menu (⋮)
- Remove photos from an album via context menu when inside that album view

### Map
- **⊙ Map view** — geotagged photos and videos plotted on an interactive Leaflet map (loaded from CDN — requires an internet connection for map tiles and the Leaflet/marker-cluster libraries)
- **Clustering** — nearby items group into a cluster marker showing the most recent thumbnail and a count badge; clusters expand as you zoom
- **Cluster panel** — clicking a cluster opens a scrollable side panel of every photo at that location, newest first, lazy-loaded as you scroll
- **Coverage banner** — shows how many of your indexed items have GPS coordinates when it's less than the full library
- Clicking any marker or panel thumbnail opens the full lightbox, with `←`/`→` navigation scoped to that cluster
- Map refreshes automatically after a Sync so newly indexed GPS data appears

### Media Management
- **Hide / unhide** — per-card context menu; hidden items are excluded from all queries by default
- **Full metadata panel** — image: resolution, colour space, orientation, full camera EXIF (make, model, lens, aperture, shutter, ISO, focal length), GPS coordinates, software; video: resolution, codec, duration, FPS

### Settings Panel
Accessible via **⚙ Settings** in the sidebar. Covers all configuration keys — appearance, sorting, performance, media types, sync behaviour, metadata, and server settings. Changes are saved immediately to `data/configuration.json` and most take effect without a restart.

### Locations Manager
Accessible via **⊞ Locations** in the top bar. Add, edit, rename, delete, and toggle visibility of source directories without editing `data/media.json` directly.

### Thumbnail Cache
Thumbnails are generated once on first request and cached to `thumb/` (configurable via `thumbnail_cache_path`). HEIC files and video frames (extracted at 10% of duration via ffmpeg) are cached the same way. Subsequent requests serve from disk instantly.

### Logging
Daily rotating log files are written to `logs/log-YYYY-MM-DD.log` alongside console output. Logs rotate at midnight, retained for 30 days (configurable), all git-ignored.

---

## API Reference

All media endpoints support server-side filtering via query parameters.

### Media

| Method | Path | Description |
|---|---|---|
| GET | `/api/media` | Filtered, sorted, paginated media. Params: `offset`, `limit`, `sort` (`date-desc`\|`date-asc`\|`name`), `format`, `camera`, `location`, `q`, `hidden` (`true`\|`include`) |
| GET | `/api/media/by-id/<uniqueName>` | Single full media record — used by the map panel to fetch details on demand |
| GET | `/api/media/count` | `{total: N}` — fast indexed count |
| GET | `/api/media/formats` | All distinct formats in the database (`SELECT DISTINCT` on indexed column) |
| GET | `/api/media/cameras` | All distinct `Make Model` camera strings |
| GET | `/api/media/locations` | Union of `data/media.json` sources + indexed source roots. Returns `[{root, label}]` |
| GET | `/api/media/subdirs` | All distinct source roots and subdirectories containing indexed files, as a flat tree list. Returns `[{path, source_root, label, depth, is_root}]` — used by the photo picker's location filter |
| GET | `/api/media/gps` | Lightweight list of non-hidden media with GPS coordinates, for the Map view. Returns `{items: [{uniqueName, name, type, lat, lng, date}], total, gps_count}` |
| POST | `/api/media/hide` | `{"uniqueName": "…", "hidden": true\|false}` |

### Database

| Method | Path | Description |
|---|---|---|
| GET | `/api/db` | `{media, albums, total, offset, has_more}` — same filter params as `/api/media`; used by the frontend on initial load |
| POST | `/api/db` | Save `{media, albums}` — replaces both tables in SQLite |

### Config

| Method | Path | Description |
|---|---|---|
| GET | `/api/config` | Returns full `data/configuration.json` |
| POST | `/api/config` | Merges body into `data/configuration.json` (existing keys not in body are preserved) |

### Sync

| Method | Path | Description |
|---|---|---|
| POST | `/api/sync` | Start a background sync of all visible sources. Returns `202 {status: "started"}` immediately, or `409` if a sync is already running |
| GET | `/api/sync/status` | Poll current sync state — `{running, done, scanned, added, total_at_start, current_file, current_source, log (last 50 lines), result, error}` |
| GET | `/api/sync/stream` | Server-Sent Events stream of live sync progress — `connected`, `progress`, `log`, `complete`, `heartbeat` events; closes automatically when sync finishes |

### Albums

| Method | Path | Description |
|---|---|---|
| GET | `/api/albums` | Full albums array |
| POST | `/api/album/create` | `{"name": "…"}` — returns created album with generated `id` |
| POST | `/api/album/add` | `{"albumId": "…", "uniqueName": "…"}` |
| POST | `/api/album/add-bulk` | `{"albumId": "…", "uniqueNames": ["…", …]}` — adds many photos to an album in one transaction. Returns `{ok: true, added: N}` where `N` is the number newly inserted (duplicates skipped) |

### Locations (media.json)

| Method | Path | Description |
|---|---|---|
| GET | `/api/locations` | Returns `data/media.json` entries with `root` and `label` aliases added |
| POST | `/api/locations` | Overwrites `data/media.json` with the submitted array |

### Files

| Method | Path | Description |
|---|---|---|
| GET | `/api/thumb/<id>` | Thumbnail JPEG — disk-cached, size/quality from config by default, overridable per-request with `?size=` and `?quality=` (used by the Map view for small 80px marker thumbnails). Supports images and video frame extraction |
| GET | `/api/image/<id>` | Full-resolution image — HEIC transcoded to JPEG on the fly |
| GET/HEAD | `/api/video/<id>` | Video stream with HTTP 206 range-request support |

---

## Data Files

| File / Directory | Git | Description |
|---|---|---|
| `config/configuration.json` | ✓ | App settings — shipped with the project, tracked in git, edit directly |
| `data/media.json` | ✗ | Source directory list — auto-created with `~/Pictures` entry on first run |
| `data/luminary.db` | ✗ | SQLite database — media records + albums |
| `data/luminary.db-wal` / `-shm` | ✗ | SQLite WAL files — transient, safe to delete when server is stopped |
| `thumb/` | ✗ | Thumbnail cache — auto-created on first run, fully git-ignored |
| `logs/log-YYYY-MM-DD.log` | ✗ | Daily rotating log files |

---

## Troubleshooting

**No photos after Sync**
- Verify paths in `data/media.json` are absolute and the directories exist
- Confirm `"visibility": true` on the desired sources
- Check the terminal or `logs/log-YYYY-MM-DD.log` for scan errors

**Thumbnails not appearing / HEIC images not loading**
- Install `pillow-heif`: `pip install pillow-heif`
- Or install system ImageMagick: `brew install imagemagick` / `apt install imagemagick`
- Verify HEIC support: `python3 -c "import pillow_heif; print('ok')"`

**Videos not playing**
- Install ffmpeg: `brew install ffmpeg` / `apt install ffmpeg`
- `.MOV` files recorded on iPhone with HEVC (H.265) codec will not play in Chrome — Safari supports them natively, or transcode to H.264 MP4

**Video thumbnails not generating**
- ffmpeg must be on `PATH`: `which ffmpeg`

**Filters returning no results**
- Format, Camera, and Location dropdowns are populated from the full database — if they're empty, no media has been synced yet
- After changing `data/media.json`, run Sync to index the new files

**Backend not reachable**
- Check `app.py` is running: `ps aux | grep app.py`
- Check for port conflicts: `lsof -i :5000`
- Change port in `data/configuration.json` → `"api_port"` and restart

**Settings not saving**
- Confirm `app.py` is running — Settings are saved via `POST /api/config` which writes to `config/configuration.json`
- Check file permissions: `ls -l config/configuration.json`