#!/usr/bin/env python3
"""
dashbord.py — Local status dashboard for Luminary.

Opened from the tray icon's "Dashboard" menu item (see tray.py). Shows two
tabs:

  Home  — server status/monitoring: running/stopped, address, uptime, PID,
          process CPU/memory (if psutil is installed), thread count, and
          the current background-sync state.
  Media — media stats: total indexed items, image/video breakdown, hidden
          count, distinct formats/cameras, per-location counts, album/folder
          counts, and on-disk thumbnail cache size.

Design notes
────────────
- Built with Tkinter (stdlib — no new required dependency for the UI itself).
- All media/server data is read through Luminary's own REST API
  (http://localhost:<port>/api/...) rather than importing app.py or opening
  the SQLite file directly. This keeps a single code path for reading data
  (the same one the web frontend uses) and avoids a second writer/reader
  touching the DB or its thread-local connections from a different module.
- CPU%/RSS memory readings use the optional `psutil` package. If it isn't
  installed, those two rows show "psutil not installed" instead of blocking
  the rest of the dashboard from working — same graceful-degradation pattern
  used throughout app.py/tray.py for optional imports.
- Every network/psutil read happens on a short-lived background thread and
  is handed back to the Tk main loop through a plain queue.Queue; the UI
  thread only ever does non-blocking queue reads, so a slow/unreachable
  server can never freeze the window.
- Threading model: pystray/AppIndicator already own the process's main
  thread with their own event loop (see tray.py's module docstring), so this
  window runs its own Tk root + mainloop on a dedicated background thread.
  Tkinter is not officially thread-safe, and on macOS Tk (like pystray)
  generally wants the main thread — this is a known, accepted limitation of
  running a second GUI toolkit alongside the tray icon, not something this
  module tries to fully solve. In practice this works fine on Windows and
  Linux, which is what installed builds mostly target; treat macOS as
  best-effort.
"""

import json
import logging
import os
import queue
import threading
import time
import urllib.error
import urllib.request
from datetime import timedelta

import tkinter as tk
from tkinter import ttk

log = logging.getLogger("luminary.dashbord")

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    log.info("psutil not installed — Dashboard will skip CPU/memory stats. "
              "Run: pip install psutil")

REFRESH_INTERVAL_MS = 5000   # auto-refresh cadence
QUEUE_POLL_MS       = 200    # how often the UI thread checks for new data
API_TIMEOUT_SECONDS  = 3     # per-request timeout so a stalled server can't hang a fetch


# ── data fetching (runs on background threads only) ─────────────────────────

def _api_get(port: int, path: str):
    """
    Best-effort GET against the local Luminary API. Returns the parsed JSON
    body, or None on any failure (server stopped, timeout, bad JSON, etc.) —
    callers treat None as "unknown" rather than raising, since the dashboard
    should never blow up just because the backend happens to be stopped.
    """
    url = f"http://localhost:{port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=API_TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


_psutil_proc = psutil.Process(os.getpid()) if PSUTIL_AVAILABLE else None
if _psutil_proc is not None:
    _psutil_proc.cpu_percent(interval=None)  # prime it — first call is always 0.0


def _process_stats():
    """CPU%/RSS/thread-count for this process. None fields if psutil is missing."""
    if _psutil_proc is None:
        return {"cpu_percent": None, "rss_mb": None}
    try:
        return {
            "cpu_percent": _psutil_proc.cpu_percent(interval=None),
            "rss_mb":      round(_psutil_proc.memory_info().rss / (1024 * 1024), 1),
        }
    except Exception:
        return {"cpu_percent": None, "rss_mb": None}


def _gather_home_data(controller, port: int) -> dict:
    running = controller.running
    uptime  = controller.uptime_seconds
    sync    = _api_get(port, "/api/sync/status") or {}
    stats   = _process_stats()
    return {
        "running":      running,
        "port":         port,
        "pid":          os.getpid(),
        "uptime":       uptime,
        "thread_count": threading.active_count(),
        "cpu_percent":  stats["cpu_percent"],
        "rss_mb":       stats["rss_mb"],
        "sync_running": sync.get("running", False),
        "sync_scanned": sync.get("scanned"),
        "sync_added":   sync.get("added"),
        "sync_result":  sync.get("result"),
        "sync_error":   sync.get("error"),
    }


