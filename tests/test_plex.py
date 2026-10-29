"""Asking Plex to scan after renaming, and signing in with a Plex account."""
import pytest
import requests

from conftest import FakeResponse, FakeSession
from plex import PlexClient, PlexError, PlexSignIn

SERVER = "http://192.168.1.10:32400"
SECTIONS = b"""<MediaContainer size="3">
  <Directory key="1" type="movie" title="Movies"><Location id="1" path="D:\\Plex\\Movies"/></Directory>
  <Directory key="2" type="show" title="TV Shows"><Location id="2" path="D:\\Plex\\TV Shows"/>
    <Location id="3" path="E:\\More TV"/></Directory>
  <Directory key="3" type="artist" title="Music"><Location id="4" path="D:\\Music"/></Directory>
</MediaContainer>"""


def _server(extra=None):
    routes = {("GET", f"{SERVER}/"): FakeResponse(content=b'<MediaContainer friendlyName="Den PC"/>'),
              ("GET", f"{SERVER}/library/sections"): FakeResponse(content=SECTIONS),
              ("GET", f"{SERVER}/library/sections/1/refresh"): FakeResponse(),
              ("GET", f"{SERVER}/library/sections/2/refresh"): FakeResponse()}
    routes.update(extra or {})
    session = FakeSession(routes)
    return PlexClient(SERVER, "tok", session=session), session


def _refreshes(session):
    return [(url.rsplit("/", 2)[-2], (kw.get("params") or {}).get("path"))
            for method, url, kw in session.calls if url.endswith("/refresh")]


def test_server_name_and_libraries():
    plex, session = _server()
    assert plex.server_name() == "Den PC"
    sections = plex.sections()
    assert [(s["title"], s["type"]) for s in sections] == [("Movies", "movie"), ("TV Shows", "show"), ("Music", "artist")]
    assert sections[1]["locations"] == ["D:\\Plex\\TV Shows", "E:\\More TV"]
    assert session.calls[0][2]["headers"]["X-Plex-Token"] == "tok"


def test_scans_only_the_new_folders():
    plex, session = _server()
    scanned = plex.scan(["D:/Plex/Movies/Dune (2021)/Dune (2021).mkv",
                         "D:/Plex/Movies/Dune (2021)/Dune (2021).eng.srt",
                         "d:\\plex\\tv shows\\The Office\\Season 02\\The Office S02E01.mkv",
                         "E:/More TV/Show/Season 01/Show S01E01.mkv"], "movie")
    assert scanned == ["Movies", "TV Shows"]
    assert sorted(_refreshes(session)) == [
        ("1", "D:\\Plex\\Movies\\Dune (2021)"),
        ("2", "D:\\Plex\\TV Shows\\The Office\\Season 02"),
        ("2", "E:\\More TV\\Show\\Season 01")]


def test_files_outside_the_libraries_scan_the_whole_library():
    """Plex may see the drive under another path (another computer, Docker)."""
    plex, session = _server()
    assert plex.scan(["Z:/Shared/Movies/Heat (1995)/Heat (1995).mkv"], "movie") == ["Movies"]
    assert _refreshes(session) == [("1", None)]
    plex, session = _server()
    assert plex.scan(["Z:/TV/Show/Season 01/Show S01E01.mkv"], "anime") == ["TV Shows"]


def test_linux_server_paths():
    sections = b"""<MediaContainer><Directory key="7" type="movie" title="Films">
      <Location path="/data/films/"/></Directory></MediaContainer>"""
    plex, session = _server({("GET", f"{SERVER}/library/sections"): FakeResponse(content=sections),
                             ("GET", f"{SERVER}/library/sections/7/refresh"): FakeResponse()})
    plex.scan(["/data/films/Heat (1995)/Heat (1995).mkv"], "movie")
    assert _refreshes(session) == [("7", "/data/films/Heat (1995)")]


@pytest.mark.parametrize("session, message", [
    (FakeSession({("GET", f"{SERVER}/library/sections"): FakeResponse(401)}), "Sign in again"),
    (FakeSession(error=requests.ConnectionError()), "Can't reach Plex"),
    (FakeSession({("GET", f"{SERVER}/library/sections"): FakeResponse(content=b"<html>router</html")}),
     "doesn't look like a Plex server"),
])
def test_errors_are_worded_for_people(session, message):
    with pytest.raises(PlexError, match=message):
        PlexClient(SERVER, "tok", session=session).sections()


def test_not_set_up():
    with pytest.raises(PlexError, match="isn't set up"):
        PlexClient("", "", session=FakeSession()).sections()
    assert PlexClient("localhost:32400", "t").base_url == "http://localhost:32400"


def test_sign_in_with_plex():
    pin = {"id": 42, "code": "abc123"}
    polls = iter([{"authToken": None}, {"authToken": "user-token"}])
    resources = [
        {"name": "Friend's server", "provides": "server", "owned": False, "accessToken": "shared",
         "connections": [{"uri": "https://friend.plex.direct:32400", "local": False}]},
        {"name": "Den PC", "provides": "server,player", "owned": True, "accessToken": "server-token",
         "connections": [{"uri": "https://relay.plex.direct:8443", "local": False, "relay": True},
                         {"uri": "https://192-168-1-10.abc.plex.direct:32400", "local": True}]},
        {"name": "Phone", "provides": "player", "connections": [{"uri": "http://x", "local": True}]},
    ]
    session = FakeSession({
        ("POST", "https://plex.tv/api/v2/pins"): FakeResponse(json=pin),
        ("GET", "https://plex.tv/api/v2/pins/42"): lambda **kw: FakeResponse(json=next(polls)),
        ("GET", "https://plex.tv/api/v2/resources"): FakeResponse(json=resources),
    })
    sign_in = PlexSignIn("client-1", session=session)
    pin_id, code, page = sign_in.start()
    assert (pin_id, code) == (42, "abc123")
    assert page.startswith("https://app.plex.tv/auth#?clientID=client-1&code=abc123&context")
    assert sign_in.token(42, "abc123") is None              # not approved yet
    assert sign_in.token(42, "abc123") == "user-token"
    servers = sign_in.servers("user-token")
    assert servers[0] == {"name": "Den PC", "url": "https://192-168-1-10.abc.plex.direct:32400",
                          "token": "server-token", "owned": True}
    assert [s["name"] for s in servers] == ["Den PC", "Friend's server"]
    assert session.calls[0][2]["headers"]["X-Plex-Client-Identifier"] == "client-1"


def test_scan_every_library():
    plex, session = _server()
    assert plex.scan_all() == ["Movies", "TV Shows"]
    assert _refreshes(session) == [("1", None), ("2", None)]
