"""Subtitles: which ones belong to a video, their language/flags, their Plex
names, and that they are moved (and undone) together with their video."""
from pathlib import Path

import pytest

from conftest import make_files
import scanner


# ── language and flags ─────────────────────────────────────────────────────
@pytest.mark.parametrize("name,video,expected", [
    ("Movie.2019.1080p-GRP.en.srt", "Movie.2019.1080p-GRP", ("eng", [])),
    ("Movie.2019.1080p-GRP.srt", "Movie.2019.1080p-GRP", ("eng", [])),
    ("Movie.2019.1080p-GRP.fr.srt", "Movie.2019.1080p-GRP", ("fre", [])),
    ("Movie.2019.1080p-GRP.fra.srt", "Movie.2019.1080p-GRP", ("fre", [])),
    ("Movie.2019.1080p-GRP.deu.srt", "Movie.2019.1080p-GRP", ("ger", [])),
    ("Movie.2019.1080p-GRP.pt-BR.srt", "Movie.2019.1080p-GRP", ("por", [])),
    ("Movie.2019.1080p-GRP.es-419.srt", "Movie.2019.1080p-GRP", ("spa", [])),
    ("Movie.2019.1080p-GRP.zh-Hans.srt", "Movie.2019.1080p-GRP", ("chi", [])),
    ("Movie.2019.1080p-GRP.fr-CA.srt", "Movie.2019.1080p-GRP", ("fre", [])),
    ("Movie.2019.1080p-GRP.ca.srt", "Movie.2019.1080p-GRP", ("cat", [])),
    ("Movie.2019.1080p-GRP.English.Forced.srt", "Movie.2019.1080p-GRP", ("eng", ["forced"])),
    ("Movie.2019.1080p-GRP.eng.hi.srt", "Movie.2019.1080p-GRP", ("eng", ["sdh"])),
    ("Movie.2019.1080p-GRP.en.sdh.forced.srt", "Movie.2019.1080p-GRP", ("eng", ["sdh", "forced"])),
    # title words are not tags
    ("The.IT.Crowd.S01E01.720p.srt", "The.IT.Crowd.S01E01.720p", ("eng", [])),
    ("Hi.Score.Girl.S01E01.srt", "Hi.Score.Girl.S01E01", ("eng", [])),
    ("La.Casa.de.Papel.S01E01.srt", "La.Casa.de.Papel.S01E01", ("eng", [])),
    # not named after the video (Subs folders, language-only names)
    ("2_English.srt", None, ("eng", [])),
    ("4_Spanish (Latin America).srt", None, ("spa", [])),
    ("5_French (Canada).srt", None, ("fre", [])),
    ("6_Portuguese (Brazil).srt", None, ("por", [])),
    ("7_Chinese (Traditional).srt", None, ("chi", [])),
    ("8_English (SDH).srt", None, ("eng", ["sdh"])),
    ("English [Forced].srt", None, ("eng", ["forced"])),
    ("Español.srt", None, ("spa", [])),
    ("Show.S01E01.720p.HDTV.x264-GRP.ita.srt", None, ("ita", [])),
    # titles alone say nothing about the language
    ("IT.2017.srt", None, ("eng", [])),
    ("French.Kiss.1995.720p.srt", None, ("eng", [])),
    ("Us.2019.srt", None, ("eng", [])),
])
def test_language_and_flags(name, video, expected):
    assert scanner.detect_subtitle_language(Path(name), video) == expected


def test_language_only_names():
    assert scanner.is_language_only_name("English")
    assert scanner.is_language_only_name("2_eng")
    assert scanner.is_language_only_name("English (SDH)")
    assert not scanner.is_language_only_name("Movie.2019.1080p")
    assert not scanner.is_language_only_name("Subs")


def test_same_language_twice_is_numbered_before_the_language():
    taken = set()
    names = [scanner.unique_subtitle_filename("Movie (2019)", "eng", ".srt", [], taken) for _ in range(3)]
    assert names == ["Movie (2019).eng.srt", "Movie (2019).2.eng.srt", "Movie (2019).3.eng.srt"]


# ── which subtitles belong to a video ──────────────────────────────────────
def _names(paths, root):
    return sorted(str(p.relative_to(root)).replace("\\", "/") for p in paths)


def test_language_only_names_next_to_a_single_video(main, tmp_path):
    video, = make_files(tmp_path, "Up.2009.1080p/Up.2009.1080p.mkv")
    make_files(tmp_path, "Up.2009.1080p/English.srt", "Up.2009.1080p/Spanish.srt",
               "Up.2009.1080p/notes.srt")
    assert _names(main.find_subtitle_siblings(video), tmp_path) == [
        "Up.2009.1080p/English.srt", "Up.2009.1080p/Spanish.srt"]


