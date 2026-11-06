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
    assert list(BUILTIN_PRESETS) == ["Aired order", "DVD order", "Streaming order", "Absolute order",
                                     "Strip episode numbers"]
    w = make_window()
    w._fill_presets_menu()
    names = [a.text() for a in w.presets_menu.actions()]
    assert names[:5] == list(BUILTIN_PRESETS)
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
    w._apply_builtin_preset("Aired order")                                       # back to the usual
    assert (w.cfg["episode_tv"], w.cfg["episode_anime"], w.cfg["episode_match"]) == ("aired", "aired", "number")


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


# ── TheTVDB ──────────────────────────────────────────────────────────────────
class TVDBSession:
    """Answers like api4.thetvdb.com."""

    def __init__(self, orders):
        self.orders, self.calls, self.logins = orders, [], 0

    def post(self, url, json=None, timeout=None):
        self.logins += 1
        from conftest import FakeResponse
        return FakeResponse(json={"data": {"token": f"tok{self.logins}"}})

    def get(self, url, params=None, timeout=None, headers=None):
        from conftest import FakeResponse
        self.calls.append((url, dict(params or {}), headers["Authorization"]))
        kind = url.rsplit("/", 1)[-1]
        if "/search" in url:
            return FakeResponse(json={"data": [{"tvdb_id": "78874", "name": "Firefly", "year": "2002"}]})
        eps = self.orders.get(kind, [])
        page = params.get("page", 0)
        chunk = eps[page * 2:(page + 1) * 2]                                # two per page: tests paging
        return FakeResponse(json={"data": {"episodes": chunk},
                                  "links": {"next": "more" if (page + 1) * 2 < len(eps) else None}})


def _ep(eid, season, number, absolute=None):
    return {"id": eid, "seasonNumber": season, "number": number, "absoluteNumber": absolute, "name": f"E{eid}"}


TVDB_ORDERS = {
    "default": [_ep(1, 1, 1, 2), _ep(2, 1, 2, 3), _ep(11, 1, 11, 1), _ep(21, 2, 1, 15), _ep(99, 0, 1)],
    "dvd": [_ep(11, 1, 1), _ep(1, 1, 2), _ep(2, 1, 3), _ep(21, 2, 1)],
    "regional": [_ep(1, 3, 7), _ep(2, 3, 8)],
}


@pytest.fixture
def tvdb():
    core._TVDB_TOKENS.clear()
    session = TVDBSession(TVDB_ORDERS)
    return core.TVDBClient("key", "1234", session=session), session


def test_tvdb_orders(tvdb):
    client, session = tvdb
    assert client.order(78874, 1, [1], "dvd") == (1, [2])
    assert client.order(78874, 1, [1, 2], "dvd") == (1, [2, 3])
    assert client.order(78874, 1, [1], "absolute") == (1, [2])            # absoluteNumber
    assert client.order(78874, 1, [2], "streaming") == (3, [8])           # no "alternate": "regional"
    assert client.order(78874, 2, [1], "streaming") is None               # not in that order
    assert client.order(78874, 5, [1], "dvd") is None                     # unknown episode
    assert session.logins == 1 and all(auth == "Bearer tok1" for _, _, auth in session.calls)
    assert {c[1]["page"] for c in session.calls if c[0].endswith("/default")} == {0, 1, 2}


def test_tvdb_logs_in_again_when_the_token_expires(tvdb):
    client, session = tvdb
    from conftest import FakeResponse
    real_get, answered = session.get, []

    def get(url, **kw):
        if not answered:
            answered.append(1)
            return FakeResponse(401)
        return real_get(url, **kw)
    session.get = get
    assert client.search_series("Firefly", 2002) == 78874 and session.logins == 2


def test_engine_prefers_tvdb_and_falls_back_to_tvmaze(tmp_path, tvdb):
    client, _ = tvdb
    maze = FakeTVMaze(alternate={})
    maze.get_show_info = lambda show_id: {"show": "Firefly", "id": show_id, "year": "2002", "tvdb": 78874}
    video, = make_files(tmp_path / "Firefly", "Firefly.S01E01.mkv")
    engine = MatchEngine(cfg=dict(core.DEFAULTS, read_media_info=False, episode_tv="dvd"), tvmaze=maze, tvdb=client)
    assert "S01E02 - The Train Job" in engine.match(video, "tv").dest_rel   # TheTVDB's DVD order
    broken = core.TVDBClient("bad", session=TVDBSession({}))                 # nothing from TheTVDB
    engine = MatchEngine(cfg=dict(core.DEFAULTS, read_media_info=False, episode_tv="dvd"),
                         tvmaze=FakeTVMaze(), tvdb=broken)
    assert "S01E02 - The Train Job" in engine.match(video, "tv").dest_rel   # TVMaze's DVD list


def test_tvdb_key_from_settings_or_the_release(monkeypatch):
    assert core.tvdb_from_config(dict(core.DEFAULTS)) is None
    client = core.tvdb_from_config(dict(core.DEFAULTS, tvdb_api_key="mine", tvdb_pin=core.protect("42")))
    assert (client.api_key, client.pin) == ("mine", "42")
    monkeypatch.setattr(core, "TVDB_APP_KEY", "built-in")
    assert core.tvdb_from_config(dict(core.DEFAULTS)).api_key == "built-in"


def test_tvdb_settings_are_saved(main, qapp, monkeypatch):
    monkeypatch.setattr(main, "cfg_save", lambda c: None)
    d = main.SettingsDialog(dict(main.DEFAULTS))
    d.tvdb_key.setText(" key-1 ")
    d.tvdb_pin.setText("4242")
    d._save()
    assert d.cfg["tvdb_api_key"] == "key-1" and main.unprotect(d.cfg["tvdb_pin"]) == "4242"
