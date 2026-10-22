"""The main window: Modern and Classic layouts, switching between them,
the details panel, Settings → Layout, drag & drop."""
import time

import pytest

from conftest import make_files, run_preview as _preview, wait as _wait, wait_until as _wait_until


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
    assert w.table.verticalHeader().minimumSectionSize() == 24
    assert _wait_until(qapp, lambda: w._split_main.orientation() == Qt.Orientation.Vertical)
    assert _wait_until(qapp, w.details.isHidden)
    assert not w.act_details.isChecked()


# ── regressions found in review ─────────────────────────────────────────────
def test_classic_shows_list_and_table_after_switching_from_modern(main, make_window, qapp, tmp_path):
    w = make_window()
    _preview(main, w, make_files(tmp_path / "dl", "Movie.2019.mkv"))
    w.show(); _wait(qapp, 30)
    w._set_layout("classic"); _wait(qapp, 30)
    assert w.file_list.isVisible() and w.table.isVisible()
    w._set_layout("modern"); _wait(qapp, 30)
    assert w.file_list.isVisible() and w.table.isVisible()


def test_quick_forced_automatic_forced_keeps_the_id_box(main, make_window, qapp):
    w = make_window()
    w.show(); w.resize(1200, 760); _wait(qapp, 30)
    w.match_combo.setCurrentIndex(1); _wait(qapp, 40)
    w.match_combo.setCurrentIndex(0); _wait(qapp, 40)
    w.match_combo.setCurrentIndex(1)
    assert _wait_until(qapp, lambda: not w._anims)
    assert w.tvdb_edit.isVisible() and w.tvdb_edit.width() == 130


def test_quick_hide_then_show_details_ends_shown(main, make_window, qapp):
    w = make_window()
    w.show(); w.resize(1200, 760); _wait(qapp, 50)
    w._toggle_details(False); _wait(qapp, 40)
    w._toggle_details(True)
    assert _wait_until(qapp, lambda: not w._anims)
    assert not w.details.isHidden() and w._split_preview.sizes()[1] > 100


def test_switching_layout_while_a_panel_slides(main, make_window, qapp):
    w = make_window()
    w.show(); w.resize(1200, 760); _wait(qapp, 50)
    w._toggle_details(False); _wait(qapp, 30)
    w._set_layout("classic")
    _wait(qapp, 400)                     # slot errors would fail the test
    w._set_layout("modern"); _wait(qapp, 100)
    assert not w._anims


def test_restoring_the_window_keeps_pane_sizes(main, make_window, qapp):
    w = make_window()
    w.show(); w.resize(1200, 760); _wait(qapp, 50)
    w._split_main.setSizes([500, 650]); _wait(qapp, 20)
    before = w._split_main.sizes()
    w.hide(); _wait(qapp, 20); w.show(); _wait(qapp, 50)
    assert w._split_main.sizes() == before


def test_presets_save_and_apply(main, make_window, tmp_path):
    w = make_window(media_type="movie", root_movie=str(tmp_path / "movies"), rename_action="move")
    w._save_preset("Plex movies")
    w.cfg.update(media_type="tv", root_tv=str(tmp_path / "tv"), format_movie="{title}", rename_action="hardlink")
    w.combo.setCurrentIndex(0)
    w._save_preset("Seeding TV")
    w._apply_preset("Plex movies")
    assert w._media_type() == "movie" and w.cfg["format_movie"] == main.DEFAULTS["format_movie"]
    assert w.cfg["rename_action"] == "move" and str(tmp_path / "movies") in w.lib_label.text()
    w._apply_preset("Seeding TV")
    assert w._media_type() == "tv" and w.cfg["rename_action"] == "hardlink"
    assert sorted(make_window.saved["presets"]) == ["Plex movies", "Seeding TV"]
    w._fill_presets_menu()
    texts = [a.text() for a in w.presets_menu.actions()]
    assert "Use “Plex movies”" in texts and "Save Current Settings as Preset…" in texts
