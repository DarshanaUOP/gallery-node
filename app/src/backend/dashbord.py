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
- Window lifecycle: the Tk() root is created once, the first time the
  Dashboard is opened, and lives for the rest of the app's life. Clicking
  the window's close button only withdraws (hides) it — it does not
  destroy the root — and reopening from the tray just re-shows the same
  window. This is deliberate: creating a fresh Tk() after a previous one
  was destroyed in the same process is fragile (testing showed it can
  intermittently hang), so "create once, hide/show repeatedly" sidesteps
  that entirely, on top of guaranteeing that closing the Dashboard can
  never affect the backend server or the tray icon — see
  DashboardWindow._on_window_close(). Any cross-thread signal into the Tk
  thread (e.g. re-showing the window when the tray's own thread requests
  it) goes through the same queue.Queue rather than a direct Tk call, for
  the same reason — see DashboardWindow.bring_to_front().
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
import webbrowser
from datetime import timedelta

import tkinter as tk
from tkinter import ttk

import app_paths

log = logging.getLogger("luminary.dashbord")

try:
    # Only needed for the titlebar icon on non-Windows platforms (see
    # _set_window_icon) — Pillow itself is a required project dependency
    # already, this just guards against an unusual build missing the
    # ImageTk submodule specifically.
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# Colors used for the status dot and the Start/Stop button — kept as plain
# hex so they render consistently across platforms/themes (ttk's own
# styling doesn't reliably support colored button backgrounds everywhere,
# so these two controls use plain tk widgets instead of ttk ones).
COLOR_RUNNING = "#2ecc71"  # green
COLOR_STOPPED = "#9e9e9e"  # ash/gray
COLOR_STOP_BTN  = "#e74c3c"  # red — shown when the button's action is "Stop"
COLOR_START_BTN = "#2ecc71"  # green — shown when the button's action is "Start"
GLOBE_ICON = "\U0001F310"  # 🌐 — used as the "open in browser" button's icon

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


