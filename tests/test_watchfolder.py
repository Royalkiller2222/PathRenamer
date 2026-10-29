"""The watch folder: new videos are reported once they finished arriving."""
import os
import time

import pytest

from conftest import wait, wait_until

VIDEO = {".mkv", ".mp4"}


@pytest.fixture
def watcher(qapp):
    from watchfolder import FolderWatcher
    made = []

    def make(**kw):
        kw.setdefault("settle_seconds", 0.3)
        kw.setdefault("poll_seconds", 0.2)
        w = FolderWatcher(accept=lambda p: p.suffix.lower() in VIDEO,
                          skip_dir=lambda name: name.lower() in {"sample", "extras"}, **kw)
        w.reported, w.errors = [], []
        w.files_ready.connect(lambda paths: w.reported.extend(p.name for p in paths))
        w.error.connect(w.errors.append)
        made.append(w)
        return w
    yield make
    for w in made:
        w.stop()


def test_reports_new_videos_once_they_settle(watcher, qapp, tmp_path):
    (tmp_path / "Old.Movie.2001.mkv").write_bytes(b"x" * 10)        # there before: left alone
    w = watcher()
    assert w.start(tmp_path) and w.is_running() and w.folder() == tmp_path
    wait(qapp, 300)
    (tmp_path / "New.Show.S01E01.mkv").write_bytes(b"x" * 10)
    (tmp_path / "notes.txt").write_text("not a video")
    (tmp_path / "Release" / "Sample").mkdir(parents=True)
    (tmp_path / "Release" / "Sample" / "sample.mkv").write_bytes(b"x")
    (tmp_path / "Release" / "Movie.2020.mp4").write_bytes(b"x" * 10)
    (tmp_path / ".hidden.mkv").write_bytes(b"x")
    assert wait_until(qapp, lambda: len(w.reported) >= 2, timeout=5)
    wait(qapp, 500)
    assert sorted(w.reported) == ["Movie.2020.mp4", "New.Show.S01E01.mkv"]   # each once


def test_existing_files_on_request(watcher, qapp, tmp_path):
    (tmp_path / "Old.Movie.2001.mkv").write_bytes(b"x" * 10)
    w = watcher()
    w.start(tmp_path, include_existing=True)
    assert wait_until(qapp, lambda: w.reported == ["Old.Movie.2001.mkv"], timeout=5)


def test_waits_while_a_download_is_still_growing(watcher, qapp, tmp_path):
    w = watcher(settle_seconds=0.6)
    w.start(tmp_path)
    wait(qapp, 300)
    f = tmp_path / "Downloading.2024.mkv"
    end = time.monotonic() + 1.5
    while time.monotonic() < end:                     # still being written
        with open(f, "ab") as out:
            out.write(b"x" * 100)
        wait(qapp, 100)
        assert w.reported == []
    assert wait_until(qapp, lambda: w.reported == ["Downloading.2024.mkv"], timeout=5)


def test_empty_files_are_not_ready(watcher, qapp, tmp_path):
    w = watcher()
    w.start(tmp_path)
    wait(qapp, 300)
    (tmp_path / "Placeholder.mkv").write_bytes(b"")
    wait(qapp, 1200)
    assert w.reported == []


def test_ignored_files_are_never_reported(watcher, qapp, tmp_path):
    w = watcher()
    w.start(tmp_path)
    wait(qapp, 300)
    f = tmp_path / "Back.From.Undo.mkv"
    w.ignore([f])
    f.write_bytes(b"x" * 10)
    wait(qapp, 1200)
    assert w.reported == []


def test_missing_folder_is_reported_once_and_watching_resumes(watcher, qapp, tmp_path):
    folder = tmp_path / "Downloads"
    w = watcher()
    assert not w.start(folder) and "doesn't exist" in w.errors[0]
    folder.mkdir()
    assert w.start(folder)
    wait(qapp, 300)
    os.rename(folder, tmp_path / "away")
    assert wait_until(qapp, lambda: len(w.errors) == 2, timeout=5)
    wait(qapp, 600)
    assert len(w.errors) == 2 and "missing" in w.errors[1]
    os.rename(tmp_path / "away", folder)
    wait(qapp, 400)
    (folder / "Back.2020.mkv").write_bytes(b"x" * 5)
    assert wait_until(qapp, lambda: w.reported == ["Back.2020.mkv"], timeout=5)


def test_stop_ends_watching(watcher, qapp, tmp_path):
    w = watcher()
    w.start(tmp_path)
    wait(qapp, 300)
    w.stop()
    assert not w.is_running()
    (tmp_path / "Late.2020.mkv").write_bytes(b"x" * 5)
    wait(qapp, 1000)
    assert w.reported == []
