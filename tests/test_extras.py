"""Extras (featurettes, deleted scenes, trailers, ...) go into their movie's
or show's folder, in the subfolder Plex, Jellyfin and Emby read them from."""
from pathlib import Path

import pytest

import core
from conftest import make_files
from core import MatchEngine, Renamer, RenameHistory, extra_kind, extra_name, match_files, place_groups, rename_groups


def _engine(**cfg):
    return MatchEngine(cfg=dict(core.DEFAULTS, read_media_info=False, **cfg))


def _match(files, media_type, dst, **cfg):
    return list(match_files(files, _engine(**cfg), media_type, str(dst)))


@pytest.mark.parametrize("name, kind, title", [
    ("Dune.2021.1080p/Featurettes/Dune.2021.Featurette.The.Spice.Must.Flow.1080p.BluRay.x264-GRP.mkv",
     "Featurettes", "The Spice Must Flow"),
    ("Dune.2021.1080p/Extras/Deleted Scene 1.mkv", "Deleted Scenes", "Deleted Scene 1"),
    ("Dune.2021.1080p/Extras/Teaser Trailer.mkv", "Trailers", "Teaser Trailer"),
    ("Dune.2021.1080p/Extras/Featurette - Arrakis.mkv", "Featurettes", "Arrakis"),
    ("Dune.2021.1080p/Extras/Cast Q&A.mkv", "Other", "Cast Q&A"),
    ("Dune.2021.1080p/Behind the Scenes/Day 1.mkv", "Behind The Scenes", "Day 1"),
    ("Dune.2021.1080p/Dune.2021.Behind.The.Scenes.1080p.mkv", "Behind The Scenes", "Behind The Scenes"),
    ("Dune (2021)/Dune (2021)-trailer.mkv", "Trailers", "Trailer"),
])
def test_what_an_extra_is_and_its_name(name, kind, title):
    assert extra_kind(Path(name))[0] == kind
    assert extra_name(Path(name), "Dune", "2021", kind) == title


@pytest.mark.parametrize("name", ["Movies/The.Interview.2014.1080p.mkv", "Trailer.Park.Boys.S01E01.mkv",
                                  "Show.S01/Specials/Show.S00E01.mkv", "Scenes.From.A.Marriage.S01E01.mkv"])
def test_titles_that_only_sound_like_extras(name):
    assert extra_kind(Path(name)) == (None, None)


def test_movie_extras_go_into_its_folder(tmp_path):
    rel = "Heat.1995.1080p.BluRay.x264-GRP"
    files = make_files(tmp_path / "dl", f"{rel}/{rel}.mkv",
                       f"{rel}/Featurettes/Heat.1995.Featurette.Pacino.and.De.Niro.1080p.mkv",
                       f"{rel}/Featurettes/Heat.1995.Featurette.The.Score.1080p.mkv",
                       f"{rel}/Deleted Scenes/Scene 1.mkv",
                       f"{rel}/Heat.1995.Behind.The.Scenes.1080p.mkv")
    cands = {c.source.name: c for c in _match(files, "movie", tmp_path / "lib")}
    assert cands[f"{rel}.mkv"].dest_rel == "Heat (1995)/Heat (1995) 1080p H.264.mkv"
    assert cands["Heat.1995.Featurette.Pacino.and.De.Niro.1080p.mkv"].dest_rel == \
        "Heat (1995)/Featurettes/Pacino and De Niro.mkv"
    assert cands["Heat.1995.Featurette.The.Score.1080p.mkv"].dest_rel == "Heat (1995)/Featurettes/The Score.mkv"
    assert cands["Scene 1.mkv"].dest_rel == "Heat (1995)/Deleted Scenes/Scene 1.mkv"
    assert cands["Heat.1995.Behind.The.Scenes.1080p.mkv"].dest_rel == \
        "Heat (1995)/Behind The Scenes/Behind The Scenes.mkv"
    assert all(not c.error for c in cands.values())
    assert cands["Scene 1.mkv"].extra == "Deleted Scenes" and cands["Scene 1.mkv"].title == "Heat"


def test_tv_extras_go_into_the_show_folder(tmp_path):
    files = make_files(tmp_path / "dl", "Show.S01.1080p/Show.S01E01.1080p.mkv",
                       "Show.S01.1080p/Show.S01E02.1080p.mkv", "Show.S01.1080p/Extras/Gag Reel.mkv",
                       "Show.S01.1080p/Extras/Gag Reel 2.mkv")
    cands = {c.source.name: c for c in _match(files, "tv", tmp_path / "lib")}
    assert cands["Gag Reel.mkv"].dest_rel == "Show/Featurettes/Gag Reel.mkv"
    assert cands["Gag Reel 2.mkv"].dest_rel == "Show/Featurettes/Gag Reel 2.mkv"