def _set_window_icon(root: tk.Tk):
    """
    Sets the Dashboard window's titlebar icon to the bundled Luminary icon:
    app_paths.RESOURCES_DIR / "images" / "luminary.ico" — which resolves to
    app/src/frontend/images/luminary.ico in dev mode, or
    resources/images/luminary.ico in a frozen build (see app_paths.py).

    Best-effort, same philosophy as tray.py's _load_icon_image(): a missing
    or corrupt icon file should never prevent the Dashboard from opening.
    """
    ico_path = app_paths.RESOURCES_DIR / "images" / "luminary.ico"
    if not ico_path.is_file():
        log.info("No titlebar icon found at %s — using the platform default.", ico_path)
        return
    try:
        if os.name == "nt":
            # .ico loads natively via iconbitmap on Windows.
            root.iconbitmap(default=str(ico_path))
        elif PIL_AVAILABLE:
            # iconbitmap only understands .ico on Windows (and .xbm on X11),
            # so on Linux/macOS use Pillow + iconphoto instead.
            image = Image.open(ico_path)
            photo = ImageTk.PhotoImage(image)
            root._icon_photo_ref = photo  # keep a reference — Tk drops GC'd images
            root.iconphoto(True, photo)
    except Exception:
        log.warning("Could not set the Dashboard titlebar icon from %s", ico_path, exc_info=True)


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
        self.root.geometry("460x560")
        self.root.resizable(False, False)  # fixed-size window, per request
        _set_window_icon(self.root)

        # IMPORTANT: without an explicit WM_DELETE_WINDOW handler, some
        # platforms fall back to Tk's own default close behavior for a root
        # window, which can tear down the whole Tcl interpreter/process
        # rather than just this window — that's what caused closing the
        # Dashboard to kill the entire Luminary app. Binding it ourselves to
        # a plain self.root.destroy() scopes the close to this window only;
        # the backend server and tray icon live in a completely separate
        # thread/event loop and are never touched here.
        self.root.protocol("WM_DELETE_WINDOW", self._on_window_close)

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        self.home_tab = ttk.Frame(notebook)
        self.media_tab = ttk.Frame(notebook)
        notebook.add(self.home_tab, text="Home")
        notebook.add(self.media_tab, text="Media")

        self._home_vars = {}
        self._media_vars = {}
        self._current_address = None
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
        frame.grid_columnconfigure(1, weight=1)

        # ── Row 0: server status — colored dot + text on the left, a
        # Start/Stop button (colored red/green by action) on the right ──
        ttk.Label(frame, text="Server status", font=("", 9, "bold")).grid(
            row=0, column=0, sticky="w", padx=(0, 12), pady=3)

        status_frame = ttk.Frame(frame)
        status_frame.grid(row=0, column=1, sticky="w", pady=3)
        self._status_dot = tk.Label(status_frame, text="\u25CF", font=("", 12), fg=COLOR_STOPPED)
        self._status_dot.pack(side="left")
        self._status_text_var = tk.StringVar(value="Stopped")
        tk.Label(status_frame, textvariable=self._status_text_var).pack(side="left", padx=(4, 0))

        self._toggle_btn = tk.Button(
            frame, text="Start", bg=COLOR_START_BTN, fg="white",
            relief="flat", padx=10, command=self._on_toggle_clicked,
        )
        self._toggle_btn.grid(row=0, column=2, sticky="e", padx=(10, 0), pady=3)

        # ── Row 1: address — the URL text on the left, a globe button on
        # the right to open it in the default browser ──
        ttk.Label(frame, text="Address", font=("", 9, "bold")).grid(
            row=1, column=0, sticky="w", padx=(0, 12), pady=3)
        self._address_var = tk.StringVar(value="\u2014")
        ttk.Label(frame, textvariable=self._address_var).grid(
            row=1, column=1, sticky="w", pady=3)

        self._open_browser_btn = tk.Button(
            frame, text=GLOBE_ICON, relief="flat", padx=6,
            command=self._on_open_browser_clicked, state="disabled",
        )
        self._open_browser_btn.grid(row=1, column=2, sticky="e", padx=(10, 0), pady=3)

        rows = [
            ("uptime",      "Uptime"),
            ("pid",         "Process ID"),
            ("cpu",         "CPU usage"),
            ("mem",         "Memory usage"),
            ("threads",     "Threads"),
            ("sync",        "Background sync"),
            ("last_sync",   "Last sync result"),
        ]
        for i, (key, label) in enumerate(rows, start=2):
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
        # Skip the actual fetch while hidden (closed) — nothing's watching
        # it — but keep rescheduling so it resumes instantly on reopen.
        if self.root.state() != "withdrawn":
            self._schedule_fetch()
        self.root.after(REFRESH_INTERVAL_MS, self._auto_refresh_tick)

    def _poll_queue(self):
        if not self._alive():
            return
        try:
            while True:
                item = self._queue.get_nowait()
                kind = item[0]
                if kind == "data":
                    _, home, media = item
                    self._apply_home(home)
                    self._apply_media(media)
                    self._status_var.set(f"Updated {time.strftime('%H:%M:%S')}")
                elif kind == "raise":
                    # Requested by bring_to_front() from another thread (the
                    # tray's own thread) — handled here, on the Tk thread
                    # itself, via the queue rather than a direct cross-thread
                    # Tk call. Tkinter calls from a foreign thread aren't
                    # reliably safe; queue.Queue.put() is.
                    self.root.deiconify()
                    self.root.lift()
                    self.root.focus_force()
        except queue.Empty:
            pass
        self.root.after(QUEUE_POLL_MS, self._poll_queue)

    def _apply_home(self, d: dict):
        v = self._home_vars
        running = d["running"]

        self._status_dot.config(fg=COLOR_RUNNING if running else COLOR_STOPPED)
        self._status_text_var.set("Running" if running else "Stopped")

        # Toggle button: label + color reflect the *action* it performs next,
        # not the current state — "Stop" in red while running, "Start" in
        # green while stopped. Re-enabled here too, in case it was disabled
        # while a start/stop was in flight (see _on_toggle_clicked).
        if running:
            self._toggle_btn.config(text="Stop", bg=COLOR_STOP_BTN, state="normal")
        else:
            self._toggle_btn.config(text="Start", bg=COLOR_START_BTN, state="normal")

        self._current_address = f"http://localhost:{d['port']}/" if running else None
        self._address_var.set(self._current_address or "—")
        self._open_browser_btn.config(state="normal" if running else "disabled")

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

    def _on_toggle_clicked(self):
        """
        Starts/stops the backend. Runs on a background thread — the real
        controller.stop() does a thread join with a timeout of several
        seconds, and calling that directly from the Tk main thread would
        freeze the whole window for as long as it takes. The button is
        disabled for the duration and re-enabled by the next _apply_home()
        once a fresh status has been fetched.
        """
        self._toggle_btn.config(state="disabled")

        def worker():
            try:
                if self.controller.running:
                    self.controller.stop()
                else:
                    self.controller.start()
            except Exception:
                log.exception("Failed to toggle the Luminary server from the Dashboard")
            self._schedule_fetch()

        threading.Thread(target=worker, name="luminary-dashboard-toggle", daemon=True).start()

    def _on_open_browser_clicked(self):
        if self._current_address:
            webbrowser.open(self._current_address)

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

    def _on_window_close(self):
        """
        The only thing an "X" click on the Dashboard should ever do: hide
        this window. This withdraws rather than destroys the root on
        purpose — Tcl/Tk is fragile about creating a fresh Tk() interpreter
        after a previous one was destroyed in the same process (testing
        showed this can intermittently hang on the *second* such root), so
        the safe pattern here is "create the one root once, hide/show it
        repeatedly" rather than "destroy and recreate every time". No
        sys.exit(), no touching the server controller, no reaching into the
        tray — this window, now just hidden, is the only thing affected.
        """
        try:
            self.root.withdraw()
        except Exception:
            pass

    def show(self):
        """
        Blocks for as long as this Tk root exists — in practice, for the
        rest of the app's life, since closing the window only withdraws
        (hides) it rather than destroying the root; see _on_window_close().
        """
        self.root.mainloop()

    def bring_to_front(self):
        """
        Called from a different thread (the tray's own event loop) when the
        user clicks "Dashboard" while it's already open (or was previously
        closed/hidden). Tkinter calls made directly from a thread that
        isn't running that root's mainloop are not reliably safe — under
        real-world conditions this can hang rather than raise, so this
        hands the request to the Tk thread through the same plain
        queue.Queue used for data updates instead of touching self.root
        directly. queue.Queue is itself thread-safe.
        """
        try:
            self._queue.put(("raise",))
        except Exception:
            pass


