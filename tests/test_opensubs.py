"""Finding and downloading subtitles from OpenSubtitles.com (offline: the
web service is replaced by a fake that answers like it)."""
import struct

import pytest
import requests

from conftest import FakeResponse, FakeSession
from opensubs import (API_URL, OpenSubtitlesClient, OpenSubtitlesError, SubtitleResult,
                      from_opensubtitles, opensubtitles_hash, parse_languages, subtitle_name,
                      to_opensubtitles)


def test_hash_matches_the_published_algorithm(tmp_path):
    data = bytes((i * 7 + 3) % 256 for i in range(300_000))
    f = tmp_path / "movie.mkv"
    f.write_bytes(data)
    expected = len(data)
    for chunk in (data[:65536], data[-65536:]):
        expected += sum(struct.unpack("<8192Q", chunk))
    assert opensubtitles_hash(f) == f"{expected & 0xFFFFFFFFFFFFFFFF:016x}"
    small = tmp_path / "small.mkv"
    small.write_bytes(b"x" * 1000)
    assert opensubtitles_hash(small) is None


def test_languages():
    assert parse_languages("English, es; FRE, klingon, english") == ["eng", "spa", "fre"]
    assert parse_languages("") == []
    assert to_opensubtitles("eng") == ["en"]
    assert to_opensubtitles("por") == ["pt-PT", "pt-BR"]
    assert to_opensubtitles("gre") == ["el"]
    assert [from_opensubtitles(c) for c in ("en", "pt-BR", "zh-TW", "hi", "xx")] == \
        ["eng", "por", "chi", "hin", ""]


def _item(file_id, lang="en", **attrs):
    a = {"language": lang, "download_count": 10, "files": [{"file_id": file_id, "file_name": f"{file_id}.srt"}]}
    a.update(attrs)
    return {"id": str(file_id), "type": "subtitle", "attributes": a}


def _client(routes, **kw):
    session = FakeSession(routes)
    return OpenSubtitlesClient("app-key", app_version="1.5.0", session=session, **kw), session


def test_search_sends_what_the_api_wants(tmp_path):
    video = tmp_path / "Show.S02E05.mkv"
    video.write_bytes(b"\x01" * 200_000)
    found = {"data": [_item(1, moviehash_match=True), _item(2, "pt-BR"),
                      {"attributes": {"language": "en", "files": [{"file_id": 3}, {"file_id": 4}]}}]}
    client, session = _client({("GET", f"{API_URL}/subtitles"): FakeResponse(json=found)})
    results = client.search(path=video, languages=["eng", "por"], query="The Office", season=2,
                            episode=5, is_episode=True)
    method, url, kw = session.calls[0]
    params = kw["params"]
    assert [k for k, _ in params] == sorted(k for k, _ in params)        # alphabetical
    assert dict(params) == {"languages": "en,pt-br,pt-pt", "moviehash": opensubtitles_hash(video),
                            "query": "the office", "season_number": 2, "episode_number": 5,
                            "type": "episode"}
    assert kw["headers"]["Api-Key"] == "app-key" and kw["headers"]["User-Agent"] == "PathRenamer v1.5.0"
    assert [(r.file_id, r.language, r.hash_match) for r in results] == [(1, "eng", True), (2, "por", False)]


def test_best_subtitle_per_language():
    r = [SubtitleResult(1, "eng", downloads=900),
         SubtitleResult(2, "eng", downloads=5, hash_match=True),             # made for this file
         SubtitleResult(3, "eng", downloads=9999, hash_match=True, machine=True),
         SubtitleResult(4, "spa", downloads=50, forced=True),
         SubtitleResult(5, "spa", downloads=40),
         SubtitleResult(6, "spa", downloads=60, hearing_impaired=True),
         SubtitleResult(7, "fre", downloads=1)]
    best = OpenSubtitlesClient.best_per_language(r, ["eng", "spa"])
    assert {k: v.file_id for k, v in best.items()} == {"eng": 2, "spa": 5}
    best = OpenSubtitlesClient.best_per_language(r, ["spa"], hearing_impaired=True)
    assert best["spa"].file_id == 6


def test_sign_in_and_download():
    downloaded = {"link": "https://dl.example/abc.srt", "file_name": "abc.srt", "remaining": 18}
    client, session = _client({
        ("POST", f"{API_URL}/login"): FakeResponse(json={"token": "jwt", "base_url": "vip-api.opensubtitles.com",
                                                          "user": {"allowed_downloads": 1000}}),
        ("POST", "https://vip-api.opensubtitles.com/api/v1/download"): FakeResponse(json=downloaded),
        ("GET", "https://dl.example/abc.srt"): FakeResponse(content=b"1\n00:00:01,000 --> 00:00:02,000\nHi\n"),
    }, username="me", password="pw")
    assert client.login() == {"allowed_downloads": 1000}
    data = client.download(SubtitleResult(7, "eng"))
    assert data.startswith(b"1\n00:00:01")
    post = session.calls[1]
    assert post[2]["json"] == {"file_id": 7} and post[2]["headers"]["Authorization"] == "Bearer jwt"
    assert client.remaining == 18


def test_no_sign_in_without_a_username():
    client, session = _client({})
    assert client.login() == {} and not session.calls


@pytest.mark.parametrize("status, message", [
    (401, "username or password"), (403, "API key"), (406, "download limit"), (429, "busy"), (500, "error")])
def test_errors_are_worded_for_people(status, message):
    client, _ = _client({("POST", f"{API_URL}/download"): FakeResponse(status, json={})})
    with pytest.raises(OpenSubtitlesError, match=message):
        client.download(SubtitleResult(1, "eng"))


def test_quota_message_from_the_server_is_shown():
    body = {"message": "You have downloaded your allowed 20 subtitles for 24h."}
    client, _ = _client({("POST", f"{API_URL}/download"): FakeResponse(406, json=body)})
    with pytest.raises(OpenSubtitlesError, match="allowed 20 subtitles"):
        client.download(SubtitleResult(1, "eng"))


def test_needs_an_api_key_and_a_connection():
    with pytest.raises(OpenSubtitlesError, match="API key"):
        OpenSubtitlesClient("", session=FakeSession()).search(languages=["eng"], query="x")
    offline = OpenSubtitlesClient("k", session=FakeSession(error=requests.ConnectionError()))
    with pytest.raises(OpenSubtitlesError, match="internet"):
        offline.search(languages=["eng"], query="x")


def test_plex_names_for_downloads(tmp_path):
    video = tmp_path / "Movie (2010).mkv"
    taken = {"movie (2010).eng.srt"}
    assert subtitle_name(video, SubtitleResult(1, "eng", file_name="x.srt"), taken) == "Movie (2010).2.eng.srt"
    assert subtitle_name(video, SubtitleResult(2, "spa", file_name="x.ass", hearing_impaired=True), taken) \
        == "Movie (2010).spa.sdh.ass"
    assert subtitle_name(video, SubtitleResult(3, "fre", file_name="x.zip"), taken) == "Movie (2010).fre.srt"
