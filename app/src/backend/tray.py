#!/usr/bin/env python3
"""
tray.py — System tray integration for Luminary.

Only used for frozen (installed) builds — see the ENTRY POINT section at the
bottom of app.py. Dev-mode runs (`./run.sh` / `python3 app.py`) never import
this module and keep the normal terminal/console behaviour unchanged.

Tray menu:
  Open Luminary   — opens http://localhost:<port>/ in the default browser
  Dashboard       — opens a local status window (dashbord.py): server health/
                    monitoring on one tab, indexed-media stats on another
  About           — opens the project's GitHub page
  Start / Stop    — toggles the backend server on/off; label reflects state
  Quit            — stops the backend (if running) and exits the tray app

Tray backend selection:
  - Windows / macOS: pystray, unchanged.
  - Linux: a native GTK + AppIndicator backend is tried FIRST, falling back
    to pystray only if that's unavailable. This exists because pystray's
    own GTK backend has a real, commonly-hit blind spot on Linux: it looks
    for a specific gi typelib namespace, and which one a distro actually
    ships varies —
      - Ubuntu 20.04 (and derivatives of that vintage) ship the original,
        now-unmaintained `AppIndicator3` typelib (`gir1.2-appindicator3-0.1`).
      - Most current distros (Ubuntu 22.04+, Fedora, etc.) instead ship the
        actively-maintained fork under `AyatanaAppIndicator3`
        (`gir1.2-ayatanaappindicator3-0.1`) and don't have `AppIndicator3`
        at all.
    If pystray's own detection guesses the namespace this particular
    machine doesn't have, it fails to construct a tray icon — not always
    loudly. Talking to AppIndicator directly here (via `_run_tray_appindicator`)
    sidesteps that: it tries both namespaces itself and uses whichever is
    actually installed, so both old and new Ubuntu (and anything else
    shipping either typelib) work the same way. Only if NEITHER typelib nor
    `gi` itself is available does this fall back to pystray.

Notes for packaging (see build-linux.sh / build-windows.bat / requirements.txt):
  - Requires the `pystray` package (plus Pillow, already a dependency) as
    the fallback backend on every platform.
  - On Linux, one of the two AppIndicator gi typelibs (see above) plus
    `python3-gi` and GTK 3 needs to be present for the native backend to be
    used; if neither is installed, pystray is used instead (which itself
    then also needs a tray/AppIndicator backend to actually show anything —
    see the pystray note in README.md's "System Tray" section).
  - On a headless Linux install (no desktop environment at all — e.g. a
    Raspberry Pi set up over SSH), there's no display for a tray icon to
    exist on in the first place. is_display_available() detects this
    (no DISPLAY/WAYLAND_DISPLAY set) so app.py's entry point can skip the
    tray automatically and fall back to running as a plain background
    server, the same as dev mode — see README.md's "Headless / Server Mode"
    section for running it as a systemd service in that case.
"""

import os
import importlib
import logging
import platform
import sys
import threading
import time
import webbrowser

log = logging.getLogger("luminary.tray")

ABOUT_URL = "https://github.com/DarshanaUOP/gallery-node"


