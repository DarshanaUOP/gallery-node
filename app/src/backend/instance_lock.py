#!/usr/bin/env python3
"""
instance_lock.py — Cross-platform single-instance guard for Luminary.

Prevents a second copy of Luminary from starting while one is already
running, in ANY environment app.py's entry point supports: dev console
(`python3 app.py` / `./run.sh`), an installed desktop build running as a
system tray app, or a headless Linux server (Raspberry Pi, etc.).

How it works
────────────
On Windows the PRIMARY guard is a named kernel mutex (CreateMutexW), checked
via GetLastError() == ERROR_ALREADY_EXISTS. This is the standard OS-native
single-instance idiom on Windows, and is deliberately preferred over relying
on file locking alone: msvcrt.locking()/LockFileEx are filesystem
operations, and it's common for antivirus/EDR software, backup agents, or
cloud-sync redirection of the user-data folder to hook or interfere with
file-lock calls (file-locking patterns are exactly what ransomware
heuristics watch for), which can cause a non-blocking lock attempt to
silently succeed for a second process instead of failing as it should. A
named mutex is a kernel object, not a file operation, and isn't subject to
that class of interference. The mutex handle is held for the lifetime of the
process and is released automatically by the OS on any exit path, including
a crash or `taskkill /F` — no staleness case to handle.

On POSIX (Linux/macOS), where this class of interference is far less common,
the guard is a lock file in the per-user data directory (see app_paths.py):

  - fcntl.flock(fd, LOCK_EX | LOCK_NB)

On every platform, whichever instance holds the guard also writes its PID
and port into a plain file, so a second launch attempt that fails to acquire
the guard can still read that back and point the user at the instance
that's already running, rather than just failing with no explanation.

IMPORTANT: unlike the old file-only implementation, any failure to even
open/create the info file is now treated as "assume another instance may be
running" rather than silently letting the launch through — a launcher that
can't prove it's safe to proceed should not proceed.
"""

import os
import logging

import app_paths

log = logging.getLogger("luminary.instance_lock")

LOCK_PATH = app_paths.USER_DATA_ROOT / "luminary.lock"

# Must be a fixed, unique-to-this-app name — every launch of Luminary on the
# same machine has to ask Windows for exactly this name for the mutex check
# to mean anything. "Global\\" makes it visible across sessions (e.g. RDP),
# matching "one Luminary total per machine" rather than "one per session".
_WINDOWS_MUTEX_NAME = "Global\\Luminary-SingleInstance-8B5A41FA-1578-4300-9044-C7FDF1FAD649"


class SingleInstance:
    """
    Usage:
        lock = SingleInstance(port)
        if lock.already_running:
            ...tell the user, exit...
        else:
            try:
                ...run the app...
            finally:
                lock.release()
    """

    def __init__(self, port: int):
        self.port = port
        self.already_running = False
        self.existing_pid = None
        self.existing_port = None
        self._fh = None
        self._mutex_handle = None
        if os.name == "nt":
            self._acquire_windows()
        else:
            self._acquire_posix()

    # ── Windows: named mutex is authoritative; the file is just PID/port info ──
    def _acquire_windows(self):
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]

        ERROR_ALREADY_EXISTS = 183
        handle = kernel32.CreateMutexW(None, False, _WINDOWS_MUTEX_NAME)
        last_error = kernel32.GetLastError()

        if not handle:
            # Could not create the mutex at all (extremely rare). Fail safe:
            # treat this launch as "can't prove it's the only one".
            log.warning(
                "Could not create single-instance mutex (Win32 error %d) — "
                "refusing to start a second instance to be safe.", last_error
            )
            self.already_running = True
            self._read_existing_info_best_effort()
            return

        if last_error == ERROR_ALREADY_EXISTS:
            # Someone else already holds the mutex — we are the second
            # instance. The handle Windows just gave us is a handle to the
            # EXISTING mutex object (not a new one); close it, we don't need it.
            kernel32.CloseHandle(handle)
            self.already_running = True
            self._read_existing_info_best_effort()
            return

        # We hold the mutex: we're the one true running instance. Keep the
        # handle open for the lifetime of this process — closing it (or the
        # process exiting, for any reason) releases the mutex automatically.
        self._mutex_handle = handle
        self._write_info_best_effort()

    def _read_existing_info_best_effort(self):
        try:
            with open(LOCK_PATH, "r") as fh:
                self._parse_info(fh.read())
        except OSError:
            pass  # no info file yet/unreadable — caller falls back to args.port

    def _write_info_best_effort(self):
        try:
            LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(LOCK_PATH, "w") as fh:
                fh.write(f"{os.getpid()} {self.port}\n")
        except OSError:
            log.warning("Could not write instance info file %s", LOCK_PATH, exc_info=True)

    # ── POSIX: flock on an open file handle is authoritative ──────────────
    def _acquire_posix(self):
        import fcntl

        LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            # "a+" creates the file if missing and never truncates on open,
            # so a concurrent second process can still read the current
            # holder's PID/port right up until (and unless) it wins the lock
            # itself and overwrites them.
            self._fh = open(LOCK_PATH, "a+")
        except OSError:
            # Can't prove it's safe to proceed — refuse rather than silently
            # letting a second instance start (the old behaviour here was to
            # let the launch through, which defeats the whole guard).
            log.warning(
                "Could not open lock file %s — refusing to start a second "
                "instance to be safe.", LOCK_PATH, exc_info=True
            )
            self.already_running = True
            return

        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            # Someone else already holds the lock — we are the second
            # instance. Read their info back before giving up our handle.
            self.already_running = True
            self._read_existing_info()
            self._fh.close()
            self._fh = None
            return

        # Lock acquired: we're the one true running instance. Record who we
        # are so a future second-launch attempt has something to report.
        self._fh.seek(0)
        self._fh.truncate()
        self._fh.write(f"{os.getpid()} {self.port}\n")
        self._fh.flush()

    def _parse_info(self, text: str):
        try:
            parts = text.strip().split()
            if len(parts) >= 1:
                self.existing_pid = int(parts[0])
            if len(parts) >= 2:
                self.existing_port = int(parts[1])
        except Exception:
            log.debug("Could not parse existing instance info contents", exc_info=True)

    def _read_existing_info(self):
        """POSIX path only — reads from the still-open handle."""
        try:
            self._fh.seek(0)
            self._parse_info(self._fh.read())
        except Exception:
            log.debug("Could not parse existing lock file contents", exc_info=True)

    def release(self):
        """Safe to call even if already_running is True (no-op in that case)
        or if release() is called twice."""
        if self._mutex_handle is not None:
            import ctypes
            try:
                ctypes.windll.kernel32.ReleaseMutex(self._mutex_handle)
                ctypes.windll.kernel32.CloseHandle(self._mutex_handle)
            except OSError:
                pass
            self._mutex_handle = None

        if self._fh is None:
            return
        try:
            import fcntl
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            self._fh.close()
        except OSError:
            pass
        self._fh = None