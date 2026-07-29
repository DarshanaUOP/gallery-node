# Luminary — Local Media Gallery

A lightweight, local-first photo and video gallery. Index media from your filesystem, browse with a refined dark UI, organise into albums, view geotagged photos on a map, and manage everything without any cloud dependency, accounts, or tracking.

---

## Requirements

- Python 3.8+
- ffmpeg — for video thumbnails and metadata (`apt install ffmpeg` or `brew install ffmpeg`)
- Internet connection for the **Map** view — Leaflet, the marker-clustering plugin, and OpenStreetMap tiles are loaded from a CDN at runtime. Everything else, including the lightbox's Video.js video player (bundled locally, no CDN), works fully offline.
- `pystray` (installed via `requirements.txt`) — only used by installed builds, for the system tray icon. On Linux this is the fallback backend; a native GTK + AppIndicator backend is tried first, see [System Tray](#system-tray-installed-builds-only). Not needed for dev-mode (`./run.sh`).
- `psutil` (installed via `requirements.txt`, optional) — powers the CPU/memory rows on the tray's [Dashboard](#dashboard-installed-builds-only). Everything else on the Dashboard works fine without it.

---

## Installation

```bash
# 1. Clone / download the project folder
cd luminary

# 2. Install Python dependencies
pip install -r requirements.txt
```

`requirements.txt` includes `pillow-heif` for HEIC/HEIF support. It's optional but strongly recommended — without it, Luminary falls back to system ImageMagick or ffmpeg for HEIC decoding.

**Linux only**, for the system tray icon on installed builds (skip if you only run dev mode via `./run.sh`, or if you're on Windows/macOS): install the AppIndicator bindings so `tray.py` can use its native backend instead of falling back to pystray's less reliable one (see [System Tray](#system-tray-installed-builds-only)):

```bash
# Ubuntu 20.04 and similar-vintage distros:
sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-appindicator3-0.1

# Ubuntu 22.04+ and most current distros (note the different package name):
sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1
```

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
│               ├── style.css          # Frontend styles
│               └── vendor/
│                   └── videojs/          # Video.js, bundled locally — no CDN dependency
│                       ├── video.min.js
│                       └── video-js.min.css
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
  | **Dashboard** | Opens a local status window — see [Dashboard](#dashboard-installed-builds-only) |
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

  **Linux note:** on Linux, `tray.py` tries a native GTK + AppIndicator backend first and only falls back to pystray if that's unavailable — this avoids a real gap where pystray's own backend looks for one specific `AppIndicator` gi typelib name and some distros only ship the other one. Either way, the tray icon still needs a tray/AppIndicator backend from your desktop environment to actually be visible. KDE, XFCE, and most other DEs support it natively; on GNOME you'll need an extension such as [AppIndicator and KStatusNotifierItem Support](https://extensions.gnome.org/extension/615/appindicator-support/). For the native backend specifically, you'll also need `python3-gi`, GTK 3, and one of:
  - `gir1.2-appindicator3-0.1` (Ubuntu 20.04 and similar-vintage distros), or
  - `gir1.2-ayatanaappindicator3-0.1` (Ubuntu 22.04+, most current distros)

  The `.deb` package lists these as `Recommends` (not hard `Depends`, since they aren't needed on every desktop environment — pystray remains a working fallback). That's for a desktop that's missing the tray backend but still has a display — for a machine with **no desktop environment at all**, see the next section.

  **Building/running from source on Linux — getting the native backend to actually import:** the `gir1.2-*` packages above provide the AppIndicator library itself, but the native backend also needs Python's `gi` bindings (PyGObject) installed *inside the same virtual environment Luminary runs in*. A system-wide `python3-gi`/`apt install`-provided `gi` isn't visible to a venv created without `--system-site-packages` — `venv-linux` (the one `build-linux.sh`/`run.sh` use) needs its own copy. If the native backend silently falls back to pystray (or you see an `ImportError`/`ModuleNotFoundError` for `gi` in the logs) even after installing the packages above, build PyGObject into the venv:

  ```bash
  sudo apt install libgirepository1.0-dev gcc libcairo2-dev pkg-config python3-dev
  source venv-linux/bin/activate
  pip install PyGObject
  ```

  The `apt install` line pulls in the headers and compiler PyGObject's build needs — it compiles a C extension against GTK's introspection data, so it isn't a pure-Python wheel and can't just be `pip install`ed on its own. Do this once before running `build-linux.sh` (or before `./run.sh` in dev mode) if the native tray backend isn't being picked up; `pip install -r requirements.txt` alone won't get you there without the system packages first.

### Dashboard (installed builds only)

Click **Dashboard** in the tray menu to open a small local status window (`app/src/backend/dashbord.py`). It's a plain Tkinter window with two tabs, auto-refreshing every 5 seconds (or on demand via **Refresh Now**):

- **Home** — server status/monitoring: running or stopped, the address it's serving on, uptime, process ID, CPU usage, memory usage, thread count, and whether a background sync is currently running (plus the result of the last one this session).
- **Media** — indexed-media stats: total count, image/video breakdown, hidden count, how many distinct formats and cameras have been seen, album/folder counts, on-disk thumbnail cache size, and a per-location breakdown of how many items are indexed under each configured source.

The Dashboard reads everything through Luminary's own REST API (the same endpoints the web frontend uses — see [API Reference](#api-reference)), so it always reflects exactly what the running server would show, and never opens the SQLite database directly. CPU/memory rows need `psutil` (see [Requirements](#requirements)); everything else on the Dashboard works without it.

Only one Dashboard window opens at a time — clicking the menu item again while it's already open just brings the existing window to front instead of opening a second one.

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

If a configured source directory can't be found (moved/renamed/unmounted), or files that were previously indexed under a directory that *is* still reachable have gone missing, Luminary no longer deletes those records. Instead it raises a notification (🔔 in the top bar) with a **Relocate** action — see [Notifications](#notifications) below. The sync summary counter that used to read "Removed" now reads **Missing** for the same reason.

---

## Features

### Gallery
- **CSS Grid layout** — 4 columns desktop, scales down to 2 on mobile; column count and card height configurable in Settings
- **Server-side pagination** — first page loads immediately; subsequent pages are fetched on scroll via the IntersectionObserver, one page at a time
- **Scroll date indicator** — floating pill shows the date range of currently visible cards while scrolling
- **Subdirectory path badge** — shows which subfolder a file came from, relative to its source root

### Filtering & Sorting
- **Sort** — Newest First / Oldest First (by modified date, falling back to created) / Name; sorting is server-side so all records are sorted correctly regardless of which page has loaded
- **Group by — All / Years / Months** — chips in the filter bar, available in **All Photos** and **Hidden** (not in albums or Map). *Years* shows one stack card per year; clicking a year drills into that year's *Months*, in the same stack-card style. A *Months* chip is also available directly, showing month stacks across every year. Each stack card shows the year/month label, the photo count, and up to 4 recent thumbnails fanned like a deck — server-computed via `/api/media/groups`, so only those preview thumbnails are fetched, never the full bucket. Clicking a month card opens its photos in the normal lazily-loaded flat grid, scoped to that month; a breadcrumb (`‹ Years / 2024 / March 2024`) navigates back up. Items with no detectable date aren't shown in Years/Months (they're still visible under All)
- **Filter dropdowns** — Format, Camera, Location; populated from the full database on load via dedicated indexed API endpoints, not from the currently loaded page
- **Date range filter** — From/To date pickers in the filter bar restrict results to media dated within the selected (inclusive) range; ✕ clears it
- **Active-state highlighting** — any filter dropdown or the date range gets a persistent accent border/glow the moment it holds a non-default value, and keeps it — regardless of what else you click on the page — until it's set back to its default ("All Formats"/"All Cameras"/"All Locations", or the date range is cleared). Makes it obvious at a glance which filters are currently narrowing the results
- **Search** — full-text across filename, camera make/model, and date
- All filters are applied server-side; each filter change re-fetches from the server at offset 0

### Media Viewer
- **Two-column lightbox** — media fills the left pane; filename, navigation, zoom controls, and full metadata panel are in the right panel
- **Progressive image loading** — blurred thumbnail shown immediately while full resolution loads, replaced with a smooth fade
- **Zoom** — mouse wheel, +/− buttons, 1:1 actual size, fit-to-screen, drag to pan, pinch-to-zoom on touch, double-click to toggle; keyboard: `+` `-` `0` `1`
- **Keyboard navigation** — `←` `→` to navigate, `Esc` to close
- **Video player** — [Video.js](https://videojs.com/), bundled locally in `static/vendor/videojs/` (no CDN, works fully offline), wrapping HTTP range-request streaming so the browser loads only what it needs; seeking works without downloading the full file
- **Missing video detection** — before handing a video's URL to the player, the lightbox does a single `HEAD` request to confirm the file still exists. If it doesn't, a "File not found" message is shown immediately and the player is never mounted — this avoids the browser's own media engine repeatedly probing `/api/video/<id>` on its own (its normal retry/range-probing behaviour against a file that's already known to be missing)
- **HEIC/HEIF** — decoded on the fly via pillow-heif → pyheif → ImageMagick → ffmpeg fallback chain
- **HEVC/H.265 auto-transcode** — `/api/video/<id>` detects HEVC sources (common in iPhone recordings) via each item's already-extracted codec metadata and transcodes them server-side to H.264/AAC MP4 on first playback, caching the result so it's a one-time cost per file — fixes playback in Firefox, which has no HEVC decoder, and browsers with inconsistent hardware-decoder support. Falls back to serving the original file if ffmpeg is unavailable or the transcode fails
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

Each row shows a label field with a **✕ Remove** button beside it, and below that the path field with exactly one context-dependent action button beside it, in priority order:

- **Relocate** (red row) — the location's own configured path can no longer be found on disk at all (folder moved, renamed, or unmounted). Pick the folder's new location and every already-indexed record under it is repointed in place — no re-scan/re-hash needed.
- **Resolve** (red row, red button) — the location's own path is still fine, but one or more of its already-indexed *subfolders* can no longer be found. This is the case a plain "does the location's path still exist?" check misses entirely: e.g. location `/photos` contains subfolders `2023/` and `2024/` — renaming `2023` to `2023-old` leaves `/photos` perfectly valid (so the row would otherwise never turn red) while every file that was under `2023/` quietly becomes unreachable. Clicking **Resolve** opens a popup listing each affected subfolder with **Relocate** / **Ignore** actions. Subfolders that went missing *together* (e.g. renaming a parent folder that itself contains further nested subfolders) are automatically grouped into a single entry — relocating the shared parent once relinks every subfolder beneath it in one pass, as long as the corresponding path actually exists at the new location.
- **Rescan** (cyan button, default/steady state) — shown once nothing needs fixing. Checks for folders on disk under this location that exist but were never indexed at all (newly added content, or the new name a renamed folder landed under), and offers to index just those folders — without re-scanning the entire location.

### Notifications
The 🔔 bell icon in the top bar shows a badge with the unread count and opens a panel listing recent notifications, newest/unread first. Currently raised for:
- **Location unreachable** — a configured source directory couldn't be found during Sync, or while browsing to an item stored under it. Action: **Relocate**, which opens the Locations Manager with the affected row highlighted.
- **File(s) not found** — an individual file (or a batch of them, aggregated into one notification) is missing even though its source directory is still reachable. Action: **View Location**.

In both cases the underlying database records are kept, not deleted — relocating or fixing the folder restores access without losing history. Click a notification's action button to jump to and resolve it, the ✕ to dismiss without acting, or **Mark all read** to clear the badge. Notifications are polled every 30 seconds.

### Thumbnail Cache
Thumbnails are generated once on first request and cached to the fixed `thumbnails/` location under the user data directory. HEIC files and video frames (extracted at 10% of duration via ffmpeg) are cached the same way. Subsequent requests serve from disk instantly.

### Logging
Daily rotating log files are written to `logs/log-YYYY-MM-DD.log` alongside console output. Logs rotate at midnight, retained for 30 days (configurable), all git-ignored.

---

## API Reference

All media endpoints support server-side filtering via query parameters.

### Media

| Method | Path | Description |
|---|---|---|
| GET | `/api/media` | Filtered, sorted, paginated media. Params: `offset`, `limit`, `sort` (`date-desc`\|`date-asc`\|`name`), `format`, `camera`, `location`, `q`, `hidden` (`true`\|`include`), `dateFrom`/`dateTo` (`YYYY-MM-DD`, inclusive) |
| GET | `/api/media/groups` | Year/month buckets for the gallery's grouping view. Params: `groupBy` (`year`\|`month`, required), `year` (`YYYY`, optional — restricts `groupBy=month` to that year's months), plus the same `format`/`camera`/`location`/`q`/`hidden` filters as `/api/media`. Items with no known date are excluded. Returns `{group_by, groups: [{key, count, preview}]}` — `key` is `"YYYY"` or `"YYYY-MM"`, `preview` is up to 4 of the bucket's most recent media records (not the full bucket) |
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
| GET | `/api/sync/status` | Poll current sync state — `{running, done, scanned, added, total_at_start, current_file, current_source, log (last 50 lines), result, error}`. `result.missing` is the count of files that couldn't be found on disk this run (they're kept and notified about, not deleted — see [Notifications](#notifications)) |
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
| GET | `/api/locations` | Returns `data/media.json` entries with `root`/`label` aliases, `synced_count` (indexed rows under that path), `exists` (whether the path currently resolves to a real directory on disk), and `missing_subfolder_count` (how many of this — otherwise valid — location's indexed subfolders can no longer be found on disk; only computed when `exists` is true) added |
| POST | `/api/locations` | Overwrites `data/media.json` with the submitted array |
| POST | `/api/location/delete` | `{"path": "…"}` — removes the location from `data/media.json` and discards every indexed record under it (and their cached thumbnails). Original files on disk are never touched. Returns `{ok, deleted}` |
| POST | `/api/location/relocate` | `{"old_path": "…", "new_path": "…"}` — repoints every indexed record under `old_path` to `new_path` in place (source root, file path, and embedded metadata) and updates the matching `data/media.json` entry, without needing a re-scan. Also clears any outstanding notifications for `old_path`. Returns `{ok, updated, old_root, new_root}` |
| POST | `/api/location/resolve` | `{"path": "…"}` — read-only diagnostic scan for one location: compares what's indexed against what's actually on disk, one directory at a time, to catch subfolder-level drift a plain "does the location's path exist?" check can't see. Indexed subfolders that are missing on disk are grouped by their topmost missing ancestor (so a parent-folder rename that took several indexed subfolders with it shows up as one actionable item, not one per affected leaf). Returns `{root, missing: [{path, rel, count, leaf_count}], new: [{path, rel, count}]}` |
| POST | `/api/location/relocate-subfolder` | `{"old_path": "…", "new_path": "…"}` — subfolder-scoped counterpart to `/api/location/relocate`, used when a subfolder was renamed/moved but its parent location's own path is still valid. Matches rows by `file_path` prefix rather than `source_root`, so pointing it at a shared missing ancestor relinks every subfolder beneath it in one call — each row is still individually verified with a file-existence check before being relinked. Returns `{ok, updated, still_missing, old_path, new_path}` |
| POST | `/api/location/rescan-subfolder` | `{"root": "…", "path": "…"}` — indexes new media found under one subfolder of a configured location, without re-walking the whole location the way a full Sync would. `root` is the location's configured path; `path` is the specific subfolder to scan (as returned by `/api/location/resolve`'s `new` list). Returns `{ok, scanned, added}` |

### Notifications

| Method | Path | Description |
|---|---|---|
| GET | `/api/notifications` | `{items: [{id, type, title, message, location_path, unique_name, action, action_label, is_read, created_at}], unread_count}` — most recent first, unread bubbled to the top |
| POST | `/api/notifications/<id>/read` | Marks one notification as read. Returns `{ok, unread_count}` |
| POST | `/api/notifications/read-all` | Marks every notification as read. Returns `{ok, updated, unread_count: 0}` |

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
| `data/luminary.db` | ✗ | SQLite database — media records + albums + folders + notifications |
| `data/luminary.db-wal` / `-shm` | ✗ | SQLite WAL files — transient, safe to delete when server is stopped |
| `thumbnails/` | ✗ | Thumbnail cache — auto-created on first run, fully git-ignored |
| `cache/` | ✗ | Scratch cache for temp video frame extraction, plus transcoded HEVC→H.264 videos (see [Requirements](#requirements)/Troubleshooting) — auto-created, fully git-ignored, covered by Settings' cache size/clear UI |

In dev mode these paths are relative to `app/src/backend/`, as shown above. In an installed build, they live under `%LOCALAPPDATA%\Luminary\` (Windows) or `~/.local/share/Luminary/` (Linux) instead — see [Data Locations](#data-locations--dev-mode-vs-installed-builds).
| `logs/log-YYYY-MM-DD.log` | ✗ | Daily rotating log files |

---

## Troubleshooting

**No photos after Sync**
- Verify paths in `data/media.json` are absolute and the directories exist
- Confirm `"visibility": true` on the desired sources
- Check the terminal or `logs/log-YYYY-MM-DD.log` for scan errors

**Photos disappeared / lightbox shows a broken image after moving a folder**
- This is expected now — Luminary no longer deletes indexed records when a source file or folder can't be found. Check the 🔔 bell icon in the top bar for a notification with a **Relocate** / **View Location** action
- Clicking the action opens the Locations Manager with the affected row shown in red; use its **Relocate** button to point Luminary at the folder's new path — already-indexed records are repointed in place, no re-sync needed
- If the row isn't red but a file is still missing, check whether it's a *subfolder* that moved rather than the location itself — e.g. renaming `/photos/2023` to `/photos/2023-old` leaves `/photos` perfectly valid, so the row stays green even though everything under `2023/` is now unreachable. A red **Resolve** button appears next to the path field in exactly this situation; click it to relink the affected subfolder(s)
- If neither applies, the folder itself is reachable and only that specific file is gone (deleted, or renamed outside Luminary) — check it manually

**A video keeps showing a loading spinner, or the server logs repeated `/api/video/<id>` requests for a file that no longer exists**
- Fixed as of the current `app.js` — the lightbox does one `HEAD` request to confirm a video file still exists before ever mounting the Video.js player against its URL. If missing, "File not found" is shown immediately with no further requests. Previously, handing a missing file straight to `<video>` let the browser's own media engine retry/range-probe the URL repeatedly on its own before finally giving up

**Thumbnails not appearing / HEIC images not loading**
- Install `pillow-heif`: `pip install pillow-heif`
- Or install system ImageMagick: `brew install imagemagick` / `apt install imagemagick`
- Verify HEIC support: `python3 -c "import pillow_heif; print('ok')"`

**Videos not playing**
- Install ffmpeg: `brew install ffmpeg` / `apt install ffmpeg`
- `.MOV`/`.MP4` files recorded on iPhone with HEVC (H.265) codec — Firefox ships no HEVC decoder at all, and Chrome's support depends on OS/hardware decoding, so it's inconsistent there too. As of the current `app.py`, `/api/video/<id>` detects HEVC sources (from the codec already recorded in each item's metadata) and automatically transcodes them to H.264/AAC MP4 on first playback request, caching the result under the cache directory shown in Settings so it only happens once per file. **First playback of an HEVC video may take a while** (large files can take minutes) while the transcode runs — this is a known limitation; a background/pre-emptive transcode-on-sync isn't implemented yet
- If ffmpeg isn't installed, the HEVC transcode silently fails and the original file is served as before — same "browser can't play this format" fallback message you'd have seen previously
- If *no* video ever shows controls (blank black box, no player UI at all), the Video.js vendor files probably weren't copied alongside `app.js`/`style.css` — confirm `app/src/frontend/static/vendor/videojs/video.min.js` and `video-js.min.css` exist and that `index.html`'s two `static/vendor/videojs/...` tags load without 404s (check the browser console/Network tab)

**Video thumbnails not generating**
- ffmpeg must be on `PATH`: `which ffmpeg`

**Terminal window flashes open/closed repeatedly while generating video thumbnails (Windows)**
- Every `ffmpeg`/`ffprobe` call is a console-subsystem process, so Windows opens a console for each one unless told not to — this showed up as a visible terminal flash per video thumbnail
- Fixed as of the current `app.py`, which passes `creationflags=subprocess.CREATE_NO_WINDOW` on every `subprocess.run()` call into `ffmpeg`/`ffprobe`/ImageMagick; confirm you're on a build that includes this fix if you still see it
- No effect on Linux/macOS — the flag is Windows-only and is skipped entirely on other platforms

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
- Linux, specifically on Ubuntu 20.04 or similar: `tray.py` now tries a native AppIndicator backend before falling back to pystray, specifically because pystray's own backend can silently fail to find the right typelib on some distro/version combinations — check `logs/log-YYYY-MM-DD.log` for a line noting whether it fell back to pystray and why
- Building/running from source and the native backend isn't being used even with the `gir1.2-*` packages installed system-wide? It also needs PyGObject built *inside* `venv-linux` — see the "Building/running from source on Linux" note under [System Tray](#system-tray-installed-builds-only):
  ```bash
  sudo apt install libgirepository1.0-dev gcc libcairo2-dev pkg-config python3-dev
  source venv-linux/bin/activate
  pip install PyGObject
  ```
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