class ServerController:
    """
    Starts/stops the Luminary backend on a background thread, so the tray
    icon's own event loop (which must run on the main thread) is never
    blocked by the server.

    Wraps either a waitress server (preferred — has a clean, thread-safe
    .close()) or, if waitress isn't installed, a werkzeug dev server (has
    .shutdown()). Either way, start()/stop() can be called repeatedly from
    the tray menu's "Start/Stop" item.
    """

    def __init__(self, app, port: int):
        self._app = app
        self.port = port
        self._server = None
        self._thread = None
        self._lock = threading.Lock()
        self.started_at = None  # time.monotonic() timestamp, set on start(); read by dashbord.py

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def uptime_seconds(self):
        """Seconds since the backend last started, or None if it isn't running."""
        if not self.running or self.started_at is None:
            return None
        return time.monotonic() - self.started_at

    def start(self):
        with self._lock:
            if self.running:
                return
            try:
                from waitress import create_server
                self._server = create_server(
                    self._app, host="0.0.0.0", port=self.port, threads=16
                )
                target = self._server.run
                log.info("Starting Luminary backend (Waitress) on http://0.0.0.0:%d", self.port)
            except ImportError:
                from werkzeug.serving import make_server
                self._server = make_server("0.0.0.0", self.port, self._app, threaded=True)
                target = self._server.serve_forever
                log.warning(
                    "Waitress not installed — using Flask's development server "
                    "(not recommended for production)."
                )

            self._thread = threading.Thread(
                target=target, name="luminary-server", daemon=True
            )
            self._thread.start()
            self.started_at = time.monotonic()

    def stop(self):
        with self._lock:
            if not self.running:
                return
            try:
                if hasattr(self._server, "close"):       # waitress server
                    self._server.close()
                elif hasattr(self._server, "shutdown"):  # werkzeug dev server
                    self._server.shutdown()
            except Exception:
                log.exception("Error while stopping the backend server")
            self._thread.join(timeout=5)
            self._thread = None
            self._server = None
            self.started_at = None
            log.info("Luminary backend stopped")


def is_display_available() -> bool:
    """
    Best-effort check for whether a GUI session exists to host a tray icon.

    Only meaningful on Linux: a headless install (e.g. a Raspberry Pi set up
    over SSH with no desktop environment installed) has no X11 or Wayland
    session at all, so there's nothing for pystray to attach a tray icon to
    — it has no "headless" fallback of its own, it would just fail to
    connect to a display (or hang trying). DISPLAY / WAYLAND_DISPLAY being
    unset is the standard signal that no such session exists.

    Windows and macOS installs always have a session capable of hosting a
    tray icon by the time a user-launched .exe/.app is running, so they're
    not checked here.
    """
    if platform.system() != "Linux":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _load_icon_image():
    """
    Load the app icon for the tray from the bundled frontend assets
    (resources/images/luminary.ico in a frozen build). Falls back to a
    plain generated square if the file is missing or unreadable, so a
    missing/corrupt icon file never prevents the tray from starting.
    """
    from PIL import Image
    import app_paths

    candidates = [
        app_paths.RESOURCES_DIR / "images" / "luminary.ico",
        app_paths.RESOURCES_DIR / "images" / "luminary.png",
    ]
    for path in candidates:
        if path.exists():
            try:
                return Image.open(path)
            except Exception:
                log.warning("Could not open tray icon at %s", path, exc_info=True)

    log.warning("No bundled tray icon found — using a generated placeholder.")
    return Image.new("RGB", (64, 64), color=(30, 144, 255))


def _resolve_icon_path_for_appindicator() -> str:
    """
    AppIndicator wants an icon *name* or an absolute *path* to an image file
    — GTK's icon loading here handles .png/.svg well but not .ico — unlike
    pystray, which wants a PIL Image (see _load_icon_image). Prefers an
    actual bundled .png; falls back to generating a small placeholder .png
    into CACHE_DIR (once, then reused) so a missing/corrupt icon file never
    prevents the tray from starting.
    """
    import app_paths

    candidates = [
        app_paths.RESOURCES_DIR / "images" / "luminary.png",
        app_paths.RESOURCES_DIR / "images" / "luminary.ico",
    ]
    for path in candidates:
        if path.exists():
            return str(path)

    placeholder = app_paths.CACHE_DIR / "luminary-tray-placeholder.png"
    if not placeholder.exists():
        try:
            from PIL import Image
            app_paths.CACHE_DIR.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (64, 64), color=(30, 144, 255)).save(placeholder)
        except Exception:
            log.warning("Could not generate placeholder tray icon", exc_info=True)
            return ""
    return str(placeholder)


def _import_appindicator():
    """
    Import the GTK/AppIndicator bindings used by the native tray backend.

    Uses the standard AppIndicator3 namespace on older systems such as
    Ubuntu 20.04 and falls back to AyatanaAppIndicator3 when that namespace
    is not available.
    """
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version('AppIndicator3', '0.1')
    from gi.repository import GLib, Gtk

    try:
        # On 20.04, use the standard AppIndicator3 namespace.
        from gi.repository import AppIndicator3 as appindicator
    except ImportError:
        from gi.repository import AyatanaAppIndicator3 as appindicator

    return appindicator, Gtk, GLib

