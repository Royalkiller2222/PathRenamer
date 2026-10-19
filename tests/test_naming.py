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
