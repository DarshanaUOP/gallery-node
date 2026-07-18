#!/usr/bin/env python3
"""
tray.py — System tray integration for Luminary.

Only used for frozen (installed) builds — see the ENTRY POINT section at the
bottom of app.py. Dev-mode runs (`./run.sh` / `python3 app.py`) never import
this module and keep the normal terminal/console behaviour unchanged.

Tray menu:
  Open Luminary   — opens http://localhost:<port>/ in the default browser
  About           — opens the project's GitHub page
  Start / Stop    — toggles the backend server on/off; label reflects state
  Quit            — stops the backend (if running) and exits the tray app

Notes for packaging (see build-linux.sh / build-windows.bat / requirements.txt):
  - Requires the `pystray` package (plus Pillow, already a dependency).
  - On Linux, pystray needs a system tray/AppIndicator backend to actually be
    visible (e.g. a GNOME extension, or a desktop environment with native
    tray support such as KDE/XFCE). This is a desktop-environment concern,
    not something pip/PyInstaller can provide.
  - On a headless Linux install (no desktop environment at all — e.g. a
    Raspberry Pi set up over SSH), there's no display for a tray icon to
    exist on in the first place. is_display_available() detects this
    (no DISPLAY/WAYLAND_DISPLAY set) so app.py's entry point can skip the
    tray automatically and fall back to running as a plain background
    server, the same as dev mode — see README.md's "Headless / Server Mode"
    section for running it as a systemd service in that case.
"""

import os
import logging
import platform
import threading
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

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

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


def run_tray(app, port: int, open_on_start: bool = True):
    """
    Runs the system tray icon's event loop on the CALLING thread (this
    blocks — required on some platforms, e.g. macOS, where the tray must
    live on the main thread). The Flask/waitress server itself always runs
    on a separate background thread managed by a ServerController, which is
    started immediately and can be stopped/restarted from the "Start/Stop"
    menu item without closing the tray icon.
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

    def on_toggle(icon, item):
        if controller.running:
            controller.stop()
        else:
            controller.start()

    def on_quit(icon, item):
        controller.stop()
        icon.stop()

    def toggle_label(item) -> str:
        return "Stop Luminary" if controller.running else "Start Luminary"

    menu = pystray.Menu(
        MenuItem(
            "Open Luminary", on_open, default=True,
            enabled=lambda item: controller.running,
        ),
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

    icon.run()  # blocks the calling thread until on_quit() calls icon.stop()