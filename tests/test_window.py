"""The main window: Modern and Classic layouts, switching between them,
the details panel, Settings → Layout, drag & drop."""
import time

import pytest

from conftest import make_files


@pytest.fixture
def make_window(main, qapp, tmp_path, monkeypatch):
    """Build real main windows (offline, nothing written to the real config)."""
    from PyQt6.QtWidgets import QMessageBox
    saved = {}
    windows = []
    monkeypatch.setattr(main, "cfg_save", lambda c: saved.update(c))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)

    def factory(**overrides):
        cfg = dict(main.DEFAULTS, first_run=False, media_type="movie", root_movie=str(tmp_path / "lib"),
                   use_tvmaze=False, use_anidb=False, use_cinemeta=False, last_source_folder="")
        cfg.update(overrides)
        monkeypatch.setattr(main, "cfg_load", lambda: dict(cfg))
        w = main.MainWindow()
        windows.append(w)
        return w
    factory.saved = saved
    yield factory
    for w in windows:
        w.close()


def _preview(main, w, files):
    """Run the matcher like Preview does, without the network or dialogs."""
    w._add_files_to_list(files)
    worker = main.ScanWorker(files, main.MatchEngine(cfg=w.cfg), w._media_type(), w._get_destination())
    worker.candidate_ready.connect(w._on_candidate)
    w._scanning = True
    worker.run()
    w._on_scan_done()


def _wait(qapp, ms):
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)


def _wait_until(qapp, condition, timeout=3.0):
    """Process events until condition() is true (animations run on a timer)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def test_modern_is_the_default_layout(main, make_window):
    w = make_window()
    assert w._layout_name == "modern"
    assert w.centralWidget().objectName() == "modernRoot"


def test_classic_layout_is_available(main, make_window):
    w = make_window(layout="classic")
    assert w._layout_name == "classic"
    assert w.centralWidget().objectName() != "modernRoot"
    assert w.btn_preview.text() == "⟳  Preview Renames"


def test_switching_layout_keeps_files_and_preview(main, make_window, tmp_path):
    w = make_window()
    files = make_files(tmp_path / "dl", "Arrival.2016.1080p.BluRay.x264-GRP.mkv", "Heat.1995.720p.mkv")
    _preview(main, w, files)
    w.src.setText("typed text")
    for layout in ("classic", "modern", "classic"):
        w._set_layout(layout)
        assert w._layout_name == layout
        assert w.file_list.count() == 2
        assert w.table.rowCount() == 2
        assert w.src.text() == "typed text"
        assert w.btn_rename.isEnabled()
    assert make_window.saved["layout"] == "classic"


def test_details_panel_shows_where_subtitles_go(main, make_window, tmp_path):
    w = make_window()
    rel = "Arrival.2016.1080p.BluRay.x264-GRP"
    video, _ = make_files(tmp_path / "dl", f"{rel}/{rel}.mkv", f"{rel}/{rel}.en.srt")
    _preview(main, w, [video])
    w.table.selectRow(0)
    text = w.details.body.text()
    assert "Arrival (2016) 1080p H.264.mkv" in text
    assert "Arrival (2016) 1080p H.264.eng.srt" in text
    assert "1 subtitle" in w.summary.text()


def test_toolbar_forced_match(main, make_window, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    w = make_window()
    assert w._choose_match_mode() == ("normal", None)
    w.match_combo.setCurrentIndex(1)
    w.tvdb_edit.setText(" 81189 ")
    assert w._choose_match_mode() == ("forced", "81189")
    w.tvdb_edit.setText("")
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    assert w._choose_match_mode() == (None, None)


def test_panels_stack_on_narrow_windows(main, make_window, qapp):
    from PyQt6.QtCore import Qt
    w = make_window()
    w.show()
    w.resize(1200, 760)
    assert _wait_until(qapp, lambda: w._split_main.orientation() == Qt.Orientation.Horizontal)
    w.resize(800, 700)
    assert _wait_until(qapp, lambda: w._split_main.orientation() == Qt.Orientation.Vertical)
    w.cfg["panel_mode"] = "side"
    w._update_panel_orientation()
    assert w._split_main.orientation() == Qt.Orientation.Horizontal


def test_animations_finish(main, make_window, qapp):
    w = make_window()
    w.show(); w.resize(1200, 760); _wait(qapp, 50)
    w._toggle_details(False)
    assert _wait_until(qapp, w.details.isHidden)
    w._toggle_details(True)
    assert _wait_until(qapp, lambda: w._split_preview.sizes()[1] > 100)
    assert not w.details.isHidden()
    w.match_combo.setCurrentIndex(1)
    assert _wait_until(qapp, lambda: w.tvdb_edit.isVisible() and w.tvdb_edit.width() == 130)
    w.match_combo.setCurrentIndex(0)
    assert _wait_until(qapp, w.tvdb_edit.isHidden)


def test_settings_dialog_saves_layout_options(main, qapp, monkeypatch):
    monkeypatch.setattr(main, "cfg_save", lambda c: None)
    d = main.SettingsDialog(dict(main.DEFAULTS))
    assert d.nav is not None and d.nav.count() == 6          # Modern: sidebar
    d.layout_combo.setCurrentIndex(1)
    assert not d.panel_combo.isEnabled()                     # Modern-only options
    d.panel_combo.setCurrentIndex(2)
    d.density_combo.setCurrentIndex(1)
    d.show_details_chk.setChecked(False)
    d._save()
    assert (d.cfg["layout"], d.cfg["panel_mode"], d.cfg["density"], d.cfg["show_details"]) == \
        ("classic", "stacked", "compact", False)
    classic = main.SettingsDialog(dict(main.DEFAULTS, layout="classic"))
    assert classic.nav is None                               # Classic: tabs


def test_drop_adds_only_media_files(main, make_window, tmp_path):
    from PyQt6.QtCore import QMimeData, QPointF, QUrl, Qt
    from PyQt6.QtGui import QDropEvent
    w = make_window()
    files = make_files(tmp_path / "dl", "Movie.2019.mkv", "Movie.2019.en.srt", "Movie.2019.nfo", "poster.jpg")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(f)) for f in files])
    w.dropEvent(QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction, mime,
                           Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    names = sorted(w.file_list.item(i).text() for i in range(w.file_list.count()))
    assert names == ["Movie.2019.en.srt", "Movie.2019.mkv"]


def test_window_closes_cleanly(main, make_window, tmp_path, qapp):
    w = make_window()
    _preview(main, w, make_files(tmp_path / "dl", "Movie.2019.mkv"))
    w.show(); _wait(qapp, 30)
    w.close()
    w.deleteLater(); _wait(qapp, 30)
    assert "window_geometry" in make_window.saved


def test_layout_options_apply_without_switching(main, make_window, qapp):
    from PyQt6.QtCore import Qt
    w = make_window()
    w.show(); w.resize(1200, 760); _wait(qapp, 50)
    w.cfg.update(density="compact", panel_mode="stacked", show_details=False)
    w._apply_layout_options()
    assert w.table.verticalHeader().defaultSectionSize() == 26
    assert _wait_until(qapp, lambda: w._split_main.orientation() == Qt.Orientation.Vertical)
    assert _wait_until(qapp, w.details.isHidden)
    assert not w.act_details.isChecked()