# ── module-level singleton so the tray only ever opens one dashboard window ──

_state_lock = threading.Lock()
_state = {"window": None}


def open_dashboard(controller, port: int):
    """
    Open the Dashboard window, or bring the existing one to front if it's
    already open (or was previously closed — closing only hides it, see
    DashboardWindow._on_window_close). Only ever constructs one Tk() root
    for the life of the process: the first call creates it and starts its
    mainloop on a dedicated daemon thread (pystray/AppIndicator already own
    the process's main thread with their own event loop — see the module
    docstring); every later call just re-shows that same root. Deliberately
    never destroys and recreates the root, since doing that repeatedly is
    the specific pattern that can hang (see _on_window_close's docstring).
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


def shutdown():
    """
    Fully destroys the Dashboard's Tk root, if one was ever created.
    Optional — the dashboard thread is a daemon thread, so the process
    exiting cleans it up either way — but calling this from the tray's Quit
    handler gives a tidy, immediate teardown instead of leaving a hidden
    window around until process exit. Harmless to call if no Dashboard was
    ever opened. Safe to call from any thread: this is the one and only
    root this process will ever destroy, so the cross-thread-Tk-call
    fragility that affects *repeated* create/destroy cycles doesn't apply.
    """
    with _state_lock:
        win = _state["window"]
    if win is None:
        return
    try:
        win.root.after(0, win.root.destroy)
    except Exception:
        pass