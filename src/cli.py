"""PathRenamer without a window: the command line, and the Docker version.

    pathrenamer rename /downloads/Heat.1995.1080p.mkv --type movie --to /movies
    pathrenamer watch /downloads        (Pro: new downloads are renamed on their own)
    pathrenamer undo | history | subtitles | scan | config | license | update

Everything that is free in the Windows app is free here too: renaming,
undo and history, downloading subtitles and asking the media server to
scan. Settings are the same as the Windows app's, kept in
PATHRENAMER_CONFIG_DIR (Docker: /config). A few can also come from
environment variables, which win over the settings file: PATHRENAMER_TV,
PATHRENAMER_MOVIES, PATHRENAMER_ANIME (library folders), PATHRENAMER_WATCH,
PATHRENAMER_TYPE, PATHRENAMER_ACTION and PATHRENAMER_LICENSE_KEY.
"""
import os

os.environ.setdefault("PATHRENAMER_CONSOLE_LOG", "WARNING")   # before core starts logging

import argparse          # noqa: E402
import difflib           # noqa: E402
import json              # noqa: E402
import logging           # noqa: E402
import platform          # noqa: E402
import signal            # noqa: E402
import sys               # noqa: E402
import threading         # noqa: E402
import time              # noqa: E402
from pathlib import Path  # noqa: E402

import core              # noqa: E402
import updates           # noqa: E402
from core import (APP_VERSION, BUY_URL, DEFAULTS, HISTORY_PATH, LICENSE_KEYS, OPENSUBTITLES_APP_KEY,  # noqa: E402
                  RENAME_ACTIONS, SUB_EXT, VIDEO_EXT, License, OpenSubtitlesClient, OpenSubtitlesError,
                  RenameHistory, Renamer, cfg_load, cfg_save, describe_batch, detect_media_type,
                  fetch_subtitles, make_engine, match_files, parse_languages, place_groups, protect,
                  rename_groups, run_after_rename, scan_folder, server_from_config, unprotect)
from licensing import LicenseError  # noqa: E402
from watchscan import watch_forever  # noqa: E402

SURE = 0.85                      # matches at least this sure are renamed without asking (as in the app)
TYPES = ("auto", "tv", "movie", "anime")
TYPE_NAMES = {"tv": "TV shows", "movie": "Movies", "anime": "Anime"}
ENV_SETTINGS = {
    "PATHRENAMER_TV": "root_tv",
    "PATHRENAMER_MOVIES": "root_movie",
    "PATHRENAMER_ANIME": "root_anime",
    "PATHRENAMER_WATCH": "watch_folder",
    "PATHRENAMER_TYPE": "watch_media_type",
    "PATHRENAMER_ACTION": "rename_action",
}
SECRET_KEYS = {"plex_token", "jellyfin_key", "emby_key", "kodi_password", "opensubtitles_password"}
CHOICES = {"media_server": ("plex", "jellyfin", "emby", "kodi"), "rename_action": RENAME_ACTIONS,
           "watch_media_type": TYPES, "media_type": ("tv", "movie", "anime"),
           "episode_tv": ("dvd", "absolute", "streaming"), "episode_anime": ("dvd", "absolute", "streaming")}
# Settings only the Windows app uses
WINDOW_KEYS = {"theme", "layout", "panel_mode", "density", "show_details", "animations", "files_ratio",
               "first_run", "show_media_info", "confirm_rename", "tray_on_close", "start_with_windows",
               "update_checked_at", "update_available", "update_skip_version", "last_source_folder",
               "presets", "plex_client_id"}
DAY = 86400


class Stop(Exception):
    """Ends a command with a message for the user and an exit code."""

    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


def say(text="", stamp=False):
    if stamp:
        text = time.strftime("%Y-%m-%d %H:%M:%S ") + text
    print(text, flush=True)


def in_docker():
    return os.environ.get("PATHRENAMER_DOCKER") == "1" or Path("/.dockerenv").exists()


