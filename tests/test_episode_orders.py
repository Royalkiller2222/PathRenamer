"""Episode orders (aired, DVD, streaming, absolute) and the built-in presets."""
import json

import pytest

import core
from conftest import make_files
from core import BUILTIN_PRESETS, MatchEngine

# Firefly-like: aired order differs from the DVD order
AIRED = [{"id": 101, "season": 1, "number": 1, "title": "The Train Job"},
         {"id": 102, "season": 1, "number": 2, "title": "Bushwhacked"},
         {"id": 111, "season": 1, "number": 11, "title": "Serenity"},
         {"id": 201, "season": 2, "number": 1, "title": "Next Season"},
         {"id": 999, "season": 0, "number": 1, "title": "A Special"}]
DVD = {111: (1, 1), 101: (1, 2), 102: (1, 3), 201: (2, 1)}


class FakeTVMaze:
    def __init__(self, alternate=None):
        self.alternate = alternate if alternate is not None else {"dvd": DVD}

    def search_show(self, title):
        return {"show": "Firefly", "id": 180, "year": "2002"}

    def get_show_info(self, show_id):
        return {"show": "Firefly", "id": show_id, "year": "2002"}

    def get_episodes(self, show_id):
        return AIRED

    def get_episode(self, show_id, season, number):
        e = next((e for e in AIRED if (e["season"], e["number"]) == (season, number)), None)
        return {"title": e["title"]} if e else None

    def get_alternate_order(self, show_id, kind):
        return self.alternate.get(kind)


def _name(tmp_path, file, order, tvmaze=None, media_type="tv"):
    video, = make_files(tmp_path / "Firefly", file)
    key = "episode_anime" if media_type == "anime" else "episode_tv"
    engine = MatchEngine(cfg=dict(core.DEFAULTS, read_media_info=False, **{key: order}),
                         tvmaze=tvmaze or FakeTVMaze())
    return engine.match(video, media_type)


def test_aired_order_is_the_name_s_own(tmp_path):
    cand = _name(tmp_path, "Firefly.S01E01.mkv", "aired")
    assert cand.dest_rel == "Firefly (2002)/Season 01/Firefly S01E01 - The Train Job.mkv"


def test_dvd_order_from_tvmaze(tmp_path):
    cand = _name(tmp_path, "Firefly.S01E01.mkv", "dvd")
    assert cand.dest_rel == "Firefly (2002)/Season 01/Firefly S01E02 - The Train Job.mkv"
    assert (cand.season, cand.episodes) == (1, [1])                  # the row still knows the aired numbers
    assert "S01E01 - Serenity" in _name(tmp_path, "Firefly.S01E11.mkv", "dvd").dest_rel


def test_streaming_and_dvd_fall_back_to_aired_without_a_list(tmp_path):
    for order in ("streaming", "dvd"):
        cand = _name(tmp_path, "Firefly.S01E02.mkv", order, tvmaze=FakeTVMaze(alternate={}))
        assert "S01E02 - Bushwhacked" in cand.dest_rel


def test_absolute_order_counts_across_seasons(tmp_path):
    cand = _name(tmp_path, "Firefly.S02E01.mkv", "absolute")
    assert cand.dest_rel == "Firefly (2002)/Firefly 04 - Next Season.mkv"    # specials don't count


def test_anime_uses_its_own_order_setting(tmp_path):
    cand = _name(tmp_path, "Firefly.S01E01.mkv", "dvd", media_type="anime")
    assert "S01E02 - The Train Job" in cand.dest_rel


def test_tvmaze_alternate_list_parsing():
    client = core.TVMazeClient()
    answers = {
        "/shows/180/alternatelists": [{"id": 7, "dvd_release": False, "webChannel": {"name": "Netflix"}},
                                      {"id": 8, "dvd_release": True, "webChannel": None}],
        "/alternatelists/8/alternateepisodes": [
            {"season": 1, "number": 1, "_embedded": {"episodes": [{"id": 111}]}},
            {"season": 1, "number": 2, "_links": {"episodes": [{"href": "https://api.tvmaze.com/episodes/101"}]}},
            {"season": None, "number": None}],
        "/alternatelists/7/alternateepisodes": [{"season": 1, "number": 5, "_embedded": {"episodes": [{"id": 101}]}}],
    }
    client._get = lambda ep, **p: answers.get(ep)
    assert client.get_alternate_order(180, "dvd") == {111: (1, 1), 101: (1, 2)}
    assert client.get_alternate_order(180, "streaming") == {101: (1, 5)}
    client._get = lambda ep, **p: [] if ep.endswith("alternatelists") else None
    assert client.get_alternate_order(180, "dvd") is None


def test_old_dvd_setting_keeps_the_aired_names(tmp_path, monkeypatch):
    """Before 1.7 "DVD" gave the aired order, so existing settings move to "aired"."""
    path = tmp_path / "config.json"
    monkeypatch.setattr(core, "CONFIG_PATH", path)
    path.write_text(json.dumps({"episode_tv": "dvd", "episode_anime": "absolute"}))
    cfg = core.cfg_load()
    assert (cfg["episode_tv"], cfg["episode_anime"]) == ("aired", "absolute")
    path.write_text(json.dumps({"episode_tv": "dvd", "episode_orders_v2": True}))   # chosen in 1.7: stays
    assert core.cfg_load()["episode_tv"] == "dvd"


def test_builtin_presets(main, make_window, monkeypatch):
    from PyQt6.QtWidgets import QInputDialog
    assert list(BUILTIN_PRESETS) == ["DVD order", "Streaming order", "Absolute order", "Strip episode numbers"]
    w = make_window()
    w._fill_presets_menu()
    names = [a.text() for a in w.presets_menu.actions()]
    assert names[:4] == list(BUILTIN_PRESETS)
    w._apply_builtin_preset("Absolute order")
    assert (w.cfg["episode_tv"], w.cfg["episode_anime"], w.cfg["episode_match"]) == ("absolute", "absolute", "number")
    asked = []

    def pick(parent, title, text, items, current, editable):
        asked.append(items)
        return "DVD order", True
    monkeypatch.setattr(QInputDialog, "getItem", pick)
    w._apply_builtin_preset("Strip episode numbers")
    assert (w.cfg["episode_tv"], w.cfg["episode_match"]) == ("dvd", "title")
    assert len(asked[0]) == 4 and "found by their title" in w.status.currentMessage()
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a: ("", False))          # Cancel: nothing changes
    w._apply_builtin_preset("Strip episode numbers")
    assert w.cfg["episode_tv"] == "dvd"
    w._apply_builtin_preset("Streaming order")
    assert (w.cfg["episode_tv"], w.cfg["episode_match"]) == ("streaming", "number")


def test_settings_offer_the_four_orders(main, qapp, monkeypatch):
    monkeypatch.setattr(main, "cfg_save", lambda c: None)
    d = main.SettingsDialog(dict(main.DEFAULTS, episode_tv="streaming"))
    assert [d.episode_tv.itemData(i) for i in range(d.episode_tv.count())] == ["aired", "dvd", "streaming",
                                                                               "absolute"]
    assert d.episode_tv.currentData() == "streaming"
    d.episode_anime.setCurrentIndex(3)
    d._save()
    assert (d.cfg["episode_tv"], d.cfg["episode_anime"]) == ("streaming", "absolute")


def test_command_line_order(capsys):
    import cli
    args = cli.build_parser().parse_args(["rename", "x", "--order", "dvd", "--by-title"])
    assert (args.order, args.by_title) == ("dvd", True)
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["rename", "x", "--order", "random"])
