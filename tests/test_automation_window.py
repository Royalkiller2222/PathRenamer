"""Automation in the window: the watch folder, Pro switches, subtitles and
Plex after renaming, the free manual tools."""
from pathlib import Path

import pytest

from conftest import make_files, run_preview, wait, wait_until
from test_automation import FakePlex, FakeSubs
from opensubs import SubtitleResult


def _watching(w, qapp, folder):
    """Start the window's watch folder with short waits for tests."""
    w.watcher.settle_seconds = 0.2
    w.watcher.poll_seconds = 0.2
    w.watcher.stop()
    assert w.watcher.start(folder)
    wait(qapp, 300)


@pytest.fixture
def watch_window(make_window, tmp_path):
    def make(**cfg):
        (tmp_path / "dl").mkdir(exist_ok=True)
        cfg = dict(dict(watch_enabled=True, watch_folder=str(tmp_path / "dl"), watch_auto_rename=True,
                        root_movie=str(tmp_path / "movies"), root_tv=str(tmp_path / "tv"), use_anidb=False),
                   **cfg)
        return make_window(**cfg)
    return make


def test_watch_folder_renames_sure_matches(watch_window, qapp, tmp_path):
    w = watch_window()
    assert w.watcher.is_running() and w.watch_label.text() == "Watching dl" and w.act_watch.isChecked()
    _watching(w, qapp, tmp_path / "dl")
    make_files(tmp_path / "dl", "Arrival.2016.1080p.BluRay.x264-GRP/Arrival.2016.1080p.BluRay.x264-GRP.mkv")
    (tmp_path / "dl" / "Arrival.2016.1080p.BluRay.x264-GRP" / "Arrival.2016.1080p.BluRay.x264-GRP.mkv").write_bytes(b"x")
    target = tmp_path / "movies" / "Arrival (2016)" / "Arrival (2016) 1080p H.264.mkv"
    assert wait_until(qapp, target.exists, timeout=10)
    assert "Watch folder: Renamed 1 file(s)" in w.status.currentMessage()
    assert not (tmp_path / "dl" / "Arrival.2016.1080p.BluRay.x264-GRP").exists()   # emptied folder removed
    assert w.renamer.history.batches()[0]["source"] == "watch"
    # undo puts it back, and the watch folder doesn't pick it up again
    w._undo()
    wait(qapp, 1200)
    assert not target.exists() and w.table.rowCount() == 0


def test_unsure_files_go_to_the_preview(watch_window, qapp, tmp_path):
    w = watch_window()
    _watching(w, qapp, tmp_path / "dl")
    (tmp_path / "dl" / "Some Home Video.mkv").write_bytes(b"x")          # no year: not sure enough
    assert wait_until(qapp, lambda: w.table.rowCount() == 1, timeout=10)
    assert w._media_type() == "movie" and w.btn_rename.isEnabled()
    assert "1 new file(s) to check in the preview" in w.status.currentMessage()
    (tmp_path / "dl" / "Show.S01E01.mkv").write_bytes(b"x")              # the preview is busy now
    assert wait_until(qapp, lambda: w.file_list.count() == 2, timeout=10)
    assert w.table.rowCount() == 1


def test_episodes_are_detected_by_name(watch_window, qapp, tmp_path):
    w = watch_window(watch_auto_rename=False)
    assert w._watch_media_type(Path("The.Office.S02E05.mkv")) == "tv"
    assert w._watch_media_type(Path("Dune.Part.Two.2024.2160p.mkv")) == "movie"
    assert w._watch_media_type(Path("[SubsPlease] Frieren - 12 (1080p).mkv")) == "tv"
    w.cfg["watch_media_type"] = "anime"
    assert w._watch_media_type(Path("Inception.2010.mkv")) == "anime"


def test_automatic_renaming_needs_pro_after_licensing_starts(watch_window, qapp, tmp_path, monkeypatch):
    import licensing
    monkeypatch.setattr(licensing, "POLAR_ORGANIZATION_ID", "org")
    w = watch_window(trial_started=1.0)                                  # trial long over
    _watching(w, qapp, tmp_path / "dl")
    (tmp_path / "dl" / "Heat.1995.mkv").write_bytes(b"x")
    assert wait_until(qapp, lambda: w.table.rowCount() == 1, timeout=10)
    assert "part of PathRenamer Pro" in w.status.currentMessage()
    assert (tmp_path / "dl" / "Heat.1995.mkv").exists()