# ── settings ─────────────────────────────────────────────────────────────────
def load_settings(env=None):
    """The settings file with the environment variables on top (not saved)."""
    env = os.environ if env is None else env
    cfg = cfg_load()
    for var, key in ENV_SETTINGS.items():
        if env.get(var):
            cfg[key] = env[var]
    return cfg


def save_keys(cfg, keys):
    """Save only these keys, so environment overrides never end up in the file."""
    disk = cfg_load()
    for k in keys:
        if k in cfg:
            disk[k] = cfg[k]
        else:
            disk.pop(k, None)
    cfg_save(disk)


def save_license(lic):
    save_keys(lic.cfg, LICENSE_KEYS)


def license_from_env(lic, env=None):
    """PATHRENAMER_LICENSE_KEY activates Pro on the first start (Docker)."""
    env = os.environ if env is None else env
    key = (env.get("PATHRENAMER_LICENSE_KEY") or "").strip()
    if not key or not lic.enabled or lic.key == key:
        return
    try:
        lic.activate(key, label=f"PathRenamer on {platform.node() or 'Docker'}")
        say("License activated: " + lic.summary())
    except (LicenseError, ConnectionError) as e:
        say(f"Couldn't activate PATHRENAMER_LICENSE_KEY: {e}")
    save_license(lic)


# ── finding and matching files ───────────────────────────────────────────────
def collect(paths):
    """Videos in the given files and folders (or subtitles, for a folder of
    only subtitles), each once."""
    files, seen = [], set()
    for raw in paths:
        p = Path(raw).expanduser()
        if p.is_dir():
            found = scan_folder(str(p)) or scan_folder(str(p), include_subtitles=True)
        elif p.is_file() and p.suffix.lower() in VIDEO_EXT | SUB_EXT:
            found = [p]
        elif p.exists():
            say(f"Skipped {p}: not a video or subtitle file.")
            found = []
        else:
            raise Stop(f"Not found: {p}")
        for f in found:
            key = os.path.normcase(str(f.resolve()))
            if key not in seen:
                seen.add(key)
                files.append(f)
    return files


def destination(cfg, kind, to=None):
    return str(Path(to).expanduser()) if to else (cfg.get(f"root_{kind}") or "").strip()


def match(files, cfg, chosen="auto", to=None, engine=None):
    """[(media type, library folder, [Candidate])], one entry per type."""
    groups = {}
    for f in files:
        groups.setdefault(detect_media_type(f, chosen), []).append(f)
    engine = engine or make_engine(cfg)
    out = []
    for kind, batch in groups.items():
        dst = destination(cfg, kind, to)
        out.append((kind, dst, list(match_files(batch, engine, kind, dst or "."))))
    return out


def verdict(c, include_unsure=False):
    """('ok' | 'unsure' | 'conflict' | 'error', what to tell the user)."""
    if c.error:
        return "error", c.error
    if c.conflict:
        return "conflict", "a file with the new name is already in the library"
    if c.confidence < SURE and not include_unsure:
        return "unsure", f"not sure it's the right title ({c.confidence:.0%}): check it, then add --include-unsure"
    return "ok", ""


def show(kind, dst, cands, include_unsure=False):
    say(f"\n{TYPE_NAMES[kind]} -> {dst or '(no library folder set)'}")
    marks = {"ok": "ok", "unsure": "??", "conflict": "!!", "error": "--"}
    for c in cands:
        state, why = verdict(c, include_unsure)
        say(f"  [{marks[state]}] {c.source.name}")
        if not c.error:
            say(f"       -> {c.dest_rel}")
            for _, rel in c.subtitles:
                say(f"          + {rel}")
        if why:
            say(f"       {why}")


