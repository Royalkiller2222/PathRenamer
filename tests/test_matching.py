"""MatchEngine end to end, offline and with a fake TVMaze client."""
from pathlib import Path

import pytest


@pytest.fixture
def engine(main):
    return main.MatchEngine(cfg=dict(main.DEFAULTS))


@pytest.mark.parametrize("path,media_type,expected", [
    ("/dl/Incoming/The.Office.US.S02E03.720p.WEB-DL.x264.mkv", "tv",
     "The Office (US)/Season 02/The Office (US) S02E03 - Episode 3.mkv"),
    ("/dl/Incoming/Spider-Man.No.Way.Home.2021.1080p.WEB-DL.x264-GRP.mkv", "movie",
     "Spider-Man No Way Home (2021)/Spider-Man No Way Home (2021) 1080p H.264.mkv"),
    ("/dl/Incoming/The.Simpsons.S01E01E02.1080p.BluRay.mkv", "tv",
     "The Simpsons/Season 01/The Simpsons S01E01-E02 - Episode 1.mkv"),
    ("/dl/Incoming/The.Daily.Show.2024.05.17.Guest.Name.720p.WEB.h264.mkv", "tv",
     "The Daily Show/Season 2024/The Daily Show 2024-05-17 - Guest Name.mkv"),
    ("/dl/Incoming/Doctor.Who.2005.S06E13.720p.HDTV.mkv", "tv",
     "Doctor Who (2005)/Season 06/Doctor Who S06E13 - Episode 13.mkv"),
    ("/media/Show/Specials/Show.S00E02.mkv", "tv",
     "Show/Season 00/Show S00E02 - Episode 2.mkv"),
    ("/dl/[Frostii]_Nodame_Cantabile_Finale_-_00_[73AD0735].mkv", "anime",
     "Nodame Cantabile Finale/Season 01/Nodame Cantabile Finale S01E00 - Episode 0.mkv"),
    ("/movies/Fear and Loathing in Las Vegas (1998)/Fear.and.Loathing.in.Las.Vegas.720p.HDDVD.DTS.x264-ESiR.mkv",
     "movie",
     "Fear and Loathing in Las Vegas (1998)/Fear and Loathing in Las Vegas (1998) 720p H.264 DTS.mkv"),
    ("/dl/Incoming/Some.Movie.1080p.BluRay.x264.mkv", "movie",
     "Some Movie/Some Movie 1080p H.264.mkv"),
])
def test_offline_destination(engine, path, media_type, expected):
    cand = engine.match(Path(path), media_type)
    assert cand.error == ""
    assert cand.dest_rel == expected


def test_no_title_is_an_error_not_unknown(engine):
    cand = engine.match(Path("/x/Downloads/S02E03.mkv"), "tv")
    assert cand.error and "title" in cand.error


def test_no_episode_number_is_an_error_not_s01e01(engine):
    cand = engine.match(Path("/x/Downloads/Show.Name.Christmas.Special.720p.mkv"), "tv")
    assert cand.error and "episode" in cand.error


class FakeTVMaze:
    titles = {(1, 1): "Simpsons Roasting on an Open Fire", (1, 2): "Bart the Genius"}

    def search_show(self, title):
        return {"show": "The Simpsons", "id": 83, "year": "1989"}

    def get_episode(self, show_id, season, episode):
        t = self.titles.get((season, episode))
        return {"title": t} if t else None

    def get_episode_by_date(self, show_id, air_date):
        return {"title": f"Episode of {air_date.isoformat()}"}

    def get_show_info(self, show_id):
        return {"show": "The Simpsons", "id": show_id, "year": "1989"}


def test_multi_episode_titles_are_joined(main):
    engine = main.MatchEngine(cfg=dict(main.DEFAULTS), tvmaze=FakeTVMaze())
    cand = engine.match(Path("/dl/The.Simpsons.S01E01E02.1080p.mkv"), "tv")
    assert cand.dest_rel == ("The Simpsons (1989)/Season 01/The Simpsons S01E01-E02 - "
                             "Simpsons Roasting on an Open Fire & Bart the Genius.mkv")


def test_daily_show_looks_up_by_air_date(main):
    engine = main.MatchEngine(cfg=dict(main.DEFAULTS), tvmaze=FakeTVMaze())
    cand = engine.match(Path("/dl/The.Daily.Show.2024.05.17.Guest.Name.720p.mkv"), "tv")
    assert cand.dest_rel == ("The Simpsons (1989)/Season 2024/"
                             "The Simpsons 2024-05-17 - Episode of 2024-05-17.mkv")


def test_selected_show_is_used(main):
    engine = main.MatchEngine(cfg=dict(main.DEFAULTS), tvmaze=FakeTVMaze())
    cand = engine.match_with_override(Path("/dl/simpsons.s01e02.mkv"), "tv",
                                      selected_title="The Simpsons", selected_id=("tvmaze", 83))
    assert cand.dest_rel == "The Simpsons (1989)/Season 01/The Simpsons S01E02 - Bart the Genius.mkv"
