# Luminary — Local Media Gallery

A lightweight, local-first photo and video gallery. Index media from your filesystem, browse with a refined dark UI, organise into albums, view geotagged photos on a map, and manage everything without any cloud dependency, accounts, or tracking.

---

## Requirements

- Python 3.8+
- ffmpeg — for video thumbnails and metadata (`apt install ffmpeg` or `brew install ffmpeg`)
- Internet connection for the **Map** view — Leaflet, the marker-clustering plugin, and OpenStreetMap tiles are loaded from a CDN at runtime. Everything else works fully offline.
- `pystray` (installed via `requirements.txt`) — only used by installed builds, for the system tray icon. Not needed for dev-mode (`./run.sh`). See [System Tray](#system-tray-installed-builds-only).

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

`./run.sh` (dev mode) always runs in your terminal with normal console output, exactly as above — this is unchanged. Only the *installed* build (the packaged `.exe`/`.deb`) runs as a system tray application instead; see [System Tray](#system-tray-installed-builds-only).

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
├── Luminary.spec                 # PyInstaller spec file
├── README.md
├── requirements.txt               # Python dependencies (includes waitress — production WSGI server)
├── run.sh                        # Startup script (dev mode)
├── build/                        # PyInstaller's own intermediate build cache — git-ignored
├── app/
│   ├── build/                    # Packaged app output (see scripts/) — git-ignored
│   │   ├── linux/portable/Luminary/
│   │   │   ├── Luminary
│   │   │   ├── _internal/            # PyInstaller runtime + libraries
│   │   │   ├── resources/            # Bundled frontend (renamed from frontend/)
│   │   │   ├── ffmpeg / ffprobe       # Bundled if available at build time
│   │   │   └── run-luminary.sh        # Launcher that puts ffmpeg on PATH
│   │   ├── linux/deb-pkg/            # .deb staging tree (see scripts/linux/build-deb.sh) — git-ignored
│   │   ├── linux/*.deb               # Built Debian package
│   │   ├── windows/portable/Luminary/
│   │   │   ├── Luminary.exe
│   │   │   ├── _internal/
│   │   │   ├── resources/
│   │   │   └── ffmpeg.exe / ffprobe.exe
│   │   └── windows/installer/        # Built Inno Setup .exe installer
│   └── src/
│       ├── backend/
│       │   ├── app.py                 # Flask backend — API, scanner, metadata extractor
│       │   ├── app_paths.py           # Central path resolver — see "Data Locations" below
│       │   ├── tray.py                # System tray icon — installed builds only, see "System Tray"
│       │   ├── instance_lock.py       # Single-instance guard — see "Single Instance"
│       │   ├── config/                # Dev-mode settings — tracked in git
│       │   │   └── configuration.json # All configurable settings (edit this)
│       │   ├── data/                  # Dev-mode runtime data — auto-created, git-ignored
│       │   │   ├── media.json         # Source directory list
│       │   │   ├── luminary.db        # Media + albums — SQLite database
│       │   │   ├── luminary.db-wal    # SQLite write-ahead log (transient)
│       │   │   └── luminary.db-shm    # SQLite shared memory (transient)
│       │   ├── logs/                  # Dev-mode daily rotating logs — git-ignored
│       │   │   └── log-YYYY-MM-DD.log
│       │   ├── thumbnails/            # Dev-mode thumbnail cache — auto-created, git-ignored
│       │   └── cache/                 # Dev-mode scratch cache (temp video frames) — git-ignored
│       └── frontend/
│           ├── index.html             # Frontend markup
│           └── static/
│               ├── app.js             # Frontend logic
│               └── style.css          # Frontend styles
└── scripts/
    ├── Readme.md                 # What the build scripts do
    ├── build-linux.sh            # Packages a portable Linux build (see BUILD.md)
    ├── build-windows.bat         # Packages a portable Windows build (see BUILD.md)
    ├── windows/
    │   └── Luminary.iss          # Inno Setup script — builds the Windows installer
    └── linux/
        ├── build-deb.sh          # Assembles a .deb from the build-linux.sh output
        ├── usr-bin-luminary      # Launcher installed at /usr/bin/luminary
        ├── luminary.desktop      # Application-menu entry
        ├── luminary.service      # Example systemd --user service for headless installs (Raspberry Pi, etc.)
        └── debian/
            ├── control           # Package metadata template
            ├── postinst          # Fixes permissions, refreshes desktop/icon caches
            └── prerm              # Pre-removal hook (no-op — see comments in the file)
```

On first run `app.py` automatically creates `media.json` with a default entry pointing at `~/Pictures`. `configuration.json` is shipped with the project and tracked in git for dev mode — edit it directly to change settings, or use the in-app Settings panel.

`index.html` is served from `app/src/frontend/`, with `app.js` and `style.css` served from `app/src/frontend/static/` — no build step or bundler required, just edit and refresh.

`Luminary.spec` and the top-level `build/` folder are PyInstaller's own working files, regenerated on every run of the build scripts — see `BUILD.md` and `scripts/Readme.md` for packaging a standalone executable, `.deb`, or Windows installer.

### Data Locations — dev mode vs. installed builds

All of the paths above (`data/`, `config/`, `logs/`, `thumbnails/`, `cache/`) are resolved by `app_paths.py`, and where they actually live depends on how Luminary is running:

- **Dev mode** (`python3 app.py`, or `./run.sh`): everything lives under `app/src/backend/`, exactly as shown in the tree above — same as always, so the normal dev workflow is unchanged.
- **Installed build** (the PyInstaller `.exe`/binary, via the Windows installer or the `.deb`): user data lives in an OS-standard per-user location, *outside* the install directory entirely, so upgrading or reinstalling the app never touches it:
  - Windows: `%LOCALAPPDATA%\Luminary\`
  - Linux: `~/.local/share/Luminary/`

  If you're upgrading from an older build that stored data inside `_internal\`, `app_paths.py` migrates it to the new location automatically, once, the first time you launch the new version — nothing to do manually.

### Single Instance

Luminary refuses to start a second time while an instance is already running — this applies everywhere: two dev runs, two installed tray-app launches, a dev run alongside an installed build, a headless server, any combination. The guard (`instance_lock.py`) is platform-specific:

- **Windows**: a named OS-level mutex (`CreateMutexW`) is the authoritative check. This is deliberately used instead of a file lock — file-locking APIs are filesystem operations that antivirus/EDR software and backup tools sometimes hook or interfere with (locking is exactly the pattern ransomware heuristics watch for), which can make a non-blocking file lock silently succeed for a second process instead of failing. A named mutex is a kernel object, not a file operation, and isn't subject to that. It's released automatically the instant the process exits for any reason, including a crash or `taskkill /F`.
- **Linux/macOS**: an OS-level advisory lock (`fcntl.flock`) on a small `luminary.lock` file in the per-user data directory described above, so it's scoped exactly like the DB/config/logs.

On every platform, whichever instance is running also writes its PID and port to `luminary.lock` in the per-user data directory — this file is informational only (used to tell you who's already running and to redirect your browser), never the source of truth for whether another instance exists. A second dev run against the same checkout is blocked, and a second launch of the same installed build is blocked, but a dev run and an installed build don't interfere with each other since they use different data directories.

If a launch finds another instance already running, it doesn't error out — it logs/prints a note, opens `http://localhost:<port>/` in your default browser (skipped automatically on a headless box with no display), and exits cleanly.

If you deliberately want more than one instance at once (e.g. two dev servers on different `--port` values for parallel testing), pass `--allow-multiple-instances` to skip the check.

### System Tray (installed builds only)

- **Dev mode** (`python3 app.py`, or `./run.sh`): unchanged — runs in your terminal with normal console output, no tray icon.
- **Installed build** (the packaged `.exe`, `.deb`, etc., launched from the Start Menu / application launcher / desktop shortcut, same as any other installed app): runs as a system tray application instead of a visible console window. `tray.py` starts the backend on a background thread and shows a tray icon with:

  | Menu item | Action |
  |---|---|
  | **Open Luminary** | Opens `http://localhost:<port>/` in your default browser (also the default action if you double-click the icon) |
  | **About** | Opens the project's GitHub page |
  | **Start / Stop** | Toggles the backend on/off — label reflects current state |
  | **Quit** | Stops the backend and closes the tray icon, ending the app |

  Nothing auto-starts at login or boot — like any other installed app, it only runs once you launch it yourself.

  If you ever need the old plain-console behaviour from an installed build (e.g. to see log output live while debugging), run the executable with `--no-tray`:
  ```bash
  # Linux
  /opt/luminary/Luminary --no-tray
  # Windows (from a terminal)
  "C:\Program Files\Luminary\Luminary.exe" --no-tray
  ```

  **Linux note:** the tray icon needs a tray/AppIndicator backend from your desktop environment. KDE, XFCE, and most other DEs support it natively; on GNOME you'll need an extension such as [AppIndicator and KStatusNotifierItem Support](https://extensions.gnome.org/extension/615/appindicator-support/). The `.deb` package lists the relevant GTK/AppIndicator libraries as `Recommends` (not hard `Depends`, since they aren't needed on every desktop environment). That's for a desktop that's missing the tray backend but still has a display — for a machine with **no desktop environment at all**, see the next section.

### Headless / Server Mode (Linux, e.g. Raspberry Pi)

An installed build launched on a Linux machine with **no display at all** — no X11, no Wayland, no desktop environment installed — can't show a tray icon; there's nothing for it to attach to. This is the normal situation for a headless Raspberry Pi (or any server) set up over SSH.

Luminary detects this automatically: `tray.py` checks for `DISPLAY`/`WAYLAND_DISPLAY` before attempting the tray, and if neither is set, it logs a note and runs as a plain background server instead — the exact same code path dev mode uses. **No flags or config changes are needed** for this to work; it Just Works on a fresh headless install.

The one thing headless setups do need that a desktop doesn't: something to start Luminary automatically on boot, since there's no Start Menu/launcher to click. An example systemd **user** service is provided at `scripts/linux/luminary.service`:

```bash
mkdir -p ~/.config/systemd/user
cp scripts/linux/luminary.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now luminary.service

# So it also starts on boot without you logging in / opening an SSH session:
sudo loginctl enable-linger $USER
```

Check on it with:
```bash
systemctl --user status luminary.service
journalctl --user -u luminary -f
```

Or skip systemd entirely and just run it directly over SSH for a quick one-off session:
```bash
/usr/bin/luminary   # or python3 app/src/backend/app.py in a dev checkout
```

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
  "style": "classic",
  "font_size": "small",
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
| `theme` | `dark` | `dark` \| `light` \| `system` — light/dark mode |
| `style` | `classic` | `classic` \| `modern` \| `terminal` \| `sunset` \| `nordic` — see below |
| `font_size` | `small` | `small` \| `medium` \| `large` — `small` matches the app's original sizing; `medium` and `large` scale every font size in the UI proportionally (1.125× and 1.25×) while preserving each style's relative type scale |

**Style options**

| Style | Look |
|---|---|
| `classic` | Serif headers (Cormorant Garamond) + mono body (DM Mono), amber accent, sharp 4px corners — the original editorial look |
| `modern` | Sans-serif (Space Grotesk + Inter), blue accent, rounded 12px corners, larger 14px type |
| `terminal` | Monospace console (JetBrains Mono), phosphor-green accent, sharp 2px corners, compact 12px type |
| `sunset` | Serif display (Fraunces) + rounded sans body (Nunito Sans), terracotta accent, generously rounded 16px corners |
| `nordic` | Serif display (Merriweather) + system sans body, muted slate-teal accent, understated 6px corners |

Each style has its own dark and light variant, controlled independently by `theme`.
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
Thumbnails are generated once on first request and cached to `thumbnails/` (configurable via `thumbnail_cache_path`; the historical `thumb` value is still accepted). HEIC files and video frames (extracted at 10% of duration via ffmpeg) are cached the same way. Subsequent requests serve from disk instantly.

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
| GET | `/api/db` | `{media, albums, folders, total, offset, has_more}` — same filter params as `/api/media`; used by the frontend on initial load |
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
| POST | `/api/album/move` | `{"albumId": "…", "folderId": "…"|null}` — moves an album into a folder, or out of any folder if `folderId` is `null` |

### Folders

Folders group albums for display in the sidebar (collapsible tree). A folder can hold any number of albums; an album belongs to at most one folder.

| Method | Path | Description |
|---|---|---|
| GET | `/api/folders` | All folders as `[{id, name}]` |
| POST | `/api/folder/create` | `{"name": "…"}` — returns created folder |
| POST | `/api/folder/rename` | `{"folderId": "…", "name": "…"}` |
| POST | `/api/folder/delete` | `{"folderId": "…", "force": false}` — if the folder still has albums inside and `force` isn't set, returns `409 {needs_confirmation: true, album_count, album_names}` instead of deleting anything. Call again with `force: true` to proceed. Deleting a folder deletes the albums inside it (and their album-membership rows) but never touches the underlying media files/records |

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
| `data/luminary.db` | ✗ | SQLite database — media records + albums + folders |
| `data/luminary.db-wal` / `-shm` | ✗ | SQLite WAL files — transient, safe to delete when server is stopped |
| `thumbnails/` | ✗ | Thumbnail cache — auto-created on first run, fully git-ignored |
| `cache/` | ✗ | Scratch cache for temp video frame extraction — auto-created, fully git-ignored |

In dev mode these paths are relative to `app/src/backend/`, as shown above. In an installed build, they live under `%LOCALAPPDATA%\Luminary\` (Windows) or `~/.local/share/Luminary/` (Linux) instead — see [Data Locations](#data-locations--dev-mode-vs-installed-builds).
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

**Tray icon doesn't appear (installed build)**
- This only applies to installed builds — dev mode (`./run.sh`) never shows a tray icon, that's expected
- Linux: your desktop environment needs tray/AppIndicator support — install it (e.g. on GNOME, the [AppIndicator extension](https://extensions.gnome.org/extension/615/appindicator-support/)) and log out/in, or install the packages listed under `Recommends` in the `.deb` if you installed via `dpkg`/`apt`
- The app itself is still running even without a visible tray icon — check `logs/log-YYYY-MM-DD.log` under the installed-build data directory (see [Data Locations](#data-locations--dev-mode-vs-installed-builds)) and open `http://localhost:5000` directly
- To rule out a tray-specific issue, launch with `--no-tray` (see [System Tray](#system-tray-installed-builds-only)) — if the app works fine that way, the problem is specifically with the tray backend, not Luminary itself

**Two tray icons appear after launching Luminary twice**
- This means the single-instance guard (see [Single Instance](#single-instance)) didn't catch the second launch and should not happen — as of the current `instance_lock.py`, Windows uses a named mutex specifically because file-lock APIs can be silently interfered with by antivirus/EDR/backup software, which was a known cause of this symptom
- Confirm you're on a build that includes this fix: check `logs/log-YYYY-MM-DD.log` — the second launch should log an "already running" line naming the first instance's PID
- If it's still reproducible, check Task Manager for the actual process count (two `Luminary.exe` processes vs. one process somehow drawing two icons) and report which it is

**Headless install (Raspberry Pi, etc.) — is it running as a tray app or not?**
- It shouldn't be — no tray icon is expected on a machine with no display at all; Luminary should auto-detect this and run as a plain background server (see [Headless / Server Mode](#headless--server-mode-linux-eg-raspberry-pi))
- Confirm the auto-detection kicked in: check the log for a line like `No graphical session detected ... running Luminary as a plain background server`
- If it's hanging or erroring instead of falling back, `DISPLAY`/`WAYLAND_DISPLAY` may be set to a stale/invalid value in your shell (common after an old X11-forwarded SSH session) — `echo $DISPLAY` to check, `unset DISPLAY WAYLAND_DISPLAY` to clear it, or just pass `--no-tray` to force plain mode regardless
- Not starting after a reboot? You likely haven't set up the systemd service yet — see `scripts/linux/luminary.service`

**"Luminary is already running" but I don't see it**
- On an installed build with a display, this should have opened `http://localhost:<port>/` in your browser automatically — check for a browser window/tab that already opened
- The message includes the PID and port of the running instance; confirm it's actually alive: `ps -p <PID>` (Linux/macOS) or Task Manager (Windows). If that PID is gone but you still get this message:
  - **Windows**: the guard is a named mutex, not a file, so it's released automatically the instant a process exits — there's nothing to delete by hand. If this happens anyway, it's worth reporting.
  - **Linux/macOS**: the lock file's OS-level `flock` should have released automatically when that process exited — this would be unusual and worth reporting, but as a workaround you can delete the lock file directly (see [Data Locations](#data-locations--dev-mode-vs-installed-builds) for where `luminary.lock` lives) while no instance is running
- Intentionally want two running at once (e.g. two dev servers on different ports)? Pass `--allow-multiple-instances`