# ── renaming ─────────────────────────────────────────────────────────────────
def place(renamer, cfg, lic, kind, dst, cands, action, cleanup_root=None, source="cli", stamp=False):
    """Rename the candidates, then (Pro) download missing subtitles and ask
    the media server to scan. Returns the RenameResult."""
    result, msg = place_groups(renamer, rename_groups(cands, dst), dst, action=action, source=source,
                               cleanup_root=cleanup_root, cleanup=cfg.get("cleanup_empty_folders", True))
    say(msg, stamp)
    for op in result.failed:
        say(f"  Failed: {op.src.name}: {op.error}", stamp)
    if in_docker() and any("Permission denied" in op.error for op in result.failed):
        say(f"  PathRenamer runs as user {os.getuid()}, group {os.getgid()} (PUID and PGID): give them write "
            "access to your folders, or set PUID and PGID to the owner of the folders.", stamp)
    after(cfg, lic, renamer, result, cands, kind, stamp)
    return result


def subtitle_client(cfg):
    key = (cfg.get("opensubtitles_api_key") or "").strip() or OPENSUBTITLES_APP_KEY
    return OpenSubtitlesClient(key, cfg.get("opensubtitles_username", ""),
                               unprotect(cfg.get("opensubtitles_password", "")), APP_VERSION)


def after(cfg, lic, renamer, result, cands, kind, stamp=False):
    """Pro, like the app: missing subtitles, then a media server scan."""
    subs_on = bool(cfg.get("subs_auto")) and lic.allows("subs_auto")
    scan_on = bool(cfg.get("plex_auto_scan")) and lic.allows("plex_auto_scan")
    if not result.succeeded or not (subs_on or scan_on):
        return
    placed = {os.path.normcase(str(op.src)): op.dst for op in result.succeeded}
    videos = [(c, placed[os.path.normcase(str(c.source))]) for c in cands
              if c.source.suffix.lower() not in SUB_EXT and os.path.normcase(str(c.source)) in placed]
    subtitles = None
    if subs_on and videos:
        subtitles = {"client": subtitle_client(cfg),
                     "languages": parse_languages(cfg.get("subs_languages", "")) or ["eng"],
                     "hearing_impaired": bool(cfg.get("subs_hearing_impaired"))}
    server = server_from_config(cfg, unprotect) if scan_on else None
    if subtitles is None and server is None:
        return
    downloaded, scanned, problems = run_after_rename(videos, [op.dst for op in result.succeeded], kind,
                                                     subtitles, server)
    if downloaded and renamer.last_batch_id:
        renamer.history.add_ops(renamer.last_batch_id, [("OpenSubtitles", f, "download") for f in downloaded])
    parts = []
    if downloaded:
        parts.append(f"downloaded {len(downloaded)} subtitle(s)")
    if scanned:
        parts.append(f"{server.label} is scanning {', '.join(scanned)}")
    parts += problems
    if parts:
        say("After renaming: " + " · ".join(parts), stamp)


def confirm(question):
    if not sys.stdin.isatty():
        return False
    try:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def cmd_rename(args, cfg):
    files = collect(args.paths)
    if not files:
        raise Stop("No videos found there.")
    chosen = args.type or os.environ.get("PATHRENAMER_TYPE") or "auto"
    action = args.action or cfg.get("rename_action") or "move"
    if action not in RENAME_ACTIONS:
        raise Stop(f"Unknown action {action!r}: use move, copy or hardlink.", 2)
    say(f"Matching {len(files)} file(s)…")
    batches = match(files, cfg, chosen, args.to)
    todo, missing_root = [], []
    for kind, dst, cands in batches:
        show(kind, dst, cands, args.include_unsure)
        chosen_cands = [c for c in cands if verdict(c, args.include_unsure)[0] == "ok"]
        if chosen_cands and not dst:
            missing_root.append(kind)
        elif chosen_cands:
            todo.append((kind, dst, chosen_cands))
    say()
    for kind in missing_root:
        env = {"tv": "PATHRENAMER_TV", "movie": "PATHRENAMER_MOVIES", "anime": "PATHRENAMER_ANIME"}[kind]
        say(f"No library folder for {TYPE_NAMES[kind].lower()}: add --to FOLDER, set {env}, or run "
            f"pathrenamer config set root_{kind} FOLDER")
    count = sum(len(c) for _, _, c in todo)
    if not count:
        raise Stop("Nothing to rename.", 1 if missing_root else 0)
    if args.dry_run:
        say(f"Dry run: {count} file(s) would be renamed.")
        return 0
    verb = core.ACTION_WORDS[action][0].lower()
    if not args.yes and not confirm(f"{verb.capitalize()} {count} file(s) into the library?"):
        raise Stop("Nothing was renamed. Add --yes to rename without asking.", 2)
    cleanup_root = None
    dirs = [Path(p).expanduser() for p in args.paths if Path(p).expanduser().is_dir()]
    if len(dirs) == 1 and len(args.paths) == 1:
        cleanup_root = dirs[0]
    renamer = Renamer(RenameHistory(HISTORY_PATH))
    lic = License(cfg)
    failed, batches = 0, []
    for kind, dst, cands in todo:
        result = place(renamer, cfg, lic, kind, dst, cands, action, cleanup_root)
        failed += len(result.failed)
        if renamer.last_batch_id:
            batches.append(renamer.last_batch_id)
    if len(batches) == 1:
        say("Undo with: pathrenamer undo")
    elif batches:
        say("Undo with: " + " and ".join(f"pathrenamer undo {b}" for b in reversed(batches)))
    return 1 if failed else 0


