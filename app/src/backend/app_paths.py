#!/usr/bin/env python3
"""
app_paths.py — Central path management for Luminary.

This module is the single source of truth for every filesystem location the
app touches. It must be imported and used BEFORE anything else reads or
writes app data — app.py calls ensure_dirs() and migrate_legacy_internal_data()
at the very top of its startup sequence, before _bootstrap() creates any
default files and before logging is configured (logging needs LOGS_DIR to
already exist).

Design summary
──────────────
- Frozen (PyInstaller onedir) build:
    Install dir   = folder containing Luminary.exe — binaries/resources only,
                     safe for an installer to wipe and replace on upgrade.
    User data dir = OS-appropriate per-user location (see get_user_data_root),
                     never touched by the installer, so it survives upgrades.
    On first launch after upgrading from the old layout (where data lived
    inside _internal/), migrate_legacy_internal_data() moves everything over
    exactly once.

- Dev / "python app.py" runs:
    Both install dir and user data dir resolve to paths inside the project
    checkout, exactly as before this refactor — there's no installer in dev
    mode to overwrite anything, so keeping data next to the source is more
    convenient. Set LUMINARY_FORCE_USER_DATA_DIR=1 to opt a dev run into the
    OS-standard user data location anyway (useful for testing migration
    logic without building an .exe).
"""

import os
import sys
import shutil
import platform
from pathlib import Path

APP_NAME = "Luminary"

# Set LUMINARY_FORCE_USER_DATA_DIR=1 to make a `python app.py` dev run use
# the same OS-standard user-data location a frozen build would use, instead
# of the project-relative dev paths. Useful for testing the appdata/migration
# logic without producing a PyInstaller build.
_FORCE_USER_DATA_DIR = os.environ.get("LUMINARY_FORCE_USER_DATA_DIR") == "1"


def is_frozen() -> bool:
    """True when running as a PyInstaller-built executable."""
    return getattr(sys, "frozen", False)


def get_install_dir() -> Path:
    """
    Directory holding the running application binaries.
    - Frozen: the folder containing Luminary.exe (PyInstaller's onedir output).
    - Dev mode: the project root (four levels up from this file, which lives
      at <project_root>/app/src/backend/app_paths.py).
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent.parent


def get_resources_dir() -> Path:
    """
    Read-only bundled resources (the built frontend, icons, etc).
    - Frozen: <install_dir>/resources — kept as a sibling of PyInstaller's own
      _internal/ folder so it's clear which parts are "PyInstaller's runtime
      bundle" vs. "our bundled assets", per the target install layout.
    - Dev mode: <project_root>/app/src/frontend, unchanged from before this
      refactor (this is where frontend_dir already pointed).
    """
    if is_frozen():
        return get_install_dir() / "resources"
    return get_install_dir() / "app" / "src" / "frontend"


def _windows_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / APP_NAME


def _linux_data_dir() -> Path:
    # Respect XDG_DATA_HOME if set (XDG Base Directory spec), else the
    # standard ~/.local/share default.
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_NAME


def _macos_data_dir() -> Path:
    # Not a stated requirement (only Windows/Linux were asked for), but this
    # keeps the app from silently writing into the install dir if it's ever
    # run on macOS rather than failing in a confusing way.
    return Path.home() / "Library" / "Application Support" / APP_NAME


def get_user_data_root() -> Path:
    """
    OS-appropriate per-user data root, used for everything that must survive
    an upgrade: the SQLite DB, config, logs, thumbnail cache.
    In dev mode (and not force-overridden), this resolves inside the project
    checkout instead, preserving pre-refactor behavior.
    """
    if not is_frozen() and not _FORCE_USER_DATA_DIR:
        return get_install_dir() / "app" / "src" / "backend"

    system = platform.system()
    if system == "Windows":
        return _windows_data_dir()
    if system == "Darwin":
        return _macos_data_dir()
    return _linux_data_dir()  # Linux, and any other POSIX-like fallback


# ── resolved paths (computed once at import time) ──────────────────────────
INSTALL_DIR     = get_install_dir()
RESOURCES_DIR   = get_resources_dir()
USER_DATA_ROOT  = get_user_data_root()

DATA_DIR   = USER_DATA_ROOT / "data"
CONFIG_DIR = USER_DATA_ROOT / "config"
LOGS_DIR   = USER_DATA_ROOT / "logs"
THUMB_DIR  = USER_DATA_ROOT / "thumbnails"
CACHE_DIR  = USER_DATA_ROOT / "cache"

_MIGRATION_MARKER = USER_DATA_ROOT / ".migrated_from_internal"


def ensure_dirs() -> None:
    """Create every user-data directory if missing. Cheap — safe to call on every launch."""
    for d in (USER_DATA_ROOT, DATA_DIR, CONFIG_DIR, LOGS_DIR, THUMB_DIR, CACHE_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _move_tree_no_overwrite(src: Path, dst: Path) -> int:
    """
    Move every file from src into dst (recreating the relative subdirectory
    structure), skipping anything that already exists at the destination —
    a file already present at the new location is treated as authoritative
    and never overwritten. Returns the number of files actually moved.
    """
    if not src.is_dir():
        return 0
    moved = 0
    for item in src.rglob("*"):
        if item.is_dir():
            continue
        target = dst / item.relative_to(src)
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(item), str(target))
            moved += 1
        except OSError:
            pass  # leave the source file in place if the move fails; nothing lost
    return moved


def migrate_legacy_internal_data(log=None) -> None:
    """
    One-time migration for upgrades from the old layout, where the DB,
    config, logs, and thumbnails lived inside PyInstaller's _internal/ folder
    and were silently wiped by every reinstall/upgrade.

    Must be called BEFORE _bootstrap() creates default data/config files —
    otherwise a fresh default config.json would already exist at the
    destination by the time migration runs, and the no-overwrite rule above
    would skip restoring the user's real one.

    Only runs:
      - for frozen builds (dev mode never had the _internal problem)
      - once ever, tracked by a marker file in the new user-data root
      - without ever overwriting a file that already exists at the new location
    """
    def _log(msg):
        if log:
            log(msg)
        else:
            print(f"[INFO] {msg}")

    if not is_frozen():
        return
    if _MIGRATION_MARKER.exists():
        return

    ensure_dirs()

    legacy_internal = INSTALL_DIR / "_internal"
    if not legacy_internal.is_dir():
        # Fresh install, nothing to migrate — record that so we never check again.
        _MIGRATION_MARKER.write_text("no legacy _internal directory found\n", encoding="utf-8")
        return

    moved = 0
    moved += _move_tree_no_overwrite(legacy_internal / "data", DATA_DIR)
    moved += _move_tree_no_overwrite(legacy_internal / "config", CONFIG_DIR)
    moved += _move_tree_no_overwrite(legacy_internal / "logs", LOGS_DIR)
    # Old builds may have named this dir either "thumb" or "thumbnails".
    moved += _move_tree_no_overwrite(legacy_internal / "thumbnails", THUMB_DIR)
    moved += _move_tree_no_overwrite(legacy_internal / "thumb", THUMB_DIR)

    _MIGRATION_MARKER.write_text(
        f"migrated {moved} file(s) from {legacy_internal} on first launch of this version\n",
        encoding="utf-8",
    )
    if moved:
        _log(f"Migrated {moved} file(s) from the old _internal/ layout to {USER_DATA_ROOT}")