def _open_dashboard(controller: ServerController, port: int):
    """
    Opens the Dashboard window (dashbord.py). Imported lazily here, not at
    module load, so a missing/broken Tkinter or psutil install never
    prevents the tray icon itself from starting — only the Dashboard menu
    item would fail, and only when clicked.
    """
    try:
        import dashbord
        dashbord.open_dashboard(controller, port)
    except Exception:
        log.exception("Failed to open the Dashboard window")


def _shutdown_dashboard():
    """
    Tears down the Dashboard window's Tk root (if one was ever created)
    when the tray is quitting. Optional cleanliness — the dashboard runs on
    a daemon thread, so the process exiting would take it down anyway —
    but this avoids leaving a hidden Tk window lingering for however long
    shutdown otherwise takes. Only imports dashbord if it was already
    imported (i.e. the Dashboard was opened at least once this session);
    never triggers the (comparatively heavy) Tkinter/psutil import path
    just to quit.
    """
    dashbord = sys.modules.get("dashbord")
    if dashbord is None:
        return
    try:
        dashbord.shutdown()
    except Exception:
        log.exception("Failed to shut down the Dashboard window")


def _run_tray_appindicator(app, port: int, open_on_start: bool):
    """
    Native GTK + AppIndicator tray backend — tried first on Linux (see the
    module docstring). Runs GLib's main loop on the CALLING thread, same
    blocking contract as pystray's icon.run().

    Note: unlike pystray's `default=True` menu item (which pystray emulates
    as a double-click/primary-click action on the platforms that support
    it), AppIndicator/StatusNotifierItem icons open their menu on any click
    by design — there's no separate "primary action" slot. "Open Luminary"
    is therefore always reached via the menu here, not a direct click.
    """
    appindicator, Gtk, GLib = _import_appindicator()

    controller = ServerController(app, port)
    controller.start()

    def url() -> str:
        return f"http://localhost:{port}/"

    def on_open(_item=None):
        webbrowser.open(url())

    def on_about(_item):
        webbrowser.open(ABOUT_URL)

    def on_dashboard(_item):
        _open_dashboard(controller, port)

    def on_toggle(_item):
        if controller.running:
            controller.stop()
        else:
            controller.start()
        toggle_item.set_label("Stop Luminary" if controller.running else "Start Luminary")

    def on_quit(_item):
        _shutdown_dashboard()
        controller.stop()
        Gtk.main_quit()

    menu = Gtk.Menu()

    open_item = Gtk.MenuItem(label="Open Luminary")
    open_item.connect("activate", on_open)
    menu.append(open_item)

    dashboard_item = Gtk.MenuItem(label="Dashboard")
    dashboard_item.connect("activate", on_dashboard)
    menu.append(dashboard_item)

    about_item = Gtk.MenuItem(label="About")
    about_item.connect("activate", on_about)
    menu.append(about_item)

    menu.append(Gtk.SeparatorMenuItem())

    toggle_item = Gtk.MenuItem(label="Stop Luminary" if controller.running else "Start Luminary")
    toggle_item.connect("activate", on_toggle)
    menu.append(toggle_item)

    quit_item = Gtk.MenuItem(label="Quit")
    quit_item.connect("activate", on_quit)
    menu.append(quit_item)

    menu.show_all()

    icon_path = _resolve_icon_path_for_appindicator()
    indicator = appindicator.Indicator.new(
        "luminary",
        icon_path or "image-missing",
        appindicator.IndicatorCategory.APPLICATION_STATUS,
    )
    indicator.set_status(appindicator.IndicatorStatus.ACTIVE)
    indicator.set_title("Luminary")
    indicator.set_menu(menu)

    if open_on_start:
        # Give the server a moment to bind its socket before the browser
        # makes its first request.
        GLib.timeout_add(1000, lambda: (on_open(), False)[1])

    Gtk.main()  # blocks the calling thread until on_quit() calls Gtk.main_quit()