# ── watching a folder ────────────────────────────────────────────────────────
class Watcher:
    """pathrenamer watch: new downloads are matched as soon as they finish.
    With Pro (or during the trial) sure matches are renamed into the library;
    otherwise what would be renamed is reported."""

    def __init__(self, cfg, folder, chosen, to, action, rename=True, stamp=True, clock=time.time):
        self.cfg, self.folder, self.chosen, self.to, self.action = cfg, folder, chosen, to, action
        self.rename = rename
        self.stamp = stamp
        self.clock = clock
        self.lic = License(cfg)
        self.renamer = Renamer(RenameHistory(HISTORY_PATH))
        self.engine = make_engine(cfg)
        self.stop = threading.Event()
        self._next_house = 0.0
        self._next_update = 0.0

    def log(self, text):
        say(text, self.stamp)

    def start_trial_if_needed(self):
        if not self.rename or not self.lic.enabled or self.lic.allows():
            return
        if self.lic.can_try() and self.lic.start_trial():
            save_license(self.lic)
            self.log("Your free 14-day Pro trial has started: new downloads are renamed on their own.")
        else:
            self.log("Automatic renaming is part of PathRenamer Pro. New downloads are reported here; "
                     f"rename them with 'pathrenamer rename'. Get Pro: {BUY_URL}")

    def handle(self, paths):
        files = [p for p in paths if p.exists()]
        if not files:
            return
        for kind, dst, cands in match(files, self.cfg, self.chosen, self.to, self.engine):
            allowed = self.rename and self.lic.allows("watch_auto_rename")
            sure = [c for c in cands if allowed and dst and verdict(c)[0] == "ok"
                    and c.source.suffix.lower() not in SUB_EXT]
            if sure:
                self.log(f"{len(sure)} new file(s) for {TYPE_NAMES[kind].lower()}:")
                place(self.renamer, self.cfg, self.lic, kind, dst, sure, self.action,
                      cleanup_root=self.folder, source="watch", stamp=self.stamp)
            done = {id(c) for c in sure}
            for c in cands:
                if id(c) in done:
                    continue
                state, why = verdict(c)
                if state == "ok" and not dst:
                    why = f"no library folder for {TYPE_NAMES[kind].lower()} (set root_{kind})"
                elif state == "ok":
                    why = "automatic renaming is part of PathRenamer Pro" if self.rename else "reported only"
                target = f" -> {c.dest_rel}" if not c.error else ""
                self.log(f"Needs a look: {c.source}{target} ({why})")

    def housekeeping(self):
        """Every few hours: the weekly license check, and a daily update check."""
        now = self.clock()
        if now >= self._next_house:
            self._next_house = now + 6 * 3600
            changed = self.lic.touch()
            if self.lic.needs_check():
                changed = self.lic.check() or changed
            if changed:
                save_license(self.lic)
        if now >= self._next_update and self.cfg.get("check_updates", True):
            self._next_update = now + DAY
            try:
                rel = updates.check(APP_VERSION)
            except updates.UpdateError:
                rel = None
            if rel is not None:
                how = (f"docker pull {updates.DOCKER_IMAGE}:latest, then recreate the container"
                       if in_docker() else rel.page_url)
                self.log(f"PathRenamer {rel.version} is available (you have {APP_VERSION}): {how}")

    def should_stop(self):
        try:
            self.housekeeping()
        except Exception as e:                    # never stop watching over a check
            logging.getLogger("PathRenamer").warning(f"Housekeeping failed: {e!r}")
        return self.stop.is_set()

    def run(self, existing=False, settle=15.0, poll=10.0):
        self.start_trial_if_needed()
        self.log(f"Watching {self.folder} for new downloads (PathRenamer {APP_VERSION})."
                 + ("" if in_docker() else " Stop with Ctrl+C."))

        def on_ready(paths):
            try:
                self.handle(paths)
            except Exception as e:                # report it and keep watching
                logging.getLogger("PathRenamer").exception("Watch batch failed")
                self.log(f"Couldn't handle {len(paths)} new file(s): {e}")

        watch_forever(self.folder, core._watchable, on_ready, skip_dir=core._skip_watch_dir,
                      settle_seconds=settle, poll_seconds=poll, include_existing=existing,
                      should_stop=self.should_stop, on_error=self.log, sleep=self.stop.wait)
        self.log("Stopped watching.")