def test_two_extras_with_the_same_name_both_fit(tmp_path):
    rel = "Heat.1995.1080p"
    files = make_files(tmp_path / "dl", f"{rel}/{rel}.mkv", f"{rel}/Trailers/Heat.1995.Trailer.1080p.mkv",
                       f"{rel}/Trailers/Heat.1995.Trailer.720p.mkv")
    dests = sorted(c.dest_rel for c in _match(files, "movie", tmp_path / "lib") if c.extra)
    assert dests == ["Heat (1995)/Trailers/Trailer 2.mkv", "Heat (1995)/Trailers/Trailer.mkv"]


def test_an_extra_without_its_movie_waits(tmp_path):
    files = make_files(tmp_path / "dl", "Heat.1995.1080p/Featurettes/The Score.mkv")
    cand, = _match(files, "movie", tmp_path / "lib")
    assert "add its movie" in cand.error and cand.extra == "Featurettes"


def test_a_making_of_film_on_its_own_is_just_a_movie(tmp_path):
    files = make_files(tmp_path / "dl", "The.Making.of.Star.Wars.1977.1080p.mkv")
    cand, = _match(files, "movie", tmp_path / "lib")
    assert not cand.extra and not cand.error and cand.dest_rel.startswith("The Making of Star Wars (1977)/")


def test_extras_can_be_switched_off(tmp_path):
    rel = "Heat.1995.1080p"
    files = make_files(tmp_path / "dl", f"{rel}/{rel}.mkv", f"{rel}/Featurettes/The Score.mkv")
    cands = {c.source.name: c for c in _match(files, "movie", tmp_path / "lib", handle_extras=False)}
    assert "extras are turned off" in cands["The Score.mkv"].error
    assert not cands[f"{rel}.mkv"].error


def test_extras_need_a_folder_per_movie(tmp_path):
    rel = "Heat.1995.1080p"
    files = make_files(tmp_path / "dl", f"{rel}/{rel}.mkv", f"{rel}/Featurettes/The Score.mkv")
    cands = {c.source.name: c for c in _match(files, "movie", tmp_path / "lib", format_movie="{title} ({year})")}
    assert "folder per movie" in cands["The Score.mkv"].error


def test_rename_and_undo_take_the_extras_along(tmp_path):
    rel = "Heat.1995.1080p"
    files = make_files(tmp_path / "dl", f"{rel}/{rel}.mkv", f"{rel}/Featurettes/The Score.mkv")
    dst = tmp_path / "lib"
    cands = _match(files, "movie", dst)
    renamer = Renamer(RenameHistory(tmp_path / "history.json"))
    result, msg = place_groups(renamer, rename_groups(cands, dst), dst, cleanup_root=tmp_path / "dl")
    assert not result.failed and "Renamed 2 file(s)" in msg
    assert (dst / "Heat (1995)" / "Featurettes" / "The Score.mkv").exists()
    assert not (tmp_path / "dl" / rel).exists()                            # emptied release folder removed
    renamer.undo_last()
    assert all(f.exists() for f in files) and not (dst / "Heat (1995)").exists()


def test_settings_switches(main, qapp, monkeypatch):
    monkeypatch.setattr(main, "cfg_save", lambda c: None)
    d = main.SettingsDialog(dict(main.DEFAULTS), page="Options")
    assert d.handle_extras.isChecked() and not d.match_by_title.isChecked()
    d.handle_extras.setChecked(False)
    d.match_by_title.setChecked(True)
    d._save()
    assert d.cfg["handle_extras"] is False and d.cfg["episode_match"] == "title"


def test_the_preview_says_what_an_extra_is(main, make_window, tmp_path):
    w = make_window()
    rel = "Heat.1995.1080p"
    files = make_files(tmp_path / "dl", f"{rel}/{rel}.mkv", f"{rel}/Featurettes/The Score.mkv")
    w.table.populate(_match(files, "movie", tmp_path / "lib"), str(tmp_path / "lib"))
    labels = [w.table.item(r, 1).text() for r in range(w.table.rowCount())]
    assert "Featurettes · Heat (1995)" in labels
