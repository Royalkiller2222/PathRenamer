"""The command line and Docker version: rename, undo, history, settings,
the license, the watch folder and updates, without a window."""
import json

import pytest

import cli
import core
import licensing
import updates
from conftest import FakeResponse, FakeSession, ended_trial, make_files
from licensing import POLAR_API


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A settings folder of its own, offline (no metadata look-ups)."""
    monkeypatch.setattr(core, "CONFIG_PATH", tmp_path / "config" / "config.json")
    monkeypatch.setattr(cli, "HISTORY_PATH", tmp_path / "config" / "history.json")
    monkeypatch.setattr(updates, "check", lambda current: pytest.fail("no update check in tests"))
    for var in list(cli.ENV_SETTINGS) + ["PATHRENAMER_LICENSE_KEY", "PATHRENAMER_DOCKER"]:
        monkeypatch.delenv(var, raising=False)
    core.cfg_save(dict(core.DEFAULTS, use_tvmaze=False, use_anidb=False, use_cinemeta=False,
                       check_updates=False))
    monkeypatch.setenv("PATHRENAMER_MOVIES", str(tmp_path / "movies"))
    monkeypatch.setenv("PATHRENAMER_TV", str(tmp_path / "tv"))
    return tmp_path


def run(capsys, *args):
    code = cli.main([str(a) for a in args])
    out = capsys.readouterr()
    return code, out.out + out.err


def test_rename_shows_first_then_renames_and_undoes(home, capsys):
    video, sub = make_files(home / "dl", "Heat.1995.1080p.BluRay.x264.mkv", "Heat.1995.1080p.BluRay.x264.en.srt")
    code, out = run(capsys, "rename", home / "dl", "--dry-run")
    assert code == 0 and "-> Heat (1995)/Heat (1995) 1080p H.264.mkv" in out and "Dry run: 1 file(s)" in out
    assert video.exists()
    code, out = run(capsys, "rename", home / "dl")                  # no terminal to ask in: nothing happens
    assert code == 2 and "Add --yes" in out and video.exists()
    code, out = run(capsys, "rename", home / "dl", "--yes")
    placed = home / "movies" / "Heat (1995)" / "Heat (1995) 1080p H.264.mkv"
    assert code == 0 and placed.exists() and placed.with_suffix(".eng.srt").exists()
    assert "Renamed 1 file(s) and 1 subtitle(s)" in out and not video.exists()
    code, out = run(capsys, "history")
    assert code == 0 and "2 files moved to" in out
    code, out = run(capsys, "undo")
    assert code == 0 and video.exists() and sub.exists() and not placed.exists()
    code, out = run(capsys, "undo")
    assert code == 1 and "Nothing to undo" in out


def test_unsure_matches_wait_for_a_flag(home, capsys):
    make_files(home / "dl", "The.Office.US.S02E05.720p.mkv")       # offline: not sure about the show
    code, out = run(capsys, "rename", home / "dl", "--yes")
    assert code == 0 and "[??]" in out and "--include-unsure" in out and "Nothing to rename" in out
    code, out = run(capsys, "rename", home / "dl", "--yes", "--include-unsure")
    assert code == 0 and (home / "tv" / "The Office (US)" / "Season 02").is_dir()


def test_a_missing_library_folder_is_explained(home, capsys, monkeypatch):
    monkeypatch.delenv("PATHRENAMER_MOVIES")
    make_files(home / "dl", "Heat.1995.mkv")
    code, out = run(capsys, "rename", home / "dl", "--yes")
    assert code == 1 and "PATHRENAMER_MOVIES" in out and "config set root_movie" in out
    code, out = run(capsys, "rename", home / "dl", "--yes", "--to", home / "elsewhere")
    assert code == 0 and (home / "elsewhere" / "Heat (1995)").is_dir()
    code, out = run(capsys, "rename", home / "nowhere")
    assert code == 1 and "Not found" in out


def test_settings(home, capsys, monkeypatch):
    code, out = run(capsys, "config", "set", "jellyfin_key", "abc")
    assert code == 0 and "(set)" in out and "abc" not in out
    assert core.unprotect(core.cfg_load()["jellyfin_key"]) == "abc"        # stored encrypted on Windows
    assert run(capsys, "config", "get", "jellyfin_key")[1].strip() == "(set)"
    assert run(capsys, "config", "set", "use_tvmaze", "yes")[0] == 0 and core.cfg_load()["use_tvmaze"] is True
    code, out = run(capsys, "config", "set", "use_tvmaze", "maybe")
    assert code == 2 and "true or false" in out
    code, out = run(capsys, "config", "set", "media_server", "netflix")
    assert code == 2 and "plex, jellyfin, emby, kodi" in out
    code, out = run(capsys, "config", "set", "jelyfin_url", "x")
    assert code == 2 and "Did you mean jellyfin_url" in out
    code, out = run(capsys, "config", "set", "license_key", "FREE-PRO")
    assert code == 2 and "license activate" in out
    code, out = run(capsys, "config", "show")
    assert "root_movie" in out and "PATHRENAMER_MOVIES (root_movie)" in out and "theme" not in out
    assert "root_movie" not in json.loads(core.CONFIG_PATH.read_text()) or \
        core.cfg_load()["root_movie"] == ""                          # environment values aren't saved


@pytest.fixture
def polar(monkeypatch):
    """Licensing switched on, with a fake license server."""
    monkeypatch.setattr(licensing, "POLAR_ORGANIZATION_ID", "org")
    granted = {"status": "granted", "display_key": "****-1234", "expires_at": None}
    session = FakeSession({
        ("POST", f"{POLAR_API}/activate"): FakeResponse(json={"id": "act-1", "license_key": granted}),
        ("POST", f"{POLAR_API}/validate"): FakeResponse(json=granted),
        ("POST", f"{POLAR_API}/deactivate"): FakeResponse(204)})
    monkeypatch.setattr(licensing.requests, "Session", lambda: session)
    return session


def test_license_commands(home, capsys, polar):
    code, out = run(capsys, "license", "status")
    assert code == 0 and "try Pro free" in out and "Get Pro" in out
    code, out = run(capsys, "license", "activate", "KEY-1")
    assert code == 0 and "Pro is active (****-1234)" in out
    saved = json.loads(core.CONFIG_PATH.read_text())
    assert saved["license_state"] and "license_key" not in saved and "KEY-1" not in json.dumps(saved)
    assert run(capsys, "license", "check")[0] == 0
    code, out = run(capsys, "license", "deactivate")
    assert code == 0 and polar.calls[-1][1].endswith("/deactivate") and "Pro is active" not in out
    code, out = run(capsys, "license", "check")
    assert code == 1 and "No license key" in out


def test_license_key_from_the_environment(home, capsys, polar, monkeypatch):
    monkeypatch.setenv("PATHRENAMER_LICENSE_KEY", "KEY-ENV")
    assert run(capsys, "license", "status")[1].count("Pro is active") == 2   # activated, then the status
    activations = [c for c in polar.calls if c[1].endswith("/activate")]
    run(capsys, "license", "status")
    assert len([c for c in polar.calls if c[1].endswith("/activate")]) == len(activations) == 1


def _watcher(home, **kw):
    cfg = cli.load_settings()
    return cli.Watcher(cfg, str(home / "dl"), "auto", None, "move", stamp=False, **kw)


def test_watch_renames_new_downloads(home, capsys):
    video = make_files(home / "dl", "Inception.2010.1080p.WEB-DL.mkv")[0]
    video.write_bytes(b"video")
    w = _watcher(home)
    w.start_trial_if_needed()                                       # licensing is off: nothing to start
    w.handle([video])
    assert (home / "movies" / "Inception (2010)" / "Inception (2010) 1080p.mkv").exists()
    assert "1 new file(s) for movies" in capsys.readouterr().out


def test_watch_without_pro_only_reports(home, capsys, monkeypatch):
    monkeypatch.setattr(licensing, "POLAR_ORGANIZATION_ID", "org")
    core.cfg_save(dict(core.cfg_load(), **ended_trial()))
    video = make_files(home / "dl", "Inception.2010.1080p.WEB-DL.mkv")[0]
    w = _watcher(home)
    w.start_trial_if_needed()
    w.handle([video])
    out = capsys.readouterr().out
    assert video.exists() and "part of PathRenamer Pro" in out and "Needs a look" in out


def test_watch_starts_the_trial(home, capsys, monkeypatch):
    monkeypatch.setattr(licensing, "POLAR_ORGANIZATION_ID", "org")
    w = _watcher(home)
    w.start_trial_if_needed()
    assert "14-day Pro trial has started" in capsys.readouterr().out
    assert licensing.License(core.cfg_load()).trial_active()        # saved


def test_watch_stops_cleanly(home, monkeypatch):
    (home / "dl").mkdir()
    w = _watcher(home)
    w.stop.set()
    w.run(poll=0.01)                                                # returns at once when told to stop
    with pytest.raises(cli.Stop, match="doesn't exist"):
        cli.cmd_watch(cli.build_parser().parse_args(["watch", str(home / "missing")]), cli.load_settings())


def test_update_command(home, capsys, monkeypatch):
    rel = updates.Release(version="9.0.0", tag="V9.0.0", page_url="https://github.com/x/releases/tag/V9.0.0")
    monkeypatch.setattr(updates, "check", lambda current: rel)
    code, out = run(capsys, "update")
    assert code == 0 and "9.0.0 is available" in out and "releases/tag/V9.0.0" in out
    monkeypatch.setenv("PATHRENAMER_DOCKER", "1")
    assert "docker pull ghcr.io/royalkiller2222/pathrenamer:latest" in run(capsys, "update")[1]
    monkeypatch.setattr(updates, "check", lambda current: None)
    assert "is the latest version" in run(capsys, "update")[1]


def test_scan_and_subtitles_say_what_to_set_up(home, capsys, monkeypatch):
    code, out = run(capsys, "scan")
    assert code == 1 and "config set media_server jellyfin" in out

    class Server:
        label, configured = "Jellyfin", True

        def scan_all(self):
            return ["Movies", "Shows"]
    monkeypatch.setattr(cli, "server_from_config", lambda cfg, unprotect: Server())
    assert run(capsys, "scan")[1].strip() == "Jellyfin is scanning Movies, Shows."
    monkeypatch.setattr(cli, "OPENSUBTITLES_APP_KEY", "")
    make_files(home / "dl", "Heat.1995.mkv")
    code, out = run(capsys, "subtitles", home / "dl")
    assert code == 1 and "opensubtitles_api_key" in out


def test_plex_sign_in(home, capsys, monkeypatch):
    import plex

    class SignIn:
        def __init__(self, client_id):
            self.polls = 0

        def start(self):
            return 7, "ABCD", "https://app.plex.tv/auth#?code=ABCD"

        def token(self, pin_id, code):
            self.polls += 1
            return "account-token" if self.polls > 1 else None

        def servers(self, token):
            return [{"name": "Den", "url": "http://192.168.1.5:32400", "token": "server-token", "owned": True}]
    monkeypatch.setattr(plex, "PlexSignIn", SignIn)
    args = cli.build_parser().parse_args(["plex-signin"])
    assert cli.cmd_plex_signin(args, {}, wait=lambda s: None) == 0
    saved = core.cfg_load()
    assert (saved["media_server"], saved["plex_url"]) == ("plex", "http://192.168.1.5:32400")
    assert core.unprotect(saved["plex_token"]) == "server-token" and saved["plex_client_id"]
    assert "app.plex.tv/auth" in capsys.readouterr().out


def test_help_and_version(capsys):
    assert cli.main([]) == 2
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert core.APP_VERSION in capsys.readouterr().out


def test_docker_start_runs_as_the_chosen_user(monkeypatch):
    import os
    from types import SimpleNamespace

    import docker_start

    class FakeOS:
        """Just enough of os, so nothing really changes owner (and it runs on Windows too)."""
        path = os.path

        def __init__(self, euid=0):
            self.euid, self.calls, self.environ = euid, [], {}

        def geteuid(self):
            return self.euid

        def makedirs(self, path, exist_ok=False):
            pass

        def walk(self, top):
            return iter([(top, ["cache"], ["config.json"])])

        def stat(self, path):
            return SimpleNamespace(st_uid=0, st_gid=0)

        def chown(self, path, uid, gid):
            self.calls.append(("chown", os.path.basename(path), uid, gid))

        def setgroups(self, groups):
            self.calls.append(("setgroups", groups))

        def setgid(self, gid):
            self.calls.append(("setgid", gid))

        def setuid(self, uid):
            self.calls.append(("setuid", uid))

    fake = FakeOS()
    monkeypatch.setattr(docker_start, "os", fake)
    docker_start.drop_privileges({"PUID": "1000", "PGID": "1001"})
    assert ("chown", "config.json", 1000, 1001) in fake.calls and ("chown", "cache", 1000, 1001) in fake.calls
    assert fake.calls[-3:] == [("setgroups", [1001]), ("setgid", 1001), ("setuid", 1000)]   # group first
    assert fake.environ["HOME"] == docker_start.CONFIG
    for env, euid in (({"PUID": "", "PGID": ""}, 0), ({"PUID": "1000", "PGID": "1000"}, 1000)):
        fake = FakeOS(euid)
        monkeypatch.setattr(docker_start, "os", fake)
        docker_start.drop_privileges(env)                  # not set, or not started as root: nothing
        assert fake.calls == []