def cmd_watch(args, cfg):
    folder = args.folder or (cfg.get("watch_folder") or "").strip()
    if not folder:
        raise Stop("Which folder? Run: pathrenamer watch /downloads (Docker: set PATHRENAMER_WATCH).", 2)
    if not Path(folder).expanduser().is_dir():
        raise Stop(f"The watch folder doesn't exist: {folder}")
    action = args.action or cfg.get("rename_action") or "move"
    if action not in RENAME_ACTIONS:
        raise Stop(f"Unknown action {action!r}: use move, copy or hardlink.", 2)
    chosen = args.type or cfg.get("watch_media_type") or "auto"
    w = Watcher(cfg, str(Path(folder).expanduser()), chosen, args.to, action, rename=not args.no_rename)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, lambda *a: w.stop.set())
        except (ValueError, OSError, AttributeError):
            pass
    w.run(existing=args.existing, settle=args.settle, poll=args.poll)
    return 0


# ── the other commands ───────────────────────────────────────────────────────
def cmd_undo(args, cfg):
    renamer = Renamer(RenameHistory(HISTORY_PATH))
    batch = renamer.history.get(args.id) if args.id else renamer.history.last_undoable()
    if batch is None:
        raise Stop("Nothing to undo." if not args.id else f"No rename with ID {args.id} (see pathrenamer history).")
    if batch.get("undone"):
        raise Stop("That rename was already undone.")
    result = renamer.undo_batch(batch["id"])
    say(f"Undid: {describe_batch(batch)} ({len(result.succeeded)} put back)")
    for op in result.failed:
        say(f"  Couldn't put back {op.src.name}: {op.error}")
    return 1 if result.failed else 0


def cmd_history(args, cfg):
    batches = RenameHistory(HISTORY_PATH).batches()[:max(1, args.limit)]
    if not batches:
        say("No renames yet.")
        return 0
    for b in batches:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(b.get("time") or 0))
        state = "  (undone)" if b.get("undone") else ""
        say(f"{b['id']}  {when}  {describe_batch(b)}{state}")
    say("\nUndo one with: pathrenamer undo ID")
    return 0


