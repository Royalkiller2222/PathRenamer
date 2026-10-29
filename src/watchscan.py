"""Finding new videos in a watch folder and telling when they finished
arriving, without Qt. The window's FolderWatcher (watchfolder.py) and the
command line's `watch` (cli.py) both use it.

A file is complete when its size and modification time stayed the same for
a while and it can be opened, so a download or copy still running is waited
for. Files already there when watching starts are left alone unless asked.
"""
import os
import time
from pathlib import Path


def path_key(path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def can_open(path) -> bool:
    try:
        with open(path, "rb") as f:
            f.read(1)
        return True
    except OSError:
        return False


def scan_tree(folder, accept, skip_dir=None, max_depth=4, known=()):
    """(files, dirs) under folder: files = {key: (path, (size, mtime), readable)}
    for files accept() takes that aren't in known; dirs = the folders walked.
    None when the folder is missing."""
    skip_dir = skip_dir or (lambda name: False)
    folder = str(folder)
    if not os.path.isdir(folder):
        return None
    files, dirs = {}, [folder]
    try:
        base = folder.rstrip("\\/").count(os.sep)
        for root, dnames, fnames in os.walk(folder):
            depth = root.rstrip("\\/").count(os.sep) - base
            dnames[:] = ([d for d in sorted(dnames) if not d.startswith(".") and not skip_dir(d)]
                         if depth < max_depth else [])
            dirs.extend(os.path.join(root, d) for d in dnames)
            for name in fnames:
                if name.startswith("."):
                    continue
                path = os.path.join(root, name)
                k = path_key(path)
                if k in known or not accept(Path(path)):
                    continue
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                files[k] = (path, (st.st_size, st.st_mtime_ns), st.st_size > 0 and can_open(path))
    except OSError:                          # a folder vanished mid-walk: try again next time
        return {}, []
    return files, dirs


class Settler:
    """Decides which new files finished arriving, scan after scan."""

    def __init__(self, settle_seconds=15.0, include_existing=False):
        self.settle_seconds = settle_seconds
        self.include_existing = include_existing
        self.known = set()                   # keys already reported or ignored
        self.pending = {}                    # key -> (path, signature, seen like this since)
        self.first = True

    def ignore(self, paths):
        """Never report these files (e.g. ones already handled)."""
        for p in paths:
            k = path_key(p)
            self.known.add(k)
            self.pending.pop(k, None)

    def update(self, files, now):
        """Feed one scan's files; returns the paths that are ready, oldest
        knowledge first. Ready files are not reported again."""
        if self.first:
            self.first = False
            if not self.include_existing:
                self.known.update(files)
                return []
        ready = []
        for k, (path, signature, readable) in files.items():
            if k in self.known:
                continue
            seen = self.pending.get(k)
            if seen is None or seen[1] != signature:
                self.pending[k] = (path, signature, now)
            elif readable and now - seen[2] >= self.settle_seconds:
                ready.append(path)
        for k in list(self.pending):
            if k not in files:
                del self.pending[k]          # gone (or renamed by its download client)
        self.ignore(ready)
        return sorted(ready)

    def next_check(self, now):
        """Seconds until a pending file could be ready (None: nothing pending).
        A file that is done but still locked (being copied) is retried every
        few seconds."""
        if not self.pending:
            return None
        wait = min(seen + self.settle_seconds - now for _, _, seen in self.pending.values())
        return wait + 0.2 if wait > 0 else 5.0


def watch_forever(folder, accept, on_ready, skip_dir=None, settle_seconds=15.0, poll_seconds=10.0,
                  max_depth=4, include_existing=False, should_stop=lambda: False, on_error=None,
                  clock=time.monotonic, sleep=time.sleep):
    """Poll folder until should_stop() is true and call on_ready([Path]) with
    each batch of new, complete videos. on_error(message) hears once when
    the folder goes missing; watching resumes when it is back."""
    settler = Settler(settle_seconds, include_existing)
    missing = False
    while not should_stop():
        result = scan_tree(folder, accept, skip_dir, max_depth, settler.known)
        now = clock()
        if result is None:
            if not missing and on_error:
                on_error(f"The watch folder is missing: {folder}. Watching resumes when it is back.")
            missing = True
        else:
            missing = False
            ready = settler.update(result[0], now)
            if ready:
                on_ready([Path(p) for p in ready])
        wait = settler.next_check(now)
        sleep(poll_seconds if wait is None else min(poll_seconds, wait))
    return settler
