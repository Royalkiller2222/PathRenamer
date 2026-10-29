"""Checking GitHub for a newer version and downloading it safely."""
import hashlib

import pytest
import requests

from conftest import FakeResponse, FakeSession
from updates import (DOWNLOAD_PREFIX, RELEASES_API, Asset, UpdateError, check, download, install_kind,
                     installer_command, is_newer, latest_release, parse_version, portable_swap_script)

SETUP = b"MZ fake installer bytes" * 100


def _release(tag="V1.6.0", digest=None, url=None):
    return {"tag_name": tag, "html_url": "https://github.com/Royalkiller2222/PathRenamer/releases/tag/" + tag,
            "body": "**New:** faster.",
            "assets": [
                {"name": f"PathRenamer-Setup-{tag[1:]}.exe", "size": len(SETUP),
                 "browser_download_url": url or DOWNLOAD_PREFIX + f"{tag}/PathRenamer-Setup-{tag[1:]}.exe",
                 "digest": digest if digest is not None else "sha256:" + hashlib.sha256(SETUP).hexdigest()},
                {"name": "PathRenamer.exe", "size": 3, "digest": None,
                 "browser_download_url": DOWNLOAD_PREFIX + f"{tag}/PathRenamer.exe"},
                {"name": "evil.exe", "size": 1, "browser_download_url": "https://example.com/evil.exe"}]}


def test_versions():
    assert parse_version("V1.5.0") == (1, 5, 0) and parse_version("1.6") == (1, 6, 0)
    assert is_newer("1.10.0", "1.9.3") and not is_newer("V1.5.0", "1.5.0") and not is_newer("1.4.9", "1.5.0")


def test_latest_release_and_check():
    session = FakeSession({("GET", RELEASES_API): FakeResponse(json=_release())})
    rel = latest_release(session)
    assert (rel.version, rel.tag) == ("1.6.0", "V1.6.0")
    assert rel.installer.name == "PathRenamer-Setup-1.6.0.exe" and rel.installer.sha256
    assert rel.portable.name == "PathRenamer.exe"
    assert all(a.url.startswith(DOWNLOAD_PREFIX) for a in rel.assets)      # evil.exe left out
    assert session.calls[0][2]["headers"]["User-Agent"] == "PathRenamer"
    assert check("1.5.0", session).version == "1.6.0"
    assert check("1.6.0", session) is None


@pytest.mark.parametrize("response, message", [
    (FakeResponse(404), "No release"), (FakeResponse(403), "limiting"), (FakeResponse(500), "error")])
def test_check_errors(response, message):
    with pytest.raises(UpdateError, match=message):
        latest_release(FakeSession({("GET", RELEASES_API): response}))
    with pytest.raises(UpdateError, match="Can't reach GitHub"):
        latest_release(FakeSession(error=requests.ConnectionError()))


class Stream:
    def __init__(self, data, status=200):
        self.data, self.status_code, self.headers = data, status, {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_content(self, chunk_size):
        for i in range(0, len(self.data), chunk_size):
            yield self.data[i:i + chunk_size]


def _asset(data=SETUP, sha=None, size=None):
    return Asset("PathRenamer-Setup-1.6.0.exe", DOWNLOAD_PREFIX + "V1.6.0/PathRenamer-Setup-1.6.0.exe",
                 len(SETUP) if size is None else size,
                 hashlib.sha256(SETUP).hexdigest() if sha is None else sha)


def test_download_checks_size_and_hash(tmp_path):
    seen = []
    session = FakeSession({("GET", _asset().url): lambda **kw: Stream(SETUP)})
    path = download(_asset(), tmp_path, progress=lambda d, t: seen.append((d, t)), session=session)
    assert path.read_bytes() == SETUP and path.name == "PathRenamer-Setup-1.6.0.exe"
    assert seen[-1] == (len(SETUP), len(SETUP))


def test_tampered_or_cut_downloads_are_deleted(tmp_path):
    bad = FakeSession({("GET", _asset().url): lambda **kw: Stream(SETUP[:-1] + b"X")})
    with pytest.raises(UpdateError, match="didn't match"):
        download(_asset(), tmp_path, session=bad)
    short = FakeSession({("GET", _asset().url): lambda **kw: Stream(SETUP[:10])})
    with pytest.raises(UpdateError, match="incomplete"):
        download(_asset(), tmp_path, session=short)
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(UpdateError, match="doesn't come from"):
        download(Asset("x.exe", "https://example.com/x.exe"), tmp_path, session=bad)
    cancel = FakeSession({("GET", _asset().url): lambda **kw: Stream(SETUP)})
    with pytest.raises(UpdateError, match="cancelled"):
        download(_asset(), tmp_path, should_cancel=lambda: True, session=cancel)


def test_how_this_copy_updates(tmp_path):
    exe = tmp_path / "PathRenamer.exe"
    exe.write_bytes(b"")
    assert install_kind(frozen=False) == "source"
    assert install_kind(frozen=True, exe=exe) == "portable"
    (tmp_path / "unins000.exe").write_bytes(b"")
    assert install_kind(frozen=True, exe=exe) == "installer"
    assert installer_command(tmp_path / "setup.exe")[1:] == ["/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                                                             "/CLOSEAPPLICATIONS", "/relaunch=1"]
    script = portable_swap_script(tmp_path / "new.exe", exe, 4242)
    assert 'PID eq 4242' in script and f'move /y "{tmp_path / "new.exe"}" "{exe}"' in script
    assert f'start "" "{exe}"' in script


def test_old_downloads_are_cleaned_up(tmp_path):
    import os
    import time
    from updates import remove_old_downloads
    old, new = tmp_path / "pathrenamer-update-old", tmp_path / "pathrenamer-update-new"
    for folder in (old, new):
        folder.mkdir()
        (folder / "PathRenamer-Setup-1.6.0.exe").write_bytes(b"x")
    os.utime(old, (time.time() - 7200, time.time() - 7200))
    (tmp_path / "keep.txt").write_text("not ours")
    remove_old_downloads(tmp_path)
    assert not old.exists() and new.exists() and (tmp_path / "keep.txt").exists()


def test_installer_that_needs_admin_rights_is_started_through_the_shell(tmp_path, monkeypatch):
    import os
    import subprocess

    import updates
    started = []

    def refuse(*a, **k):
        e = OSError("The requested operation requires elevation")
        e.winerror = 740
        raise e
    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(os, "startfile", lambda *a: started.append(a), raising=False)
    updates.start_update(tmp_path / "PathRenamer-Setup-1.6.0.exe", "installer")
    assert started[0][1] == "open" and "/relaunch=1" in started[0][2]
    with pytest.raises(updates.UpdateError, match="from source"):
        updates.start_update(tmp_path / "x.exe", "source")
