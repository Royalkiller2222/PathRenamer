import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

# main.py creates its log and config files under %APPDATA%\PathRenamer when it
# is imported; keep the tests away from the real ones.
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="pathrenamer-tests-")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def main():
    """The app module (needs PyQt6, like the app itself)."""
    pytest.importorskip("PyQt6.QtWidgets")
    import main as app
    return app


@pytest.fixture(autouse=True)
def no_registry(monkeypatch):
    """The trial's second copy goes to the Windows registry in the app; keep
    the tests away from it (each License gets an empty one)."""
    import licensing
    monkeypatch.setattr(licensing, "trial_backup", licensing.MemoryBackup)


@pytest.fixture(autouse=True)
def no_update_checks(monkeypatch):
    """Windows built by a test may check GitHub for updates after a few
    seconds; never from the tests (tests that need it patch updates.check)."""
    import updates
    monkeypatch.setattr(updates, "check", lambda current, session=None: None)


def ended_trial():
    """Config values for a Pro trial that ended long ago, sealed like the app does."""
    import licensing
    lic = licensing.License({}, organization_id="org", clock=lambda: 1_000.0, backup=licensing.MemoryBackup())
    lic.start_trial()
    return {licensing.STATE_KEY: lic.cfg[licensing.STATE_KEY]}


@pytest.fixture(autouse=True)
def fail_on_slot_errors(monkeypatch):
    """Errors raised inside Qt slots don't reach pytest (and the app's own
    handler would open a message box); collect them and fail the test."""
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *exc: errors.append(exc))
    yield
    if errors:
        raise errors[0][1]


@pytest.fixture(scope="session")
def qapp(main):
    """One QApplication for every test that builds windows (kept referenced:
    Qt aborts if it is garbage collected while widgets exist)."""
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def make_files(root: Path, *rel_paths: str) -> list:
    """Create empty files under root and return their paths."""
    out = []
    for rel in rel_paths:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"")
        out.append(p)
    return out


@pytest.fixture
def make_window(main, qapp, tmp_path, monkeypatch):
    """Build real main windows (offline, nothing written to the real config)."""
    from PyQt6.QtWidgets import QMessageBox
    saved = {}
    windows = []
    monkeypatch.setattr(main, "cfg_save", lambda c: saved.update(c))
    monkeypatch.setattr(main, "HISTORY_PATH", tmp_path / "history.json")
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)

    def factory(**overrides):
        cfg = dict(main.DEFAULTS, first_run=False, media_type="movie", root_movie=str(tmp_path / "lib"),
                   use_tvmaze=False, use_anidb=False, use_cinemeta=False, last_source_folder="",
                   check_updates=False)
        cfg.update(overrides)
        monkeypatch.setattr(main, "cfg_load", lambda: dict(cfg))
        w = main.MainWindow()
        windows.append(w)
        return w
    factory.saved = saved
    yield factory
    for w in windows:
        w._quitting = True                 # (not to the tray)
        w.close()


def run_preview(main, w, files):
    """Run the matcher like Preview does, without the network or dialogs."""
    w._add_files_to_list(files)
    worker = main.ScanWorker(files, main.MatchEngine(cfg=w.cfg), w._media_type(), w._get_destination())
    worker.candidate_ready.connect(w._on_candidate)
    w._scanning = True
    worker.run()
    w._on_scan_done()


def wait(qapp, ms):
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)


def wait_until(qapp, condition, timeout=5.0):
    """Process events until condition() is true (animations run on a timer)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return condition()


class FakeResponse:
    def __init__(self, status=200, json=None, content=b"", text=None):
        import json as _json
        self.status_code = status
        self._json = json
        if json is not None and not content:
            content = _json.dumps(json).encode()
        self.content = content if text is None else text.encode()

    def json(self):
        if self._json is None:
            raise ValueError("no JSON")
        return self._json


class FakeSession:
    """Answers requests from a table instead of the network.
    routes: {(METHOD, url): response or callable(**kwargs) -> response};
    calls: [(METHOD, url, kwargs)] in order."""

    def __init__(self, routes=None, error=None):
        self.routes = dict(routes or {})
        self.calls = []
        self.error = error           # raise this for every request (no network)

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.error is not None:
            raise self.error
        answer = self.routes.get((method, url))
        if answer is None:
            return FakeResponse(404, json={"detail": "not found"})
        return answer(**kwargs) if callable(answer) else answer

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)
