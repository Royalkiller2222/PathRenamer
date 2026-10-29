"""Library scans for Jellyfin, Emby and Kodi (Plex has its own tests)."""
import pytest
import requests

from conftest import FakeResponse, FakeSession
from mediaservers import (JellyfinClient, KodiClient, MediaServerError, map_folders, norm,
                          server_from_config)

JF = "http://nas:8096"
LIBRARIES = [{"Name": "Movies", "CollectionType": "movies", "ItemId": "m1", "Locations": ["/data/movies"]},
             {"Name": "Shows", "CollectionType": "tvshows", "ItemId": "s1", "Locations": ["/data/tv", "/more/tv"]},
             {"Name": "Music", "CollectionType": "music", "ItemId": "a1", "Locations": ["/data/music"]}]


def _jellyfin(emby=False):
    base = JF + ("/emby" if emby else "")
    session = FakeSession({("GET", f"{base}/System/Info"): FakeResponse(json={"ServerName": "Den NAS"}),
                           ("GET", f"{base}/Library/VirtualFolders"): FakeResponse(json=LIBRARIES),
                           ("POST", f"{base}/Library/Media/Updated"): FakeResponse(204),
                           ("POST", f"{base}/Library/Refresh"): FakeResponse(204)})
    return JellyfinClient(JF, "key-1", session=session, emby=emby), session


def test_folders_map_to_the_server_s_own_paths():
    libs = [{"key": 1, "title": "Movies", "locations": ["D:\\Plex\\Movies"]},
            {"key": 2, "title": "TV", "locations": ["smb://NAS/TV/"]}]
    jobs, unmatched = map_folders(["d:/plex/movies/Heat (1995)/Heat (1995).mkv",
                                   "\\\\NAS\\TV\\Show\\Season 01\\Show S01E01.mkv"], libs)
    assert not unmatched
    assert jobs[1][1] == {"D:\\Plex\\Movies\\Heat (1995)"}
    assert jobs[2][1] == {"smb://NAS/TV/Show/Season 01"}
    assert norm("smb://NAS/TV/") == norm("\\\\nas\\tv")
    _, unmatched = map_folders(["E:/Elsewhere/x.mkv"], libs)
    assert unmatched


@pytest.mark.parametrize("emby", [False, True])
def test_jellyfin_and_emby_scan_only_new_folders(emby):
    client, session = _jellyfin(emby)
    assert client.server_name() == "Den NAS"
    assert client.scan(["/data/movies/Heat (1995)/Heat (1995).mkv",
                        "/more/tv/Show/Season 01/Show S01E01.mkv"], "movie") == ["Movies", "Shows"]
    method, url, kw = session.calls[-1]
    assert url.endswith("/Library/Media/Updated")
    assert kw["json"] == {"Updates": [{"Path": "/data/movies/Heat (1995)", "UpdateType": "Created"},
                                      {"Path": "/more/tv/Show/Season 01", "UpdateType": "Created"}]}
    if emby:
        assert kw["headers"]["X-Emby-Token"] == "key-1"
    else:
        assert kw["headers"]["Authorization"] == 'MediaBrowser Token="key-1"'


def test_jellyfin_scans_everything_when_paths_differ():
    """Docker or another computer: the server knows the files by other paths."""
    client, session = _jellyfin()
    assert client.scan(["D:/Media/Movies/Heat (1995)/Heat (1995).mkv"]) == ["Movies", "Shows"]
    assert session.calls[-1][1].endswith("/Library/Refresh")
    assert client.scan_all() == ["Movies", "Shows"]


@pytest.mark.parametrize("response, message", [
    (FakeResponse(401), "didn't accept the API key"), (FakeResponse(500), "error"),
    (FakeResponse(content=b"<html>"), "doesn't look like a Jellyfin server")])
def test_jellyfin_errors(response, message):
    session = FakeSession({("GET", f"{JF}/System/Info"): response})
    with pytest.raises(MediaServerError, match=message):
        JellyfinClient(JF, "k", session=session).server_name()
    with pytest.raises(MediaServerError, match="isn't set up"):
        JellyfinClient("", "", session=FakeSession()).server_name()


def _kodi(sources):
    calls = []

    def answer(json=None, **kw):
        calls.append((json["method"], json["params"], kw.get("auth")))
        result = {"Application.GetProperties": {"name": "Kodi", "version": {"major": 21}},
                  "Files.GetSources": {"sources": sources},
                  "VideoLibrary.Scan": "OK"}[json["method"]]
        return FakeResponse(json={"jsonrpc": "2.0", "id": 1, "result": result})
    session = FakeSession({("POST", "http://kodi:8080/jsonrpc"): answer})
    return KodiClient("kodi:8080", "kodi", "pw", session=session), calls


def test_kodi_scans_the_new_folder():
    kodi, calls = _kodi([{"file": "smb://NAS/Movies/", "label": "Movies"}])
    assert kodi.server_name() == "Kodi 21"
    assert kodi.scan(["\\\\NAS\\Movies\\Heat (1995)\\Heat (1995).mkv"]) == ["Movies"]
    assert calls[-1] == ("VideoLibrary.Scan", {"directory": "smb://NAS/Movies/Heat (1995)/"}, ("kodi", "pw"))


def test_kodi_scans_everything_when_paths_differ():
    kodi, calls = _kodi([{"file": "/storage/movies/", "label": "Movies"}])
    assert kodi.scan(["D:/Movies/Heat (1995)/Heat (1995).mkv"]) == ["Movies"]
    assert calls[-1][:2] == ("VideoLibrary.Scan", {})


def test_kodi_errors():
    offline = KodiClient("http://kodi:8080", session=FakeSession(error=requests.ConnectionError()))
    with pytest.raises(MediaServerError, match="Allow remote control via HTTP"):
        offline.scan_all()
    refused = KodiClient("http://kodi:8080", session=FakeSession(
        {("POST", "http://kodi:8080/jsonrpc"): FakeResponse(json={"error": {"message": "Invalid params"}})}))
    with pytest.raises(MediaServerError, match="Invalid params"):
        refused.scan_all()


def test_server_from_settings():
    assert server_from_config({}).label == "Plex"
    jf = server_from_config({"media_server": "jellyfin", "jellyfin_url": "nas:8096", "jellyfin_key": "k"})
    assert (jf.label, jf.base_url, jf.configured) == ("Jellyfin", "http://nas:8096", True)
    assert server_from_config({"media_server": "emby"}).label == "Emby"
    assert not server_from_config({"media_server": "emby"}).configured
    kodi = server_from_config({"media_server": "kodi", "kodi_url": "http://k:8080", "kodi_user": "u",
                               "kodi_password": "enc"}, unprotect=lambda s: s.upper())
    assert (kodi.label, kodi.password) == ("Kodi", "ENC")
