"""Watch a folder (like Downloads) and report new videos once they are
complete: their size and modification time stayed the same for a while and
they can be opened. A download or copy that is still running is waited for.

Files already in the folder when watching starts are left alone unless asked
for. Scanning runs in a background thread, so a slow network drive never
freezes the window.
"""
import os
import threading
import time
from pathlib import Path

from PyQt6.QtCore import QFileSystemWatcher, QObject, QTimer, pyqtSignal

MAX_WATCHED_DIRS = 400          # operating systems limit how many folders can be watched


def _key(path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _can_open(path) -> bool:
    try:
        with open(path, "rb") as f:
            f.read(1)
        return True
    except OSError:
        return False


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
        self._first = False
        self._include_existing = False
        self._known = set()                   # normcased paths already reported or ignored
        self._pending = {}                    # normcased path -> (path, signature, seen since)
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
        self._first = True
        self._include_existing = include_existing
        self._known, self._pending, self._missing = set(), {}, False
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
        self._pending = {}

    def is_running(self):
        return self._folder is not None

    def folder(self):
        return self._folder

    def ignore(self, paths):
        """Never report these files (e.g. ones the user already handled)."""
        for p in paths:
            k = _key(p)
            self._known.add(k)
            self._pending.pop(k, None)

    def check_now(self):
        if self._folder is None:
            return
        if self._scanning:
            self._again = True
            return
        self._scanning = True
        known = set(self._known)
        threading.Thread(target=self._scan, args=(self._generation, str(self._folder), known),
                         daemon=True).start()

    def _check_in(self, seconds):
        ms = int(seconds * 1000)
        if self._folder is not None and (not self._timer.isActive() or self._timer.remainingTime() > ms):
            self._timer.start(ms)

    # ── scanning (background thread) ─────────────────────────────────
    def _scan(self, generation, folder, known):
        result = None
        try:
            if os.path.isdir(folder):
                files, dirs = {}, [folder]
                base = folder.rstrip("\\/").count(os.sep)
                for root, dnames, fnames in os.walk(folder):
                    depth = root.rstrip("\\/").count(os.sep) - base
                    dnames[:] = ([d for d in sorted(dnames) if not d.startswith(".") and not self.skip_dir(d)]
                                 if depth < self.max_depth else [])
                    dirs.extend(os.path.join(root, d) for d in dnames)
                    for name in fnames:
                        if name.startswith("."):
                            continue
                        path = os.path.join(root, name)
                        k = _key(path)
                        if k in known or not self.accept(Path(path)):
                            continue
                        try:
                            st = os.stat(path)
                        except OSError:
                            continue
                        files[k] = (path, (st.st_size, st.st_mtime_ns), st.st_size > 0 and _can_open(path))
                result = (files, dirs)
        except Exception:                     # a folder vanished mid-walk: try again next time
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
        if self._missing:
            self._missing = False
        files, dirs = result
        if self._first:
            self._first = False
            if not self._include_existing:
                self._known.update(files)
                files = {}
        now = time.monotonic()
        ready = []
        for k, (path, signature, readable) in files.items():
            if k in self._known:
                continue
            seen = self._pending.get(k)
            if seen is None or seen[1] != signature:
                self._pending[k] = (path, signature, now)
            elif readable and now - seen[2] >= self.settle_seconds:
                ready.append(path)
        for k in list(self._pending):
            if k not in files:
                del self._pending[k]          # gone (or renamed by its download client)
        self._watch_dirs(dirs)
        if ready:
            self.ignore(ready)
            self.files_ready.emit([Path(p) for p in sorted(ready)])
        if self._pending:
            # look again when the next file could be ready; a file that is
            # done but still locked (being copied) is retried every few seconds
            wait = min(seen + self.settle_seconds - now for _, _, seen in self._pending.values())
            self._check_in(wait + 0.2 if wait > 0 else 5.0)
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