def test_subs_folder_name_in_any_case(main, tmp_path):
    video, = make_files(tmp_path, "Heat.1995/Heat.1995.mkv")
    make_files(tmp_path, "Heat.1995/SUBS/Heat.1995.eng.srt", "Heat.1995/SubTitles/2_English.srt")
    assert _names(main.find_subtitle_siblings(video), tmp_path) == [
        "Heat.1995/SUBS/Heat.1995.eng.srt", "Heat.1995/SubTitles/2_English.srt"]


def test_season_pack_subs_with_short_names(main, tmp_path):
    e1, e2 = make_files(tmp_path, "Lost.S01/Lost.S01E01.720p.BluRay.x264-GRP.mkv",
                        "Lost.S01/Lost.S01E02.720p.BluRay.x264-GRP.mkv")
    make_files(tmp_path, "Lost.S01/Subs/Lost.S01E01.eng.srt", "Lost.S01/Subs/Lost.S01E02.eng.srt",
               "Lost.S01/Subs/S01E02/3_Spanish.srt")
    assert _names(main.find_subtitle_siblings(e1), tmp_path) == ["Lost.S01/Subs/Lost.S01E01.eng.srt"]
    assert _names(main.find_subtitle_siblings(e2), tmp_path) == [
        "Lost.S01/Subs/Lost.S01E02.eng.srt", "Lost.S01/Subs/S01E02/3_Spanish.srt"]


def test_episode_number_alone_does_not_claim_another_shows_subtitle(main, tmp_path):
    video, = make_files(tmp_path, "dl/ShowB.S01E01.mkv")
    make_files(tmp_path, "dl/ShowC.S01E01.srt")
    assert main.find_subtitle_siblings(video) == []


def test_subtitle_goes_to_the_longest_matching_video(main, tmp_path):
    plain, special = make_files(tmp_path, "Aliens/Aliens.1986.mkv", "Aliens/Aliens.1986.Special.Edition.mkv")
    make_files(tmp_path, "Aliens/Aliens.1986.en.srt", "Aliens/Aliens.1986.Special.Edition.en.srt")
    assert _names(main.find_subtitle_siblings(plain), tmp_path) == ["Aliens/Aliens.1986.en.srt"]
    assert _names(main.find_subtitle_siblings(special), tmp_path) == ["Aliens/Aliens.1986.Special.Edition.en.srt"]


def test_vobsub_pgs_and_sami_are_picked_up(main, tmp_path):
    video, = make_files(tmp_path, "Alien/Alien.1979.avi")
    make_files(tmp_path, "Alien/Alien.1979.idx", "Alien/Alien.1979.sub", "Alien/Alien.1979.eng.sup",
               "Alien/Alien.1979.kor.smi")
    assert len(main.find_subtitle_siblings(video)) == 4


# ── names in the library ───────────────────────────────────────────────────
def test_rarbg_subs_folder_names(main, tmp_path):
    rel = "Dune.2021.1080p.WEBRip.x264-RARBG"
    video, = make_files(tmp_path, f"{rel}/{rel}.mp4")
    make_files(tmp_path, *(f"{rel}/Subs/{n}" for n in [
        "2_English.srt", "3_English.srt", "4_Spanish (Latin America).srt", "5_French (Canada).srt",
        "8_English (SDH).srt"]))
    cand = main.MatchEngine(cfg=dict(main.DEFAULTS)).match(video, "movie")
    assert sorted(Path(d).name for _, d in cand.subtitles) == [
        "Dune (2021) 1080p H.264.2.eng.srt", "Dune (2021) 1080p H.264.eng.sdh.srt",
        "Dune (2021) 1080p H.264.eng.srt", "Dune (2021) 1080p H.264.fre.srt",
        "Dune (2021) 1080p H.264.spa.srt"]
    assert all(d.startswith("Dune (2021)/") for _, d in cand.subtitles)


def test_subtitles_can_be_switched_off(main, tmp_path):
    video, _ = make_files(tmp_path, "Movie.2019/Movie.2019.mkv", "Movie.2019/Movie.2019.en.srt")
    cfg = dict(main.DEFAULTS, rename_subtitles=False)
    assert main.MatchEngine(cfg=cfg).match(video, "movie").subtitles == []


def test_subtitle_added_with_its_video_is_done_once(main, tmp_path):
    video, sub = make_files(tmp_path / "in", "Show.S01E01.mkv", "Show.S01E01.en.srt")
    worker = main.ScanWorker([sub, video], main.MatchEngine(cfg=dict(main.DEFAULTS)), "tv", str(tmp_path / "lib"))
    got = []
    worker.candidate_ready.connect(got.append)
    worker.run()
    assert [c.source for c in got] == [video]
    assert [s for s, _ in got[0].subtitles] == [sub]


