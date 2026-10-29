"""The window's watch folder: reports new videos once they are complete
(see watchscan.py, which decides that). Scanning runs in a background
thread, so a slow network drive never freezes the window, and folder
change notifications make new files show up within seconds.
"""
import os
import threading
import time
from pathlib import Path

from PyQt6.QtCore import QFileSystemWatcher, QObject, QTimer, pyqtSignal

from watchscan import Settler, scan_tree

MAX_WATCHED_DIRS = 400          # operating systems limit how many folders can be watched


class FolderWatcher(QObject):
    files_ready = pyqtSignal(list)            # [Path]: new videos that finished arriving
    error = pyqtSignal(str)                   # the folder went missing (said once)
    _scanned = pyqtSignal(int, object)        # from the scan thread: (generation, result)

    def __init__(self, accept, skip_dir=None, parent=None, settle_seconds=15.0, poll_seconds=30.0,
                 max_depth=4):
        super().__init__(parent)
        self.accept = accept                  # Path -> bool: a file to report
        self.skip_dir = skip_dir or (lambda name: False)
        self.settle_seconds = settle_seconds
        self.poll_seconds = poll_seconds
        self.max_depth = max_depth
        self._folder = None
        self._generation = 0
        self._settler = Settler(settle_seconds)
        self._scanning = False
        self._again = False
        self._missing = False
        self._fs = QFileSystemWatcher(self)
        self._fs.directoryChanged.connect(lambda _path: self._check_in(2.0))
        self._poll = QTimer(self)
        self._poll.timeout.connect(self.check_now)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.check_now)
        self._scanned.connect(self._on_scanned)

    # ── control ──────────────────────────────────────────────────────
    def start(self, folder, include_existing=False):
        """Watch folder. Returns False (and says why through error) when it
        doesn't exist."""
        self.stop()
        folder = Path(folder)
        if not folder.is_dir():
            self.error.emit(f"The watch folder doesn't exist: {folder}")
            return False
        self._folder = folder
        self._generation += 1
        self._settler = Settler(self.settle_seconds, include_existing)
        self._missing = False
        self._poll.start(int(self.poll_seconds * 1000))
        self.check_now()
        return True

    def stop(self):
        self._generation += 1                 # results of a running scan are dropped
        self._folder = None
        self._poll.stop()
        self._timer.stop()
        dirs = self._fs.directories()
        if dirs:
            self._fs.removePaths(dirs)
        self._settler.pending = {}

    def is_running(self):
        return self._folder is not None

    def folder(self):
        return self._folder

    def ignore(self, paths):
        """Never report these files (e.g. ones the user already handled)."""
        self._settler.ignore(paths)

    def check_now(self):
        if self._folder is None:
            return
        if self._scanning:
            self._again = True
            return
        self._scanning = True
        self._settler.settle_seconds = self.settle_seconds
        known = set(self._settler.known)
        threading.Thread(target=self._scan, args=(self._generation, str(self._folder), known),
                         daemon=True).start()

    def _check_in(self, seconds):
        ms = int(seconds * 1000)
        if self._folder is not None and (not self._timer.isActive() or self._timer.remainingTime() > ms):
            self._timer.start(ms)

    # ── scanning (background thread) ─────────────────────────────────
    def _scan(self, generation, folder, known):
        try:
            result = scan_tree(folder, self.accept, self.skip_dir, self.max_depth, known)
        except Exception:                     # never let a scan error stop watching
            result = ({}, [])
        try:
            self._scanned.emit(generation, result)
        except RuntimeError:                  # the watcher was deleted meanwhile
            pass

    # ── results (UI thread) ──────────────────────────────────────────
    def _on_scanned(self, generation, result):
        self._scanning = False
        if generation != self._generation or self._folder is None:
            if self._again and self._folder is not None:
                self._again = False
                self.check_now()
            return
        if result is None:
            if not self._missing:
                self._missing = True
                self.error.emit(f"The watch folder is missing: {self._folder}. "
                                "Watching resumes when it is back.")
            return
        self._missing = False
        files, dirs = result
        now = time.monotonic()
        ready = self._settler.update(files, now)
        self._watch_dirs(dirs)
        if ready:
            self.files_ready.emit([Path(p) for p in ready])
        wait = self._settler.next_check(now)
        if wait is not None:
            self._check_in(wait)
        if self._again:
            self._again = False
            self.check_now()

    def _watch_dirs(self, dirs):
        wanted = set(dirs[:MAX_WATCHED_DIRS])
        current = set(self._fs.directories())
        gone = [d for d in current if d not in wanted]
        new = [d for d in wanted if d not in current and os.path.isdir(d)]
        if gone:
            self._fs.removePaths(gone)
        if new:
            self._fs.addPaths(new)