def test_after_rename_downloads_subtitles_and_scans_plex(make_window, qapp, tmp_path):
    w = make_window(subs_auto=True, plex_auto_scan=True, subs_languages="English, Spanish")
    subs, plex = FakeSubs([SubtitleResult(5, "spa")]), FakePlex()
    w._subtitle_client = lambda: subs
    w._plex_client = lambda: plex
    video, _ = make_files(tmp_path / "dl", "Heat.1995.mkv", "Heat.1995.en.srt")
    run_preview(__import__("main"), w, [video])
    w._rename()
    assert wait_until(qapp, lambda: w.jobs.running == 0 and plex.scans, timeout=5)
    spanish = tmp_path / "lib" / "Heat (1995)" / "Heat (1995).spa.srt"
    assert spanish.read_bytes() == b"subtitle 5"
    assert subs.searches[0]["languages"] == ["spa"]                       # English came with the video
    assert plex.scans[0][1] == "movie" and "Heat (1995).spa.srt" in plex.scans[0][0]
    assert "downloaded 1 subtitle(s) · Plex is scanning Movies" in w.status.currentMessage()
    w._undo()                                                             # the download is undone too
    assert not spanish.exists() and video.exists()


def test_switches_off_means_nothing_runs(make_window, qapp, tmp_path):
    w = make_window()
    w._subtitle_client = lambda: pytest.fail("no subtitles without the switch")
    w._plex_client = lambda: pytest.fail("no Plex without the switch")
    run_preview(__import__("main"), w, make_files(tmp_path / "dl", "Heat.1995.mkv"))
    w._rename()
    wait(qapp, 100)
    assert w.jobs.running == 0


def test_download_subtitles_by_hand(make_window, qapp, tmp_path):
    w = make_window(subs_languages="English")
    subs = FakeSubs([SubtitleResult(8, "eng")])
    w._subtitle_client = lambda: subs
    video = make_files(tmp_path / "dl", "Heat.1995.1080p.mkv")[0]
    run_preview(__import__("main"), w, [video])
    w.table.selectRow(0)
    assert w.act_download_subs.isEnabled()
    w._download_subtitles()
    assert wait_until(qapp, lambda: w.jobs.running == 0 and not w._scanning, timeout=10)
    assert (tmp_path / "dl" / "Heat.1995.1080p.eng.srt").read_bytes() == b"subtitle 8"
    cand = w.table.get_cands()[0]                                         # renamed along with the video
    assert [Path(d).name for _, d in cand.subtitles] == ["Heat (1995) 1080p.eng.srt"]
    assert w.status.currentMessage().startswith("Downloaded 1 subtitle(s)  ·  1 file(s) matched")


def test_tools_need_setting_up_first(make_window, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    w = make_window()
    opened = []
    monkeypatch.setattr(w, "_settings", lambda page=None: opened.append(page))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    w._toggle_watch(True)
    w._scan_plex_now()
    assert opened == ["Automation", "Automation"] and not w.watcher.is_running()


def test_settings_save_automation(main, qapp, monkeypatch, tmp_path):
    from PyQt6.QtWidgets import QMessageBox
    import licensing
    monkeypatch.setattr(main, "cfg_save", lambda c: None)
    monkeypatch.setattr(licensing, "POLAR_ORGANIZATION_ID", "org")
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2]))
    d = main.SettingsDialog(dict(main.DEFAULTS), page="Pro")
    assert d.nav.currentRow() == 7 and "try Pro free for 14 days" in d.pro_status.text()
    d.watch_enabled.setChecked(True)
    d.watch_folder.setText(str(tmp_path))
    d.watch_type.setCurrentIndex(2)
    d.watch_auto.setChecked(True)
    d.plex_url.setText("http://localhost:32400")
    d.plex_token.setText("tok")
    d.subs_languages.setText("en, spanish, klingon")
    d._save()
    c = d.cfg
    assert (c["watch_enabled"], c["watch_media_type"], c["watch_auto_rename"]) == (True, "movie", True)
    assert c["subs_languages"] == "English, Spanish" and c["plex_url"] == "http://localhost:32400"
    assert main.unprotect(c["plex_token"]) == "tok"
    assert c["trial_started"] and "trial has started" in shown[0]


def test_pro_switches_lock_after_the_trial(main, qapp, monkeypatch):
    import licensing
    monkeypatch.setattr(licensing, "POLAR_ORGANIZATION_ID", "org")
    d = main.SettingsDialog(dict(main.DEFAULTS, trial_started=1.0, watch_auto_rename=True))
    assert not d.watch_auto.isEnabled() and not d.watch_auto.isChecked()
    assert "trial has ended" in d.pro_status.text()
    early = main.SettingsDialog(dict(main.DEFAULTS))
    monkeypatch.setattr(licensing, "POLAR_ORGANIZATION_ID", "")
    early = main.SettingsDialog(dict(main.DEFAULTS))
    assert early.watch_auto.isEnabled() and early.pro_key_box.isHidden()