def _run_tray_pystray(app, port: int, open_on_start: bool):
    """
    pystray-based tray backend. Used on Windows and macOS always, and on
    Linux only as a fallback when no AppIndicator gi typelib is installed
    (see run_tray() and the module docstring).
    """
    import pystray
    from pystray import MenuItem

    controller = ServerController(app, port)
    controller.start()

    def url() -> str:
        return f"http://localhost:{port}/"

    def on_open(icon, item):
        webbrowser.open(url())

    def on_about(icon, item):
        webbrowser.open(ABOUT_URL)

    def on_dashboard(icon, item):
        _open_dashboard(controller, port)

    def on_toggle(icon, item):
        if controller.running:
            controller.stop()
        else:
            controller.start()

    def on_quit(icon, item):
        _shutdown_dashboard()
        controller.stop()
        icon.stop()

    def toggle_label(item) -> str:
        return "Stop Luminary" if controller.running else "Start Luminary"

    menu = pystray.Menu(
        MenuItem(
            "Open Luminary", on_open, default=True,
            enabled=lambda item: controller.running,
        ),
        MenuItem("Dashboard", on_dashboard),
        MenuItem("About", on_about),
        pystray.Menu.SEPARATOR,
        MenuItem(toggle_label, on_toggle),
        MenuItem("Quit", on_quit),
    )

    icon = pystray.Icon("Luminary", icon=_load_icon_image(), title="Luminary", menu=menu)

    if open_on_start:
        # Give the server a moment to bind its socket before the browser
        # makes its first request.
        threading.Timer(1.0, lambda: webbrowser.open(url())).start()

    try:
        icon.run()  # blocks the calling thread until on_quit() calls icon.stop()
    except AssertionError:
        log.exception(
            "pystray failed to dock icon (AssertionError). Running without tray."
        )
        # Keep the process alive while the server thread runs so the
        # background HTTP server started above continues serving.
        try:
            while controller.running:
                threading.Event().wait(1.0)
        except KeyboardInterrupt:
            pass


def run_tray(app, port: int, open_on_start: bool = True):
    """
    Runs the system tray icon's event loop on the CALLING thread (this
    blocks — required on some platforms, e.g. macOS, where the tray must
    live on the main thread). The Flask/waitress server itself always runs
    on a separate background thread managed by a ServerController, which is
    started immediately and can be stopped/restarted from the "Start/Stop"
    menu item without closing the tray icon.

    Backend selection: on Linux, the native AppIndicator backend is tried
    first and used if available; pystray is the fallback there and the only
    backend on Windows/macOS. See the module docstring for why.
    """
    if platform.system() == "Linux":
        try:
            _run_tray_appindicator(app, port, open_on_start)
            return
        except (ImportError, ValueError) as exc:
            log.warning(
                "Native AppIndicator tray backend unavailable (%s) — falling "
                "back to pystray. Without AppIndicator bindings, pystray's "
                "own fallback is the legacy X11 XEmbed tray protocol, which "
                "stock GNOME (no tray extension) does not support at all — "
                "if you see 'Failed to dock icon' next, install:  "
                "sudo apt install python3-gi gir1.2-gtk-3.0 "
                "gir1.2-appindicator3-0.1  (or gir1.2-ayatanaappindicator3-0.1 "
                "on newer distros) and relaunch.", exc
            )

            # Heuristic: on Wayland or a GNOME desktop without AppIndicator
            # bindings, pystray's XEmbed fallback is unlikely to work and
            # will commonly fail with Xlib ConnectionClosedError /
            # "Failed to dock icon". In that situation, prefer to run the
            # backend without a tray rather than falling back to pystray
            # which only results in noisy errors and a broken tray UX.
            wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
            xdg_desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").upper()
            if wayland or "GNOME" in xdg_desktop:
                log.info(
                    "Detected Wayland/GNOME session without AppIndicator — "
                    "starting server without tray."
                )
                controller = ServerController(app, port)
                controller.start()
                if open_on_start:
                    threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{port}/")).start()
                try:
                    while controller.running:
                        threading.Event().wait(1.0)
                except KeyboardInterrupt:
                    controller.stop()
                return

    _run_tray_pystray(app, port, open_on_start)