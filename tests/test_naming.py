"""Template formatting: what ends up on disk for a parsed episode or movie."""
import datetime

TV = "{show} ({year})/Season {s00}/{show} {s00e00} - {title}"      # the app's default
MOVIE = "{title} ({year})/{title} ({year}) {res} {vc} {ach} {c}"   # the app's default


def test_single_episode(main):
    rel = main.fmt_tv("Breaking Bad", 1, 1, "Pilot", ".mkv", TV, year="2008")
    assert rel == "Breaking Bad (2008)/Season 01/Breaking Bad S01E01 - Pilot.mkv"


def test_no_year_leaves_no_empty_brackets(main):
    rel = main.fmt_tv("Breaking Bad", 1, 1, "Pilot", ".mkv", TV)
    assert rel == "Breaking Bad/Season 01/Breaking Bad S01E01 - Pilot.mkv"


def test_multi_episode_uses_plex_range(main):
    rel = main.fmt_tv("The Simpsons", 1, 1, "Episode 1", ".mkv", TV, episodes=[1, 2, 3])
    assert rel == "The Simpsons/Season 01/The Simpsons S01E01-E03 - Episode 1.mkv"


def test_season_zero_and_episode_zero_are_kept(main):
    rel = main.fmt_tv("Show", 0, 0, "Special", ".mkv", TV)
    assert rel == "Show/Season 00/Show S00E00 - Special.mkv"


def test_date_based_episode(main):
    rel = main.fmt_tv("The Daily Show", None, None, "Guest Name", ".mkv", TV,
                      air_date=datetime.date(2024, 5, 17))
    assert rel == "The Daily Show/Season 2024/The Daily Show 2024-05-17 - Guest Name.mkv"


def test_absolute_numbering_with_multi_episode(main):
    rel = main.fmt_tv("One Piece", 1, 1071, "Title", ".mkv", TV, episode_type="absolute",
                      episodes=[1071, 1072])
    assert rel.endswith("One Piece 1071-1072 - Title.mkv")


def test_illegal_characters_are_removed(main):
    rel = main.fmt_tv('Marvel\'s Agents of S.H.I.E.L.D.: "Pilot"?', 1, 1, "A/B", ".mkv", TV)
    assert ":" not in rel and '"' not in rel and "?" not in rel
    assert rel.count("/") == 2           # "A/B" must not create an extra folder


def test_movie_with_year(main):
    rel = main.fmt_movie("Inception", 2010, ".mkv", MOVIE, "H.264", "DTS", "5.1", "Blu-ray", "1080p")
    assert rel == "Inception (2010)/Inception (2010) 1080p H.264 5.1 DTS.mkv"


def test_movie_without_year_has_no_unknown_placeholder(main):
    rel = main.fmt_movie("Inception", None, ".mkv", MOVIE, resolution="1080p")
    assert rel == "Inception/Inception 1080p.mkv"
    assert "Unknown" not in rel


def test_movie_edition_uses_the_plex_tag(main):
    rel = main.fmt_movie("Aliens", 1986, ".mkv", main.DEFAULTS["format_movie"], resolution="1080p",
                         edition="Special Edition")
    assert rel == "Aliens (1986)/Aliens (1986) {edition-Special Edition} 1080p.mkv"
    assert main.fmt_movie("Aliens", 1986, ".mkv", main.DEFAULTS["format_movie"]) == "Aliens (1986)/Aliens (1986).mkv"


import pytest


@pytest.mark.parametrize("name,expected", [
    ("Aliens.1986.Special.Edition.1080p.BluRay.x264-GRP", "Special Edition"),
    ("Alien.1979.Directors.Cut.1080p", "Director's Cut"),
    ("The.Lord.of.the.Rings.2001.EXTENDED.1080p.BluRay", "Extended Edition"),
    ("Dune.2021.IMAX.2160p.WEB-DL", "IMAX"),
    ("Blade.Runner.1982.The.Final.Cut.1080p", "Final Cut"),
    ("Apocalypse.Now.1979.Redux.720p", "Redux"),
    ("Movie.2019.25th.Anniversary.Edition.1080p", "25th Anniversary Edition"),
    ("Movie (2019) {edition-My Cut}", "My Cut"),
    ("The.Final.Cut.2004.1080p", ""),            # a title, not an edition
    ("Movie.2019.LIMITED.1080p", ""),            # a release type
    ("Movie.2019.1080p.BluRay", ""),
])
def test_detect_edition(main, name, expected):
    import guessit
    raw = dict(guessit.guessit(name + ".mkv", {"type": "movie"}))
    assert main.detect_edition(name, raw) == expected


def test_two_cuts_of_a_movie_get_different_names(main, tmp_path):
    from conftest import make_files
    plain, special = make_files(tmp_path, "Aliens.1986.1080p.mkv", "Aliens.1986.1080p.Special.Edition.mkv")
    engine = main.MatchEngine(cfg=dict(main.DEFAULTS))
    assert engine.match(plain, "movie").dest_rel == "Aliens (1986)/Aliens (1986) 1080p.mkv"
    assert engine.match(special, "movie").dest_rel == "Aliens (1986)/Aliens (1986) {edition-Special Edition} 1080p.mkv"


def test_old_default_movie_format_is_upgraded(main, tmp_path, monkeypatch):
    import json
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"format_movie": "{title} ({year})/{title} ({year}) {res} {vc} {ach} {c}"}))
    monkeypatch.setattr(main, "CONFIG_PATH", cfg_path)
    assert main.cfg_load()["format_movie"] == main.DEFAULTS["format_movie"]
