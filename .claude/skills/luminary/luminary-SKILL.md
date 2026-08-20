---
name: luminary
description: Reference this whenever working on Luminary, a local-first Flask + SQLite + vanilla-JS photo/video gallery (backend app.py, frontend index.html/app.js/style.css, tray.py, dashbord.py, app_paths.py, instance_lock.py). Use it for any feature work, bug fix, schema change, or packaging question touching this codebase — adding albums/folders/media fields, sidebar or settings UI changes, sync/scanning behavior, notifications, single-instance/tray/dashboard/headless behavior, or the PyInstaller/.deb/Windows-installer build. Trigger even when the user just says "the gallery app", "the photo app", or pastes one of its files without naming the project — the file layout and code style below are distinctive enough to recognize it.
---

# Luminary

A lightweight, local-first photo/video gallery: index media from the filesystem, browse in a dark-themed vanilla-JS frontend, organize into albums/folders, view geotagged photos on a map — no cloud, no accounts, no tracking. Runs as `python3 app.py` in dev mode, or as a packaged tray app (Windows `.exe`/installer, Linux `.deb`/portable) built with PyInstaller.

Read this before touching the codebase so changes match existing conventions instead of introducing a second way of doing something the codebase already does one way.

## Stack & non-negotiables

- **Backend**: single-file Flask app (`app.py`), SQLite (not JSON) for media/albums/folders/notifications, stdlib `sqlite3` with WAL mode — no ORM.
- **Frontend**: plain `index.html` + `app.js` + `style.css`, no framework, no bundler, no build step. Every JS function lives in global scope in one file; edit and refresh, nothing to compile. Don't introduce React/Vue/webpack/npm — it would break the "no build step" property the project is built around.
- **Packaging**: PyInstaller (onedir) → portable folder → `.deb` (Linux) / Inno Setup installer (Windows). `tray.py` (pystray, or native AppIndicator on Linux) wraps the Flask server for installed builds only; dev mode never shows a tray icon.
- **Zero cloud dependency** except the Map view, which loads Leaflet + OSM tiles from a CDN — that's the one deliberate exception; don't add other external service calls without flagging it, since "no cloud dependency" is a stated product property, not an accident.

## Project layout (dev mode)

```
app/src/backend/
  app.py            # Flask app: schema, all API routes, sync/scan logic, EXIF/ffprobe extraction
  app_paths.py       # single source of truth for every filesystem path (see below) — import first
  instance_lock.py   # single-instance guard (mutex on Windows, flock on POSIX)
  tray.py             # system tray, installed builds only
  dashbord.py         # Tkinter status window, opened from the tray, reads only via the HTTP API
  config/configuration.json   # tracked in git — default settings
  data/               # git-ignored: media.json (source dirs), luminary.db(+ -wal/-shm)
  logs/, thumbnails/, cache/  # git-ignored, auto-created
app/src/frontend/
  index.html
  static/app.js, static/style.css
  static/vendor/videojs/     # bundled locally, no CDN
scripts/            # build-linux.sh, build-windows.bat, .deb/.iss packaging
```

