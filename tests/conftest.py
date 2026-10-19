import os
import sys
import tempfile
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