def cmd_subtitles(args, cfg):
    videos = [f for f in collect(args.paths) if f.suffix.lower() not in SUB_EXT]
    if not videos:
        raise Stop("No videos found there.")
    client = subtitle_client(cfg)
    if not client.api_key:
        raise Stop("Downloading subtitles needs an OpenSubtitles API key (free at opensubtitles.com): "
                   "pathrenamer config set opensubtitles_api_key YOUR_KEY")
    languages = parse_languages(args.languages or cfg.get("subs_languages", "")) or ["eng"]
    chosen = args.type or os.environ.get("PATHRENAMER_TYPE") or "auto"
    say(f"Looking for {', '.join(languages)} subtitles for {len(videos)} video(s)…")
    saved, problems = [], []
    try:
        client.login()
        for _, _, cands in match(videos, cfg, chosen):
            for c in cands:
                try:
                    saved += fetch_subtitles(client, c.source, languages, title=c.title, year=c.year or None,
                                             season=c.season, episodes=c.episodes, media_type=c.media_type,
                                             hearing_impaired=bool(cfg.get("subs_hearing_impaired")))
                except OSError as e:
                    problems.append(f"{c.source.name}: {e.strerror or e}")
    except OpenSubtitlesError as e:               # quota or sign-in: the rest would fail too
        problems.append(str(e))
    for f in saved:
        say(f"  + {f}")
    say(f"Downloaded {len(saved)} subtitle(s)." if saved else "No new subtitles found in your languages.")
    for p in problems:
        say(f"  {p}")
    return 1 if problems else 0


def cmd_scan(args, cfg):
    server = server_from_config(cfg, unprotect)
    if not server.configured:
        raise Stop("Connect your media server first, for example:\n"
                   "  pathrenamer config set media_server jellyfin\n"
                   "  pathrenamer config set jellyfin_url http://192.168.1.20:8096\n"
                   "  pathrenamer config set jellyfin_key YOUR_API_KEY\n"
                   "(media_server: plex, jellyfin, emby or kodi)")
    titles = server.scan_all()
    say(f"{server.label} is scanning {', '.join(titles)}." if titles else
        f"{server.label} has no movie or TV libraries to scan.")
    return 0


def cmd_plex_signin(args, cfg, wait=time.sleep, timeout=300):
    """Sign in with Plex like the app does: approve PathRenamer on plex.tv,
    and the server's address and token are saved."""
    import uuid
    from plex import PlexSignIn
    disk = cfg_load()
    client_id = disk.get("plex_client_id") or uuid.uuid4().hex
    signin = PlexSignIn(client_id)
    pin_id, code, page = signin.start()
    say(f"Open this page on any device, sign in to Plex and allow PathRenamer:\n  {page}\nWaiting…")
    token, waited = None, 0
    while waited < timeout:
        token = signin.token(pin_id, code)
        if token:
            break
        wait(2)
        waited += 2
    if not token:
        raise Stop("PathRenamer wasn't allowed within 5 minutes. Run pathrenamer plex-signin again.")
    servers = signin.servers(token)
    if not servers:
        raise Stop("Signed in, but this Plex account has no server.")
    server = next((s for s in servers if args.server and s["name"].lower() == args.server.lower()), servers[0])
    disk.update(media_server="plex", plex_url=server["url"], plex_token=protect(server["token"]),
                plex_client_id=client_id)
    cfg_save(disk)
    say(f"Connected to {server['name']} at {server['url']}.")
    if len(servers) > 1:
        say("Other servers on this account: " + ", ".join(s["name"] for s in servers if s is not server)
            + " (pick one with --server NAME).")
    return 0


def _parse_value(key, text):
    default = DEFAULTS.get(key)
    if isinstance(default, bool):
        low = text.strip().lower()
        if low in ("1", "true", "yes", "on"):
            return True
        if low in ("0", "false", "no", "off"):
            return False
        raise Stop(f"{key} is on or off: use true or false.", 2)
    if isinstance(default, (int, float)) and not isinstance(default, bool):
        try:
            return type(default)(text)
        except ValueError:
            raise Stop(f"{key} needs a number.", 2)
    if isinstance(default, (dict, list)):
        raise Stop(f"{key} can't be set from the command line.", 2)
    if key in CHOICES and text not in CHOICES[key]:
        raise Stop(f"{key} is one of: {', '.join(CHOICES[key])}.", 2)
    return text


