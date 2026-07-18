#!/usr/bin/env python3
"""
instance_lock.py — Cross-platform single-instance guard for Luminary.

Prevents a second copy of Luminary from starting while one is already
running, in ANY environment app.py's entry point supports: dev console
(`python3 app.py` / `./run.sh`), an installed desktop build running as a
system tray app, or a headless Linux server (Raspberry Pi, etc.).

How it works
────────────
A lock file is opened in the per-user data directory (see app_paths.py,
already the single source of truth for where Luminary's data lives — this
means the lock is scoped exactly like the DB/config/logs: shared across dev
runs from the same checkout, or shared across launches of the same installed
build). An OS-level advisory lock is then taken on that open file handle:

  - POSIX (Linux/macOS): fcntl.flock(fd, LOCK_EX | LOCK_NB)
  - Windows:              msvcrt.locking(fd, LK_NBLCK, 1)

This is preferred over a plain PID file because the lock is held by the OS
against the open file descriptor/handle itself, not by anything Luminary has
to remember to clean up: if the process ever dies for any reason at all —
normal exit, crash, `kill -9`, the machine losing power mid-run — the lock
is released automatically the moment the file handle goes away. There is no
staleness case to handle, unlike a PID file (which can be left behind by a
crashed process and then wrongly block every future launch until someone
deletes it by hand).

Whichever instance is holding the lock writes its PID and port into the
file, so a second launch attempt that fails to acquire the lock can still
read that back and point the user at the instance that's already running,
rather than just failing with no explanation.
"""

import os
import logging

import app_paths

log = logging.getLogger("luminary.instance_lock")

LOCK_PATH = app_paths.USER_DATA_ROOT / "luminary.lock"


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
        self._acquire()

    def _acquire(self):
        LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            # "a+" creates the file if missing and never truncates on open,
            # so a concurrent second process can still read the current
            # holder's PID/port right up until (and unless) it wins the lock
            # itself and overwrites them.
            self._fh = open(LOCK_PATH, "a+")
        except OSError:
            log.warning(
                "Could not open lock file %s — skipping the single-instance "
                "check for this launch.", LOCK_PATH, exc_info=True
            )
            return

        try:
            if os.name == "nt":
                import msvcrt
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
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

    def _read_existing_info(self):
        try:
            self._fh.seek(0)
            parts = self._fh.read().strip().split()
            if len(parts) >= 1:
                self.existing_pid = int(parts[0])
            if len(parts) >= 2:
                self.existing_port = int(parts[1])
        except Exception:
            log.debug("Could not parse existing lock file contents", exc_info=True)

    def release(self):
        """Safe to call even if already_running is True (no-op in that case)
        or if release() is called twice."""
        if self._fh is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            self._fh.close()
        except OSError:
            pass
        self._fh = None