In an **installed build**, `data/`, `config/`, `logs/`, `thumbnails/`, `cache/` move to an OS-standard per-user location (`%LOCALAPPDATA%\Luminary\` / `~/.local/share/Luminary/`) instead of living next to the binary, so upgrades never wipe user data — see `app_paths.py`. Dev mode keeps everything under `app/src/backend/` for convenience. Set `LUMINARY_FORCE_USER_DATA_DIR=1` to make a dev run use the OS-standard location too (useful for testing migration logic without a full PyInstaller build).

## Backend conventions (`app.py`)

- **Routes**: `/api/<noun>` or `/api/<noun>/<action>`, e.g. `/api/album/create`, `/api/location/relocate-subfolder`. Mutations are POST with a JSON body; GETs take query params, never a body.
- **Data-access functions** are plain module-level functions above the route definitions, named `load_x()` / `save_x()` / `create_x()` for the three tables that still look JSON-array-shaped to callers (`load_albums`, `save_albums`, `create_album`, `load_folders`, `create_folder`, `load_media`, `save_media`) — routes call these rather than writing raw SQL inline. Follow this pattern for any new similar entity instead of adding ad hoc SQL in the route handler.
- **Concurrency**: `_db_lock` (a `threading.Lock`) serializes all writes; `_local` gives each thread its own SQLite connection since WAL mode allows concurrent reads. Wrap new write paths in `with _db_lock:` the way `save_albums`/`upsert_media_rows` do.
- **Schema changes**: tables are created with `CREATE TABLE IF NOT EXISTS` in `_init_schema()`, but *new columns on an existing table* are added the post-hoc way already used for `folder_id` and `created_at` — check `PRAGMA table_info(<table>)`, `ALTER TABLE ... ADD COLUMN` if missing, then backfill existing rows in a separate `UPDATE`. This is deliberate, not sloppy: SQLite's `ALTER TABLE` only accepts a constant `DEFAULT`, so a non-constant default (like "now") has to be a follow-up `UPDATE`, and doing it this way means upgrading installs pick up the new column without losing any existing data. Do NOT drop/recreate tables to add a column.
- **Timestamps**: use the existing `_now_str()` helper (`YYYY-MM-DD HH:MM:SS`, local time, lexicographically sortable) rather than `datetime('now')` in SQL (that's UTC) or a raw `datetime.now()` call, so every stamped field in the app sorts and displays consistently.
- **Full-replace save functions** (`save_albums`, `save_media`) delete-and-reinsert everything each call. When adding a new field to one of these dicts, make sure the save function preserves an existing value from the incoming dict (`a.get("created_at") or _now_str()`) rather than unconditionally stamping a fresh one — otherwise a value silently resets to "now" on every unrelated save that round-trips through this path.
- **Config**: `load_config()` returns a dict of hardcoded defaults merged with whatever's in `config/configuration.json`. To add a new setting, just add a `"key": default` line there — the existing generic `GET /api/config` / `POST /api/config` (merge-and-persist) routes handle it automatically; no new endpoint needed unless the setting needs its own validation.
- **Never delete indexed records just because a file went missing.** This was a deliberate design change (see README's Notifications/Troubleshooting sections) — missing files/locations raise a `notifications` row and get surfaced with a Relocate/Resolve action instead. Only an explicit "this location isn't configured at all anymore" sync step actually purges rows.
- **Optional dependencies degrade gracefully**, never hard-crash: `pillow-heif`/ImageMagick/ffmpeg fallback chains for HEIC, `psutil` optional on the dashboard, `pystray` vs. native AppIndicator on Linux. Follow the same try/except-and-degrade pattern rather than making a new optional import a hard requirement.

## Frontend conventions (`app.js` / `index.html` / `style.css`)

- Two module-level state objects: `db` (`{media, albums, folders, ...}`, loaded from `/api/db`) and `config` (from `/api/config`). Read/write these directly; there's no state-management library.
- API calls go through `API_BASE` + fetch; look at an existing similar call (e.g. `setAlbumNavSort`'s optimistic-update-with-revert pattern, or `toggleThemeMode`) before adding a new settings toggle — most "instant" UI preferences follow the same shape: update local state immediately, POST to `/api/config`, revert + toast on failure.
- New sidebar/settings controls should reuse existing CSS custom properties (`--surface`, `--text`, `--text-dim`, `--accent`, etc., themed per `style`/`theme` config) rather than hardcoding colors, so they render correctly across all five visual styles (`classic`/`modern`/`terminal`/`sunset`/`nordic`) × dark/light.
- No CDN dependencies except Leaflet (Map view only) and Video.js is bundled locally under `static/vendor/videojs/` specifically to avoid a CDN dependency for core playback — don't add a new CDN `<script>` for something that could ship locally instead.

## Where to make a given kind of change

| Change | Where |
|---|---|
| New API endpoint / DB field / sync behavior | `app.py` |
| New sidebar control, gallery/lightbox/map UI, settings field | `index.html` (markup) + `app.js` (logic) + `style.css` |
| Path resolution, dev-vs-installed data location | `app_paths.py` |
| Tray menu items, tray backend selection | `tray.py` |
| Status window content | `dashbord.py` (reads only via the HTTP API — never opens the SQLite file directly; keep it that way for any dashboard addition) |
| Single-instance behavior | `instance_lock.py` |
| Packaging/build steps | `scripts/`, `Luminary.spec`, `README.md`'s Project Structure section |

## Testing changes without a full app run

There's no test suite; changes are typically smoke-tested in isolation. For backend changes, the pattern used successfully before: import `app.py` in a throwaway script with `LUMINARY_FORCE_USER_DATA_DIR=1` and `XDG_DATA_HOME`/`LOCALAPPDATA` pointed at a `tempfile.mkdtemp()`, which exercises `_init_schema()`/migration against a real (empty or hand-seeded) SQLite file without touching the developer's actual data. For a schema-migration change specifically, also seed a stub DB with the *old* schema first and confirm `_init_schema()` upgrades it without data loss — that's the scenario upgrades hit in the wild.

## Keep the README in sync

`README.md` is long and carefully cross-referenced (Configuration, Features, API Reference, Troubleshooting sections all describe the same surface from different angles). Any change that adds a config key, API field/endpoint, or user-facing behavior should update the corresponding table/bullet in each relevant section, not just one of them — check `grep -n` for the field/endpoint name across the file to find every place it's mentioned before considering a README update done.