def _gather_media_data(port: int) -> dict:
    stats     = _api_get(port, "/api/media/stats")
    formats   = _api_get(port, "/api/media/formats")
    cameras   = _api_get(port, "/api/media/cameras")
    locations = _api_get(port, "/api/locations")
    albums    = _api_get(port, "/api/albums")
    folders   = _api_get(port, "/api/folders")
    cache     = _api_get(port, "/api/cache/size")
    return {
        "stats":       stats,       # {total, images, videos, hidden} or None
        "formats":     formats if isinstance(formats, list) else None,
        "cameras":     cameras if isinstance(cameras, list) else None,
        "locations":   locations if isinstance(locations, list) else None,
        "album_count":  len(albums) if isinstance(albums, list) else None,
        "folder_count": len(folders) if isinstance(folders, list) else None,
        "cache_mb":    (cache or {}).get("total_mb") if cache else None,
    }


# ── formatting helpers ───────────────────────────────────────────────────────

def _fmt_uptime(seconds):
    if seconds is None:
        return "—"
    return str(timedelta(seconds=int(seconds)))


def _fmt(value, suffix="", empty="—"):
    if value is None:
        return empty
    return f"{value}{suffix}"


# ── the window itself ────────────────────────────────────────────────────────

class DashboardWindow:
    """
    Two-tab Tkinter window. Owns its own Tk() root — must be constructed and
    driven (via .show(), which calls mainloop()) on the thread that will run
    it; see the module docstring for why that's a dedicated background
    thread rather than the tray's own thread.
    """

    def __init__(self, controller, port: int):
        self.controller = controller
        self.port = port
        self._queue: "queue.Queue[tuple]" = queue.Queue()

        self.root = tk.Tk()
        self.root.title("Luminary Dashboard")
        self.root.geometry("440x520")
        self.root.minsize(380, 440)

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        self.home_tab = ttk.Frame(notebook)
        self.media_tab = ttk.Frame(notebook)
        notebook.add(self.home_tab, text="Home")
        notebook.add(self.media_tab, text="Media")

        self._home_vars = {}
        self._media_vars = {}
        self._build_home_tab()
        self._build_media_tab()

        footer = ttk.Frame(self.root)
        footer.pack(fill="x", padx=10, pady=(0, 10))
        self._status_var = tk.StringVar(value="Loading…")
        ttk.Label(footer, textvariable=self._status_var, foreground="#666").pack(side="left")
        ttk.Button(footer, text="Refresh Now", command=self._request_refresh).pack(side="right")

        self._schedule_fetch()
        self._poll_queue()
        self.root.after(REFRESH_INTERVAL_MS, self._auto_refresh_tick)

    # ── layout ──────────────────────────────────────────────────────────────

    def _add_row(self, parent, row, label):
        ttk.Label(parent, text=label, font=("", 9, "bold")).grid(
            row=row, column=0, sticky="w", padx=(0, 12), pady=3)
        var = tk.StringVar(value="—")
        ttk.Label(parent, textvariable=var).grid(row=row, column=1, sticky="w", pady=3)
        return var

    def _build_home_tab(self):
        frame = ttk.Frame(self.home_tab, padding=10)
        frame.pack(fill="both", expand=True)

        rows = [
            ("status",      "Server status"),
            ("address",     "Address"),
            ("uptime",      "Uptime"),
            ("pid",         "Process ID"),
            ("cpu",         "CPU usage"),
            ("mem",         "Memory usage"),
            ("threads",     "Threads"),
            ("sync",        "Background sync"),
            ("last_sync",   "Last sync result"),
        ]
        for i, (key, label) in enumerate(rows):
            self._home_vars[key] = self._add_row(frame, i, label)

    def _build_media_tab(self):
        frame = ttk.Frame(self.media_tab, padding=10)
        frame.pack(fill="both", expand=True)

        rows = [
            ("total",     "Total media"),
            ("images",    "Images"),
            ("videos",    "Videos"),
            ("hidden",    "Hidden"),
            ("formats",   "Formats in use"),
            ("cameras",   "Cameras seen"),
            ("albums",    "Albums"),
            ("folders",   "Folders"),
            ("cache",     "Thumbnail cache"),
        ]
        for i, (key, label) in enumerate(rows):
            self._media_vars[key] = self._add_row(frame, i, label)

        ttk.Label(frame, text="By location", font=("", 9, "bold")).grid(
            row=len(rows), column=0, sticky="nw", pady=(10, 3))
        self._locations_list = tk.Listbox(frame, height=8)
        self._locations_list.grid(row=len(rows), column=1, sticky="nsew", pady=(10, 3))
        frame.grid_columnconfigure(1, weight=1)

    # ── fetch / refresh cycle ────────────────────────────────────────────────

    def _schedule_fetch(self):
        """Kick off one background fetch; result lands on self._queue."""
        def worker():
            home = _gather_home_data(self.controller, self.port)
            media = _gather_media_data(self.port)
            self._queue.put(("data", home, media))
        threading.Thread(target=worker, name="luminary-dashboard-fetch", daemon=True).start()

    def _request_refresh(self):
        self._status_var.set("Refreshing…")
        self._schedule_fetch()

    def _auto_refresh_tick(self):
        if not self._alive():
            return
        self._schedule_fetch()
        self.root.after(REFRESH_INTERVAL_MS, self._auto_refresh_tick)

    def _poll_queue(self):
        if not self._alive():
            return
        try:
            while True:
                kind, home, media = self._queue.get_nowait()
                self._apply_home(home)
                self._apply_media(media)
                self._status_var.set(f"Updated {time.strftime('%H:%M:%S')}")
        except queue.Empty:
            pass
        self.root.after(QUEUE_POLL_MS, self._poll_queue)

    def _apply_home(self, d: dict):
        v = self._home_vars
        v["status"].set("● Running" if d["running"] else "○ Stopped")
        v["address"].set(f"http://localhost:{d['port']}/" if d["running"] else "—")
        v["uptime"].set(_fmt_uptime(d["uptime"]))
        v["pid"].set(str(d["pid"]))
        v["cpu"].set(_fmt(d["cpu_percent"], "%") if PSUTIL_AVAILABLE else "psutil not installed")
        v["mem"].set(_fmt(d["rss_mb"], " MB") if PSUTIL_AVAILABLE else "psutil not installed")
        v["threads"].set(str(d["thread_count"]))
        v["sync"].set("Running…" if d["sync_running"] else "Idle")
        if d["sync_error"]:
            v["last_sync"].set(f"Error: {d['sync_error']}")
        elif d["sync_result"]:
            r = d["sync_result"]
            v["last_sync"].set(f"+{r.get('added', 0)} new, {r.get('removed', 0)} removed, "
                                f"{r.get('total', '—')} total")
        else:
            v["last_sync"].set("No sync run yet this session")

    def _apply_media(self, d: dict):
        v = self._media_vars
        stats = d["stats"]
        if stats:
            v["total"].set(str(stats.get("total", "—")))
            v["images"].set(str(stats.get("images", "—")))
            v["videos"].set(str(stats.get("videos", "—")))
            v["hidden"].set(str(stats.get("hidden", "—")))
        else:
            for k in ("total", "images", "videos", "hidden"):
                v[k].set("server unavailable")

        v["formats"].set(f"{len(d['formats'])} ({', '.join(d['formats'])})"
                          if d["formats"] else "—")
        v["cameras"].set(str(len(d["cameras"])) if d["cameras"] is not None else "—")
        v["albums"].set(_fmt(d["album_count"]))
        v["folders"].set(_fmt(d["folder_count"]))
        v["cache"].set(_fmt(d["cache_mb"], " MB"))

        self._locations_list.delete(0, tk.END)
        if d["locations"]:
            for loc in d["locations"]:
                label = loc.get("label") or loc.get("root", "?")
                count = loc.get("synced_count", "?")
                self._locations_list.insert(tk.END, f"{label}  —  {count} item(s)")
        else:
            self._locations_list.insert(tk.END, "(no locations configured)")

    def _alive(self) -> bool:
        try:
            return bool(self.root.winfo_exists())
        except Exception:
            return False

    def show(self):
        """Blocks until the window is closed."""
        self.root.mainloop()

    def bring_to_front(self):
        """
        Best-effort: called from a different thread (the tray's own event
        loop) when the user clicks "Dashboard" while it's already open.
        Tkinter calls aren't officially thread-safe, but scheduling through
        .after(0, …) from another thread is a common, generally-safe-enough
        pattern in CPython for a simple "raise this window" action.
        """
        try:
            self.root.after(0, lambda: (self.root.deiconify(), self.root.lift(), self.root.focus_force()))
        except Exception:
            pass


# ── module-level singleton so the tray only ever opens one dashboard window ──

_state_lock = threading.Lock()
_state = {"window": None}


def open_dashboard(controller, port: int):
    """
    Open the Dashboard window, or bring the existing one to front if it's
    already open. Runs its own Tk mainloop on a dedicated daemon thread —
    see the module docstring for why (pystray/AppIndicator already own the
    process's main thread).
    """
    with _state_lock:
        win = _state["window"]
        if win is not None and win._alive():
            win.bring_to_front()
            return

        def _run():
            window = DashboardWindow(controller, port)
            with _state_lock:
                _state["window"] = window
            try:
                window.show()
            finally:
                with _state_lock:
                    if _state["window"] is window:
                        _state["window"] = None

        threading.Thread(target=_run, name="luminary-dashboard", daemon=True).start()