def _shown(key, value):
    if key in SECRET_KEYS:
        return "(set)" if value else "(not set)"
    return json.dumps(value, ensure_ascii=False)


def cmd_config(args, cfg):
    disk = cfg_load()
    keys = sorted(k for k in DEFAULTS if k not in WINDOW_KEYS)
    if args.action == "show":
        for k in keys:
            say(f"{k} = {_shown(k, disk.get(k))}")
        overridden = [f"{var} ({key})" for var, key in ENV_SETTINGS.items() if os.environ.get(var)]
        if overridden:
            say("\nSet by environment variables (these win): " + ", ".join(overridden))
        say(f"\nSettings file: {core.CONFIG_PATH}")
        return 0
    key = args.key
    if key in LICENSE_KEYS:
        raise Stop("License details can't be set by hand: use pathrenamer license activate KEY.", 2)
    if key not in DEFAULTS:
        close = difflib.get_close_matches(key, keys, n=3)
        raise Stop(f"Unknown setting {key!r}." + (f" Did you mean {', '.join(close)}?" if close else
                                                   " See pathrenamer config show."), 2)
    if args.action == "get":
        say(_shown(key, disk.get(key)) if key in SECRET_KEYS else
            (disk.get(key) if isinstance(disk.get(key), str) else json.dumps(disk.get(key))))
        return 0
    value = _parse_value(key, args.value)
    disk[key] = protect(value) if key in SECRET_KEYS else value
    cfg_save(disk)
    say(f"{key} = {_shown(key, value)}")
    return 0


def cmd_license(args, cfg):
    disk = cfg_load()
    lic = License(disk)
    try:
        if args.action == "activate":
            lic.activate(args.key, label=f"PathRenamer on {platform.node() or 'this computer'}")
        elif args.action == "deactivate":
            lic.deactivate()
        elif args.action == "check":
            if not lic.key:
                raise Stop("No license key on this computer. Activate one: pathrenamer license activate KEY")
            if not lic.check():
                say("Couldn't reach the license server; Pro keeps working offline for 30 days.")
    except (LicenseError, ConnectionError) as e:
        save_license(lic)
        raise Stop(str(e))
    save_license(lic)
    say(lic.summary())
    if args.action == "status" and lic.enabled and not lic.is_pro():
        say(f"Get Pro: {BUY_URL}")
    return 0


def cmd_update(args, cfg):
    try:
        rel = updates.check(APP_VERSION)
    except updates.UpdateError as e:
        raise Stop(str(e))
    if rel is None:
        say(f"PathRenamer {APP_VERSION} is the latest version.")
        return 0
    say(f"PathRenamer {rel.version} is available (you have {APP_VERSION}).")
    if in_docker():
        say(f"Update: docker pull {updates.DOCKER_IMAGE}:latest, then recreate the container "
            "(docker compose up -d).")
    else:
        say(f"Download: {rel.page_url}")
    return 0


