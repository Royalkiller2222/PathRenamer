"""Episodes found by their title: for names without SxxEyy, and (with
Settings → Options → Match episodes by title) when the numbers are wrong."""
from pathlib import Path

import pytest

import core
from conftest import make_files
from core import MatchEngine, best_episode_title, episode_title_text
from parser import analyse_path

EPISODES = [{"season": 1, "number": 1, "title": "Pilot"}, {"season": 2, "number": 1, "title": "The Dundies"},
            {"season": 2, "number": 2, "title": "Sexual Harassment"}, {"season": 2, "number": 5, "title": "Halloween"},
            {"season": 3, "number": 1, "title": "Gay Witch Hunt"}, {"season": 3, "number": 23, "title": "The Job (1)"},
            {"season": 3, "number": 24, "title": "The Job (2)"}]


class FakeTVMaze:
    """Answers like TVMazeClient for one show."""

    def __init__(self):
        self.searches = []

    def search_show(self, title):
        self.searches.append(title)
        return {"show": "The Office", "id": 526, "year": "2005"} if "office" in title.lower() else None

    def get_show_info(self, show_id):
        return {"show": "The Office", "id": show_id, "year": "2005"}

    def get_episodes(self, show_id):
        return EPISODES

    def get_episode(self, show_id, season, number):
        e = next((e for e in EPISODES if (e["season"], e["number"]) == (season, number)), None)
        return {"title": e["title"]} if e else None


@pytest.mark.parametrize("name, words", [
    ("The Office/The.Office.US.S02E05.The.Dundies.720p.WEB-DL.x264-GRP.mkv", "the dundies"),
    ("The Office/The Office - The Dundies.mkv", "the dundies"),
    ("The Office/Season 2/The Dundies.mkv", "the dundies"),
    ("The.Office.US.The.Dundies.720p.HDTV.x264.mkv", "the dundies"),
    ("Breaking Bad/Breaking Bad - Ozymandias (1080p).mkv", "ozymandias"),
    ("The Office/The.Office.S01E01.720p.mkv", ""),
])
def test_the_words_that_name_the_episode(name, words):
    show = "Breaking Bad" if "Breaking" in name else "The Office"
    assert episode_title_text(analyse_path(Path(name), "tv"), show) == words


def test_closest_title_wins_unless_two_are_as_close():
    assert best_episode_title("the dundies", EPISODES)[:2] == (2, 1)
    assert best_episode_title("gay witch-hunt", EPISODES)[:2] == (3, 1)
    season, number, score = best_episode_title("sexual harrasment", EPISODES)     # a typo
    assert (season, number) == (2, 2) and 82 <= score < 95
    assert best_episode_title("the job", EPISODES) is None                       # part 1 or 2?
    assert best_episode_title("something else entirely", EPISODES) is None


def _engine(**cfg):
    return MatchEngine(cfg=dict(core.DEFAULTS, read_media_info=False, **cfg), tvmaze=FakeTVMaze())


def test_a_name_without_numbers_is_matched_by_its_title(tmp_path):
    video, = make_files(tmp_path, "The Office/The Office - The Dundies.mkv")
    cand = _engine().match(video, "tv")
    assert not cand.error and (cand.season, cand.episodes) == (2, [1])
    assert cand.dest_rel == "The Office (2005)/Season 02/The Office S02E01 - The Dundies.mkv"
    assert cand.confidence >= 0.85


def test_numbers_win_unless_matching_by_title_is_on(tmp_path):
    video, = make_files(tmp_path / "The Office", "The.Office.US.S02E05.The.Dundies.720p.mkv")  # wrong number
    assert _engine().match(video, "tv").episodes == [5]
    cand = _engine(episode_match="title").match(video, "tv")
    assert (cand.season, cand.episodes) == (2, [1]) and "S02E01 - The Dundies" in cand.dest_rel


def test_unsure_title_matches_ask_for_a_look(tmp_path):
    typo, nothing, plain = make_files(tmp_path / "The Office", "The Office - Sexual Harrasment.mkv",
                                      "The.Office.S02E05.Something.Else.Entirely.mkv", "The.Office.S02E05.mkv")
    engine = _engine(episode_match="title")
    cand = engine.match(typo, "tv")
    assert cand.episodes == [2] and cand.confidence < 0.85
    cand = engine.match(nothing, "tv")                 # title not found: the name's numbers, marked unsure
    assert cand.episodes == [5] and cand.confidence <= 0.6
    assert engine.match(plain, "tv").confidence >= 0.85   # no title to go by: the numbers are fine


def test_no_numbers_and_no_matching_title(tmp_path):
    video, = make_files(tmp_path / "The Office", "The Office - The Job.mkv")
    cand = _engine().match(video, "tv")
    assert "no episode title that matches" in cand.error


def test_episode_lists_and_answers_are_fetched_once():
    client = core.TVMazeClient()
    calls = []

    class Response:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return [{"season": 1, "number": 1, "name": "Pilot"}, {"season": 1, "number": None, "name": "Special"}]
    client.s.get = lambda url, params=None, timeout=None: calls.append(url) or Response()
    assert client.get_episodes(526) == [{"id": None, "season": 1, "number": 1, "title": "Pilot"}]
    client.get_episodes(526)
    assert len(calls) == 1