def test_subtitle_only_list_gets_plex_names(main, tmp_path):
    subs = make_files(tmp_path / "in", "Show.S01E01.720p.HDTV.x264-GRP.en.srt",
                      "Show.S01E01.720p.HDTV.x264-GRP.eng.srt", "Show.S01E02.720p.HDTV.x264-GRP.es.srt")
    worker = main.ScanWorker(subs, main.MatchEngine(cfg=dict(main.DEFAULTS)), "tv", str(tmp_path / "lib"))
    got = []
    worker.candidate_ready.connect(got.append)
    worker.run()
    assert [c.dest_rel for c in got] == [
        "Show/Season 01/Show S01E01 - Episode 1.eng.srt",
        "Show/Season 01/Show S01E01 - Episode 1.2.eng.srt",
        "Show/Season 01/Show S01E02 - Episode 2.spa.srt"]


# ── moving files ───────────────────────────────────────────────────────────
def test_subtitles_are_not_moved_without_their_video(main, tmp_path):
    video, sub, taken = make_files(tmp_path, "in/a.mkv", "in/a.en.srt", "lib/b.mkv")
    r = main.Renamer()
    res = r.execute_groups([((video, taken), [(sub, tmp_path / "lib" / "b.eng.srt")])])
    assert len(res.failed) == 2 and video.exists() and sub.exists()
    assert "video was not moved" in res.failed[1].error


@pytest.fixture
def window(main, qapp, tmp_path, monkeypatch):
    """The real main window, offline, with dialogs answered "Yes"."""
    from PyQt6.QtWidgets import QMessageBox
    lib = tmp_path / "lib"
    cfg = dict(main.DEFAULTS, first_run=False, media_type="movie", root_movie=str(lib),
               use_tvmaze=False, use_anidb=False, use_cinemeta=False, last_source_folder="")
    monkeypatch.setattr(main, "cfg_load", lambda: dict(cfg))
    monkeypatch.setattr(main, "cfg_save", lambda c: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    w = main.MainWindow()
    yield w
    w.close()


def test_rename_and_undo_from_the_window(main, window, tmp_path):
    rel = "Arrival.2016.1080p.BluRay.x264-GRP"
    video, = make_files(tmp_path / "dl", f"{rel}/{rel}.mkv")
    subs = make_files(tmp_path / "dl", f"{rel}/{rel}.en.srt", f"{rel}/{rel}.English.Forced.srt",
                      f"{rel}/Subs/3_Spanish.srt")
    window.src.setText(str(tmp_path / "dl"))
    window._add_files_to_list([video])
    worker = main.ScanWorker([video], main.MatchEngine(cfg=window.cfg), "movie", window._get_destination())
    worker.candidate_ready.connect(window._on_candidate)
    worker.run()
    window._rename()

    movie_dir = tmp_path / "lib" / "Arrival (2016)"
    assert sorted(p.name for p in movie_dir.iterdir()) == [
        "Arrival (2016) 1080p H.264.eng.forced.srt", "Arrival (2016) 1080p H.264.eng.srt",
        "Arrival (2016) 1080p H.264.mkv", "Arrival (2016) 1080p H.264.spa.srt"]
    assert not (tmp_path / "dl" / rel).exists()            # emptied release folder cleaned up
    assert "3 subtitle(s)" in window.status.currentMessage()

    window._undo()
    assert video.exists() and all(s.exists() for s in subs)
    assert not movie_dir.exists()


def test_one_episode_from_a_season_pack_takes_only_its_subtitles(main, tmp_path):
    # Only E05 was downloaded, but the pack's Subs folder has every episode
    video, = make_files(tmp_path, "Severance.S01/Severance.S01E05.1080p.WEB-DL.mkv")
    make_files(tmp_path, *(f"Severance.S01/Subs/Severance.S01E0{n}.1080p.WEB-DL/2_English.srt" for n in (1, 2, 5)))
    make_files(tmp_path, "Severance.S01/Subs/Severance.S01E01.eng.srt", "Severance.S01/Subs/Severance.S01E05.eng.srt")
    assert _names(main.find_subtitle_siblings(video), tmp_path) == [
        "Severance.S01/Subs/Severance.S01E05.1080p.WEB-DL/2_English.srt",
        "Severance.S01/Subs/Severance.S01E05.eng.srt"]


def test_similar_show_names_are_different_shows(main, tmp_path):
    lost, who = make_files(tmp_path, "dl/Lost.S01E01.mkv", "dl/Doctor.Who.2005.S06E13.mkv")
    make_files(tmp_path, "dl/Lost.Girl.S01E01.en.srt", "dl/Subs/Doctor.Who.S06E13.eng.srt")
    assert main.find_subtitle_siblings(lost) == []
    assert _names(main.find_subtitle_siblings(who), tmp_path) == ["dl/Subs/Doctor.Who.S06E13.eng.srt"]
