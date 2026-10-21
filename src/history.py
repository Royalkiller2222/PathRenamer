"""Rename history, kept on disk so renames can be undone after the app was closed.

Each rename (one click on Rename, or one batch from the watch folder) is a
batch: when it happened, how the files were placed (moved, copied or
hard-linked) and every file's old and new path.
"""
import json
import os
import time
import uuid
from pathlib import Path

ACTIONS = ("move", "copy", "hardlink")


class RenameHistory:
    def __init__(self, path, limit=300):
        self.path = Path(path) if path else None
        self.limit = limit
        self._batches = []          # oldest first
        self._load()

    # ── storage ──────────────────────────────────────────────────────
    def _load(self):
        if not self.path or not self.path.exists():
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            batches = data.get("batches", []) if isinstance(data, dict) else []
            self._batches = [b for b in batches if isinstance(b, dict) and b.get("ops")]
        except (OSError, ValueError):
            self._batches = []       # unreadable history: start a new one

    def save(self):
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "batches": self._batches}, f, indent=1)
        os.replace(tmp, self.path)  # never leaves a half-written history

    # ── batches ──────────────────────────────────────────────────────
    def add(self, ops, dest_root="", source="manual"):
        """ops: [(src, dst, method)] for files that were placed; method is the
        way that file was placed ("move", "copy" or "hardlink")."""
        batch = {
            "id": uuid.uuid4().hex[:12],
            "time": time.time(),
            "dest_root": str(dest_root or ""),
            "source": source,
            "undone": False,
            "ops": [{"src": str(s), "dst": str(d), "method": m} for s, d, m in ops],
        }
        self._batches.append(batch)
        del self._batches[:-self.limit]
        self.save()
        return batch

    def add_ops(self, batch_id, ops):
        """Add files to an existing batch (e.g. subtitles downloaded after it),
        so undoing the batch removes them too."""
        b = self.get(batch_id)
        if b is None:
            return
        b["ops"].extend({"src": str(s), "dst": str(d), "method": m} for s, d, m in ops)
        self.save()

    def batches(self):
        """Newest first."""
        return list(reversed(self._batches))

    def get(self, batch_id):
        return next((b for b in self._batches if b["id"] == batch_id), None)

    def last_undoable(self):
        return next((b for b in reversed(self._batches) if not b.get("undone")), None)

    def mark_undone(self, batch_id, undone=True):
        b = self.get(batch_id)
        if b is not None:
            b["undone"] = undone
            self.save()

    def clear(self):
        self._batches = []
        self.save()


def describe(batch):
    """'3 files moved to D:/Plex/Movies' for lists."""
    n = len(batch["ops"])
    methods = {op.get("method", "move") for op in batch["ops"]}
    methods.discard("download")                    # downloaded subtitles ride along
    verb = {"move": "moved", "copy": "copied", "hardlink": "hard-linked"}
    n = sum(1 for op in batch["ops"] if op.get("method") != "download")
    how = verb[next(iter(methods))] if len(methods) == 1 and next(iter(methods)) in verb else "placed"
    where = f" to {batch['dest_root']}" if batch.get("dest_root") else ""
    return f"{n} file{'s' if n != 1 else ''} {how}{where}"