# ── command line ─────────────────────────────────────────────────────────────
def build_parser():
    p = argparse.ArgumentParser(
        prog="pathrenamer",
        description="Rename movies, TV shows and anime the way Plex, Jellyfin, Emby and Kodi want them.",
        epilog="Help for one command: pathrenamer COMMAND --help. Guides: " + core.WEBSITE_URL)
    p.add_argument("--version", action="version", version=f"PathRenamer {APP_VERSION}")
    p.add_argument("-v", "--verbose", action="count", default=0, help="show what happens (-vv: everything)")
    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    r = sub.add_parser("rename", help="rename files or folders into your library (free)",
                       description="Match the videos, show their new names, and rename them after you agree.")
    r.add_argument("paths", nargs="+", metavar="PATH", help="video files or folders")
    r.add_argument("--type", choices=TYPES, help="what they are (default: auto, by the name)")
    r.add_argument("--to", metavar="FOLDER", help="library folder (default: the one in the settings)")
    r.add_argument("--action", choices=RENAME_ACTIONS, help="move (default), copy or hardlink")
    r.add_argument("-n", "--dry-run", action="store_true", help="only show the new names")
    r.add_argument("-y", "--yes", action="store_true", help="rename without asking")
    r.add_argument("--include-unsure", action="store_true", help="also rename matches it isn't sure about")

    w = sub.add_parser("watch", help="handle new downloads as they arrive (Pro: renames them on their own)",
                       description="Watch a folder. Finished downloads are matched; with PathRenamer Pro "
                                   "(or during the free trial) sure matches are renamed into the library.")
    w.add_argument("folder", nargs="?", help="the folder to watch (default: PATHRENAMER_WATCH)")
    w.add_argument("--type", choices=TYPES, help="what arrives there (default: auto)")
    w.add_argument("--to", metavar="FOLDER", help="library folder for everything")
    w.add_argument("--action", choices=RENAME_ACTIONS, help="move (default), copy or hardlink")
    w.add_argument("--no-rename", action="store_true", help="only report what would be renamed")
    w.add_argument("--existing", action="store_true", help="also handle what is already in the folder")
    w.add_argument("--settle", type=float, default=15.0, help=argparse.SUPPRESS)
    w.add_argument("--poll", type=float, default=10.0, help=argparse.SUPPRESS)

    u = sub.add_parser("undo", help="put the last rename back (or the one with ID)")
    u.add_argument("id", nargs="?", help="from pathrenamer history")
    h = sub.add_parser("history", help="list earlier renames")
    h.add_argument("--limit", type=int, default=20)
    s = sub.add_parser("subtitles", help="download missing subtitles (free; needs an OpenSubtitles key)")
    s.add_argument("paths", nargs="+", metavar="PATH")
    s.add_argument("--languages", help='e.g. "English, Spanish" (default: the settings)')
    s.add_argument("--type", choices=TYPES)
    sub.add_parser("scan", help="ask Plex, Jellyfin, Emby or Kodi to scan its libraries now (free)")
    ps = sub.add_parser("plex-signin", help="connect to your Plex server by signing in to Plex")
    ps.add_argument("--server", help="the server's name, if the account has more than one")

    c = sub.add_parser("config", help="show or change settings")
    csub = c.add_subparsers(dest="action", metavar="ACTION")
    csub.required = True
    csub.add_parser("show", help="list the settings")
    g = csub.add_parser("get", help="show one setting")
    g.add_argument("key")
    st = csub.add_parser("set", help="change one setting")
    st.add_argument("key")
    st.add_argument("value")

    lc = sub.add_parser("license", help="PathRenamer Pro: status, activate, deactivate, check")
    lsub = lc.add_subparsers(dest="action", metavar="ACTION")
    lsub.required = True
    lsub.add_parser("status", help="Free, trial or Pro")
    a = lsub.add_parser("activate", help="activate a license key on this computer")
    a.add_argument("key")
    lsub.add_parser("deactivate", help="free this computer's seat")
    lsub.add_parser("check", help="check the license with the license server now")

    sub.add_parser("update", help="is there a newer PathRenamer?")
    return p


COMMANDS = {"rename": cmd_rename, "watch": cmd_watch, "undo": cmd_undo, "history": cmd_history,
            "subtitles": cmd_subtitles, "scan": cmd_scan, "plex-signin": cmd_plex_signin, "config": cmd_config,
            "license": cmd_license, "update": cmd_update}


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2
    if args.verbose:
        core._console.setLevel(logging.INFO if args.verbose == 1 else logging.DEBUG)
    try:
        core._console.setStream(sys.stderr)          # messages for people stay on stdout
    except ValueError:                               # the old stream was already closed
        core._console.stream = sys.stderr
    cfg = load_settings()
    if not (args.command == "license" and args.action in ("deactivate", "activate")):
        license_from_env(License(cfg_load()))
        cfg = load_settings()                         # (the license state may have changed)
    try:
        return COMMANDS[args.command](args, cfg)
    except Stop as e:
        print(str(e), file=sys.stderr if e.code else sys.stdout, flush=True)
        return e.code
    except KeyboardInterrupt:
        return 130
    except core.MediaServerError as e:
        print(str(e), file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
