"""Fixing single rows in the preview: edit the name, change the episode, pick
another match, move rows and number them in list order."""
from pathlib import Path

from conftest import make_files, run_preview, wait_until


def _rows(w):
    return [c.dest_rel for c in w.table.get_cands()]


def _rematch_and_wait(qapp, w, *args, **kwargs):
    w._rematch(*args, **kwargs)
    assert wait_until(qapp, lambda: not w._scanning, timeout=10)


def test_number_files_without_episode_numbers_in_list_order(main, make_window, qapp, tmp_path):
    w = make_window(media_type="tv", root_tv=str(tmp_path / "tv"))
    files = make_files(tmp_path / "dl", "The Office/Season 2/The Dundies.mkv",
                       "The Office/Season 2/Sexual Harassment.mkv", "The Office/Season 2/Office Olympics.mkv")
    run_preview(main, w, files)
    assert all("episode" in c.error for c in w.table.get_cands())
    w.table.selectAll()
    _rematch_and_wait(qapp, w, [0, 1, 2], title="The Office", season=2, episodes=[[1], [2], [3]])
    cands = w.table.get_cands()
    assert [c.source.name for c in cands] == [f.name for f in files]      # rows stay in place
    assert [c.dest_rel for c in cands] == [f"The Office/Season 02/The Office S02E0{n} - Episode {n}.mkv"
                                           for n in (1, 2, 3)]
    assert w.btn_rename.isEnabled()


def test_move_rows_then_number_them(main, make_window, qapp, tmp_path):
    w = make_window(media_type="tv", root_tv=str(tmp_path / "tv"))
    files = make_files(tmp_path / "dl", "Show/Part Two.mkv", "Show/Part One.mkv")
    run_preview(main, w, files)
    w.table.selectRow(1)
    w._move_rows(-1)
    assert [c.source.name for c in w.table.get_cands()] == ["Part One.mkv", "Part Two.mkv"]
    assert w.table.selected_rows() == [0]
    _rematch_and_wait(qapp, w, [0, 1], title="Show", season=1, episodes=[[1], [2]])
    assert _rows(w) == ["Show/Season 01/Show S01E01 - Episode 1.mkv", "Show/Season 01/Show S01E02 - Episode 2.mkv"]


def test_change_episode_of_one_file(main, make_window, qapp, tmp_path):
    w = make_window(media_type="tv", root_tv=str(tmp_path / "tv"))
    video, sub = make_files(tmp_path / "dl", "Show.S01E01.mkv", "Show.S01E01.en.srt")
    run_preview(main, w, [video])
    _rematch_and_wait(qapp, w, [0], season=2, episodes=[[5, 6]])
    cand = w.table.get_cands()[0]
    assert cand.dest_rel == "Show/Season 02/Show S02E05-E06 - Episode 5.mkv"
    assert [Path(d).name for _, d in cand.subtitles] == ["Show S02E05-E06 - Episode 5.eng.srt"]


def test_choose_a_different_show(main, make_window, qapp, tmp_path):
    w = make_window(media_type="tv", root_tv=str(tmp_path / "tv"))
    files = make_files(tmp_path / "dl", "Shw.S01E01.mkv", "Shw.S01E02.mkv")
    run_preview(main, w, files)
    _rematch_and_wait(qapp, w, [0, 1], match=("The Show", ("tvmaze", 1)))
    assert _rows(w) == ["The Show/Season 01/The Show S01E01 - Episode 1.mkv",
                        "The Show/Season 01/The Show S01E02 - Episode 2.mkv"]
    # a later episode change keeps the chosen show
    _rematch_and_wait(qapp, w, [1], season=1, episodes=[[3]])
    assert _rows(w)[1] == "The Show/Season 01/The Show S01E03 - Episode 3.mkv"


def test_edit_the_new_name_by_hand(main, make_window, qapp, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QInputDialog
    w = make_window()
    video, _ = make_files(tmp_path / "dl", "Movie.2019.mkv", "Movie.2019.en.srt")
    run_preview(main, w, [video])
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("My Movies/Home: Video?", True))
    w.table.selectRow(0)
    w._edit_name()
    cand = w.table.get_cands()[0]
    assert cand.dest_rel == "My Movies/Home Video.mkv" and cand.manual and cand.confidence == 1.0
    assert [Path(d).name for _, d in cand.subtitles] == ["Home Video.eng.srt"]


def test_reordering_decides_which_duplicate_keeps_the_name(main, make_window, tmp_path):
    w = make_window(media_type="tv", root_tv=str(tmp_path / "tv"))
    files = make_files(tmp_path / "dl", "Show.S01E01.720p.mkv", "Show.S01E01.1080p.mkv")
    run_preview(main, w, files)
    first, second = w.table.get_cands()
    assert not first.error and second.error.startswith(main.DUPLICATE_PREFIX)
    w.table.selectRow(1)
    w._move_rows(-1)
    first, second = w.table.get_cands()
    assert first.source.name == "Show.S01E01.1080p.mkv" and not first.error
    assert second.error.startswith(main.DUPLICATE_PREFIX)


def test_fix_actions_follow_the_selection(main, make_window, tmp_path):
    w = make_window()
    run_preview(main, w, make_files(tmp_path / "dl", "A.2001.mkv", "B.2002.mkv"))
    w.table.clearSelection()
    assert not w.act_edit_name.isEnabled() and not w.btn_fix.isEnabled()
    w.table.selectRow(1)
    assert w.act_edit_name.isEnabled() and w.act_move_up.isEnabled() and not w.act_move_down.isEnabled()
    assert not w.act_number.isEnabled()                 # movies have no episodes
