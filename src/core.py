"""PathRenamer's engine without any window: settings, scanning folders,
reading release names, looking titles up, building Plex names and
renaming with undo. The Windows app (main.py) and the command line /
Docker version (cli.py) both use it."""
import sys, os, json, re, shutil, gzip, time, xml.etree.ElementTree as ET, logging, traceback, html, functools
import dataclasses
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple, Any

from version import APP_VERSION

# ── Logging / crash handler ───────────────────────────────────────────────────
# PATHRENAMER_CONFIG_DIR picks another settings folder (Docker: /config)
if os.environ.get("PATHRENAMER_CONFIG_DIR"):
    _APP_DIR = Path(os.environ["PATHRENAMER_CONFIG_DIR"])
else:
    _APPDATA_BASE = Path(os.getenv("APPDATA") or Path.home())
    _OLD_APP_DIR = _APPDATA_BASE / ("Plex" + "Renamer")
    _APP_DIR = _APPDATA_BASE / "PathRenamer"
    if _OLD_APP_DIR.exists() and not _APP_DIR.exists():
        try:
            _OLD_APP_DIR.rename(_APP_DIR)
        except OSError:
            pass


_LOG_DIR = _APP_DIR
_LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_PATH = _LOG_DIR / "pathrenamer.log"

import logging.handlers
# The console gets everything unless PATHRENAMER_CONSOLE_LOG says otherwise
# (the command line shows only warnings). The log files stay small: old
# lines roll over into .1 and .2 files (PathRenamer may run for weeks).
_console = logging.StreamHandler(sys.stdout)
_console.setLevel(getattr(logging, os.environ.get("PATHRENAMER_CONSOLE_LOG", "DEBUG").upper(), logging.DEBUG))
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=5_000_000, backupCount=2, encoding="utf-8"),
        _console,
    ]
)
log = logging.getLogger("PathRenamer")
tvmaze_log = logging.getLogger("TVMaze")
tvmaze_log.setLevel(logging.DEBUG)
tvmaze_handler = logging.handlers.RotatingFileHandler(_LOG_DIR / "tvmaze_debug.log", maxBytes=2_000_000,
                                                      backupCount=1, encoding="utf-8")
tvmaze_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
tvmaze_log.addHandler(tvmaze_handler)
tvmaze_log.info("=== TVMaze Debug Log ===")
log.info("=== PathRenamer starting up ===")
log.info(f"Log file: {LOG_PATH}")
log.info(f"Python: {sys.version}")

def _global_except_hook(exc_type, exc_value, exc_tb):
    msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    log.critical(f"UNHANDLED EXCEPTION:\n{msg}")
    try:
        from PyQt6.QtWidgets import QMessageBox, QApplication
        from PyQt6.QtCore import QThread
        app = QApplication.instance()

        if app and QThread.currentThread() is app.thread():
            QMessageBox.critical(None, "PathRenamer Crashed",
                f"An error occurred:\n\n{exc_value}\n\nFull log saved to:\n{LOG_PATH}")
    except Exception:
        pass
    sys.__excepthook__(exc_type, exc_value, exc_tb)

sys.excepthook = _global_except_hook


import errno
import threading
import uuid
import requests
import re
from parser import analyse_path
from history import RenameHistory, describe as describe_batch
from mediainfo import read_media_info
from automation import after_rename as run_after_rename, fetch_subtitles
import licensing
from licensing import (License, PRO_FEATURES, STATE_KEYS as LICENSE_KEYS,
                       BUY_URL, MANAGE_URL)
licensing.DATA_DIR = _APP_DIR            # the license state's signing key lives with the settings
from opensubs import OpenSubtitlesClient, OpenSubtitlesError, parse_languages
from plex import PlexClient, PlexSignIn
from mediaservers import (SERVER_KINDS, VIDEO_LIBRARY_TYPES, JellyfinClient, KodiClient, MediaServerError,
                          server_from_config)
from secret import protect, unprotect
try:
    import build_info                    # written by the release build: the app's own API keys
except ImportError:
    build_info = None
OPENSUBTITLES_APP_KEY = getattr(build_info, "OPENSUBTITLES_API_KEY", "") or ""
WEBSITE_URL = "https://royalkiller2222.github.io/PathRenamer/"
from scanner import (SUBTITLE_EXTENSIONS, detect_subtitle_language, is_language_only_name,
                     unique_subtitle_filename)
from rapidfuzz import fuzz

# ═══════════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════════
CONFIG_PATH = _APP_DIR / "config.json"
HISTORY_PATH = _APP_DIR / "history.json"
DEFAULTS = {
    "tmdb_api_key": "",
    "last_source_folder": "",
    "root_tv": "",
    "root_movie": "",
    "root_anime": "",
    "media_type": "tv",
    "format_tv": "{show} ({year})/Season {s00}/{show} {s00e00} - {title}",
    # Each edition gets its own folder, as Plex wants when movies have folders
    "format_movie": "{title} ({year}) {edition}/{title} ({year}) {edition} {res} {vc} {ach} {c}",
    "format_anime": "{show} ({year})/Season {s00}/{show} {s00e00} - {title}",
    "episode_tv": "dvd",
    "episode_anime": "dvd",
    "rename_subtitles": True,
    "use_tvmaze": True,            # data sources (Settings → Data Sources)
    "use_anidb": True,
    "use_cinemeta": True,
    "theme": "dark",
    "show_media_info": True,
    "cleanup_empty_folders": True,
    "confirm_rename": True,
    "first_run": True,
    # Window layout (Settings → Appearance → Layout)
    "layout": "modern",            # "modern" | "classic"
    "panel_mode": "auto",          # modern: "auto" (by window width) | "side" | "stacked"
    "density": "comfortable",      # modern: "comfortable" | "compact"
    "show_details": True,          # modern: details panel under the preview
    "animations": True,            # modern: smooth panel animations
    "files_ratio": 0.3,            # modern: share of the window for the file list
    "rename_action": "move",       # "move" | "copy" | "hardlink" (keeps torrents seeding)
    "read_media_info": True,       # fill {res} {vc} {c} {ach} from the video when the name lacks them
    "presets": {},                 # name -> saved settings (PRESET_KEYS)
    # Automation (Settings → Automation and Subtitles). The automatic parts are Pro.
    "watch_enabled": False,
    "watch_folder": "",
    "watch_media_type": "auto",    # "auto" (TV or movie by name) | "tv" | "movie" | "anime"
    "watch_auto_rename": False,    # Pro: rename sure matches without asking
    "watch_include_existing": False,
    "plex_url": "",
    "plex_token": "",              # encrypted (secret.protect)
    "plex_client_id": "",
    "plex_auto_scan": False,       # Pro: ask the media server to scan after renaming
    "media_server": "plex",        # "plex" | "jellyfin" | "emby" | "kodi"
    "jellyfin_url": "",
    "jellyfin_key": "",            # encrypted
    "emby_url": "",
    "emby_key": "",                # encrypted
    "kodi_url": "",
    "kodi_user": "",
    "kodi_password": "",           # encrypted
    "subs_languages": "English",
    "subs_auto": False,            # Pro: download missing subtitles after renaming
    "subs_hearing_impaired": False,
    "opensubtitles_api_key": "",   # the user's own key (releases have one built in)
    "opensubtitles_username": "",
    "opensubtitles_password": "",  # encrypted
    # Updates (Help → Check for Updates) and the tray (Settings → Options)
    "check_updates": True,         # look for a new version once a day
    "update_checked_at": 0,
    "update_available": "",        # the newer version the last check found
    "update_skip_version": "",     # "Skip This Version"
    "tray_on_close": False,        # closing the window keeps PathRenamer running in the tray
    "start_with_windows": False,   # start in the tray when Windows starts
}

# What a preset remembers
PRESET_KEYS = ("media_type", "format_tv", "format_movie", "format_anime", "episode_tv", "episode_anime",
               "root_tv", "root_movie", "root_anime", "rename_action", "rename_subtitles",
               "use_tvmaze", "use_anidb", "use_cinemeta")

LAYOUTS = ("modern", "classic")
PANEL_MODES = ("auto", "side", "stacked")
DENSITIES = ("comfortable", "compact")

# Previous default movie templates. cfg_load() upgrades configs that still
# use one of them to the current default.
_OLD_MOVIE_DEFAULTS = {
    "{title} ({year})/{title} ({year})",
    "{title} ({year})/{title} ({year}).{ext}",
    "{title} ({year})/{title} ({year}) {res} {vc} {ach} {c}",     # 1.3.0, before editions
    "{title} ({year})/{title} ({year}) {edition} {res} {vc} {ach} {c}",   # 1.4: editions shared a folder
}

def cfg_load():
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = {**DEFAULTS, **json.load(f)}
            if cfg.get("format_movie") in _OLD_MOVIE_DEFAULTS:
                cfg["format_movie"] = DEFAULTS["format_movie"]
            return cfg
        except Exception:
            pass
    return dict(DEFAULTS)

def cfg_save(cfg):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

# ═══════════════════════════════════════════════════════════════════
#  SCANNER
# ═══════════════════════════════════════════════════════════════════
VIDEO_EXT = {".mkv",".mp4",".avi",".mov",".wmv",".m4v",".ts",".m2ts",".flv",".webm",".mpg",".mpeg"}
SUB_EXT   = set(SUBTITLE_EXTENSIONS)     # .srt .sub .idx .ass .ssa .vtt .sup .smi

# Sub-folders holding samples and bonus material, not episodes or movies
# (Plex's extras folder names plus the usual release-folder ones).
_SKIP_SCAN_DIRS = {"sample", "samples", "extras", "featurettes", "behind the scenes",
                   "deleted scenes", "interviews", "trailers", "shorts", "bonus"}
# "Movie.2019.1080p-sample.mkv", "sample-movie.mkv"
_SAMPLE_FILE = re.compile(r"(?:^|[\s._-])sample(?:$|[\s._-])", re.IGNORECASE)

def scan_folder(folder, include_subtitles=False):
    """Video files (and optionally subtitles) under folder. Samples and files in
    extras/sample sub-folders are skipped: renamed like the main file they would
    collide with it."""
    result = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if d.strip().lower() not in _SKIP_SCAN_DIRS)
        for name in sorted(files):
            p = Path(root) / name
            if p.suffix.lower() in VIDEO_EXT:
                if _SAMPLE_FILE.search(p.stem):
                    continue
                result.append(p)
            elif include_subtitles and p.suffix.lower() in SUB_EXT:
                result.append(p)
    return result

def _watchable(path):
    """A video the watch folder should report (not a sample)."""
    return path.suffix.lower() in VIDEO_EXT and not _SAMPLE_FILE.search(path.stem)

def _skip_watch_dir(name):
    return name.strip().lower() in _SKIP_SCAN_DIRS

def _sub_matches_video(sub_stem, video_stem):
    """True if the subtitle is named after the video: "Movie" -> "Movie.en",
    "Movie_forced", "Movie (SDH)". "Show - 1" must not match "Show - 10"."""
    s, v = sub_stem.lower(), video_stem.lower()
    start = s.find(v)
    while start >= 0:
        after = s[start + len(v):start + len(v) + 1]
        before = s[start - 1:start] if start else ""
        if (not after or not after.isalnum()) and (not before or not before.isalnum()):
            return True
        start = s.find(v, start + 1)
    return False

# Folders next to a video that hold its subtitles (compared lower-case)
SUBTITLE_DIR_NAMES = {"subs", "subtitles", "subtitle", "sub", "captions", "vobsub", "english", "字幕"}
# S01E02 / 1x02, with the text before it (the show name)
_EPISODE_TAG = re.compile(r"(?i)(?<![a-z0-9])s(\d{1,2})[ ._-]?e(\d{1,3})(?!\d)|(?<![0-9])(\d{1,2})x(\d{2,3})(?!\d)")

@functools.lru_cache(maxsize=8192)
def _episode_tag(stem):
    """(show name squashed to lower-case letters/digits, (season, episode)) or None."""
    m = _EPISODE_TAG.search(stem)
    if not m:
        return None
    season, episode = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
    return re.sub(r"[\W_]+", "", stem[:m.start()].lower()), (int(season), int(episode))

# What may follow a show name and still be the same show: a year or a country
# ("doctorwho" / "doctorwho2005", "theoffice" / "theofficeus"), not more words
# ("lost" / "lostgirl" are different shows).
_SAME_SHOW_SUFFIX = re.compile(r"(?:19|20)\d\d|us|uk|au|nz|ca")

def _same_show(a, b):
    if a == b:
        return True
    short, long_ = sorted((a, b), key=len)
    return bool(short) and long_.startswith(short) and bool(_SAME_SHOW_SUFFIX.fullmatch(long_[len(short):]))

def _list_dir(folder):
    """(file names, folder names) in folder, sorted case-insensitively.
    os.scandir gives the entry types with the listing, which keeps this fast
    in download folders with thousands of entries."""
    files, dirs = [], []
    try:
        with os.scandir(folder) as it:
            for e in it:
                try:
                    if e.is_file():
                        files.append(e.name)
                    elif e.is_dir():
                        dirs.append(e.name)
                except OSError:
                    pass
    except OSError:
        return [], []
    files.sort(key=str.lower)
    dirs.sort(key=str.lower)
    return files, dirs

def _split_name(name):
    """("Movie.en", ".srt") like Path.stem / Path.suffix, for a bare file name."""
    i = name.rfind(".")
    return (name[:i], name[i:].lower()) if i > 0 else (name, "")

def find_subtitle_siblings(video):
    """Subtitles that belong to a video: next to it, in a Subs/Subtitles folder
    beside it, or in a per-episode folder inside that (season packs:
    "Subs/<video name>/2_English.srt").

    A subtitle belongs to the video when
      - it is named after the video ("Movie.en.srt"), unless another video in
        the folder has a longer name it also matches ("Movie.Extended.en.srt");
      - the video is the only one in its folder and the subtitle is named only
        by language ("English.srt"), or sits in the Subs folder (any name);
      - (season packs) it has the same SxxEyy and show name as the video and no
        other video in the folder has that episode.
    """
    parent = video.parent
    vstem = video.stem
    names, dirnames = _list_dir(parent)
    own = os.path.normcase(video.name)
    others = []                          # names (without extension) of the other videos
    for n in names:
        stem, ext = _split_name(n)
        if ext in VIDEO_EXT and os.path.normcase(n) != own and not _SAMPLE_FILE.search(stem):
            others.append(stem)
    only_video = not others
    tag = _episode_tag(vstem)
    episode_unique = tag is not None and not any(
        (t := _episode_tag(o)) and t[1] == tag[1] for o in others)

    def same_episode(stem, in_sub_dir):
        if not episode_unique:
            return False
        t = _episode_tag(stem)
        if not t or t[1] != tag[1]:
            return False
        if not t[0]:                     # "Subs/S01E02.eng.srt"
            return in_sub_dir
        return bool(tag[0]) and _same_show(t[0], tag[0])

    def other_episode(stem):
        # Named for another episode: e.g. one episode downloaded from a season
        # pack whose Subs folder still has every episode's subtitles
        t = _episode_tag(stem)
        return t is not None and tag is not None and t[1] != tag[1]

    def belongs(stem, in_sub_dir, folder=False):
        if _sub_matches_video(stem, vstem):
            # "Movie.Extended.en.srt" belongs to "Movie.Extended.mkv", not "Movie.mkv"
            return not any(len(o) > len(vstem) and _sub_matches_video(stem, o) for o in others)
        if other_episode(stem):
            return False
        lone = only_video and ((in_sub_dir and not folder) or is_language_only_name(stem))
        if not (lone or same_episode(stem, in_sub_dir)):
            return False
        return not any(_sub_matches_video(stem, o) for o in others)   # named after another video

    subs = []
    def _add(folder, name, where):
        f = folder / name
        subs.append(f)
        log.debug(f"Found sub ({where}): {f}")

    for n in names:
        stem, ext = _split_name(n)
        if ext in SUB_EXT and belongs(stem, False):
            _add(parent, n, "next to the video")

    for d in dirnames:
        if d.strip().lower() not in SUBTITLE_DIR_NAMES:
            continue
        sub_dir = parent / d
        files, nested = _list_dir(sub_dir)
        for n in files:
            stem, ext = _split_name(n)
            if ext in SUB_EXT and belongs(stem, True):
                _add(sub_dir, n, "Subs folder")
        for nd in nested:
            # Season packs: Subs/<video name>/ or Subs/S01E02/
            if belongs(nd.strip(), True, folder=True):
                for n in _list_dir(sub_dir / nd)[0]:
                    if _split_name(n)[1] in SUB_EXT:
                        _add(sub_dir / nd, n, "per-episode Subs folder")

    log.debug(f"Total subs found for {video.name}: {len(subs)}")
    return subs

# ═══════════════════════════════════════════════════════════════════
#  FORMATTER
# ═══════════════════════════════════════════════════════════════════
ILLEGAL = r'[<>:"/\\|?*\x00-\x1f]'

def sanitize(name):
    return (re.sub(ILLEGAL, "", str(name)).strip(". ") or "Unknown")

def _normalize_rel(rel):
    """Clean a template-produced relative path: drop empty '()' left by a
    missing year, sanitize each path segment (illegal chars, trailing
    dots/spaces Windows can't create), and drop empty segments."""
    rel = re.sub(r"\(\s*\)", "", rel)
    segments = []
    for seg in re.split(r"[/\\]+", rel):
        seg = re.sub(ILLEGAL, "", seg).strip(". ")
        seg = re.sub(r"\s{2,}", " ", seg)
        if seg:
            segments.append(seg)
    return "/".join(segments)

def _to_int(value, default):
    """int(value), or default for None / "" / junk. Keeps 0 (Season 00, episode 0)."""
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

def fmt_tv(show, season, episode, title, ext, template, video_codec="", audio_codec="", audio_channels="", media_source="", resolution="", episode_type="dvd", episode_title=None, year="", episodes=None, air_date=None):
    """episodes: all episode numbers in the file (multi-episode files become
    S01E01-E02, Plex's form). air_date: date-based shows become "2024-05-17"
    inside a "Season 2024" folder, as Plex expects."""
    if isinstance(show, list):
        show = show[0] if show else "Unknown"
    if isinstance(title, list):
        title = title[0] if title else "Episode"
    if episode_title is None:
        episode_title = title or f"Episode {episode}"
    elif isinstance(episode_title, list):
        episode_title = episode_title[0] if episode_title else f"Episode {episode}"

    if isinstance(year, list):
        year = year[0] if year else ""

    show_san = sanitize(show)
    title_san = sanitize(episode_title)

    if isinstance(episode, (list, tuple)):
        episodes, episode = list(episode), (episode[0] if episode else None)
    s = _to_int(season, 1)
    eps = [n for n in (_to_int(x, None) for x in (episodes or [])) if n is not None]
    if not eps:
        eps = [_to_int(episode, 1)]
    e = eps[0]

    s00 = f"{s:02d}"
    e00 = f"{e:02d}"
    if len(eps) > 1:
        s00e00 = f"S{s00}E{e00}-E{eps[-1]:02d}"
        abs00 = f"{e00}-{eps[-1]:02d}"
    else:
        s00e00 = f"S{s00}E{e00}"
        abs00 = e00
    if air_date:
        s00 = str(air_date.year)
        s00e00 = abs00 = air_date.isoformat()

    ctx = {
        "show": show_san,
        "s": str(s),
        "s00": s00,
        "e": str(e),
        "e00": e00,
        "s00e00": s00e00,
        "abs00": abs00,
        "title": title_san,
        "year": year,
        "edition": "",
        "edition_name": "",
        "vc": video_codec,
        "c": audio_codec,
        "ach": audio_channels,
        "res": resolution,
        "ext": ext.lstrip("."),
    }

    log.debug(f"fmt_tv input: show='{show}', season='{season}', episode='{episode}', template='{template}'")

    if not template or not template.strip():
        template = "{show} ({year})/Season {s00}/{show} {s00e00} - {title}"

    if episode_type == "absolute":
        template = template.replace("{s00e00}", "{abs00}")
        template = template.replace("{s00}", "")

    try:
        rel = template.format(**ctx)
    except Exception as ex:
        log.warning(f"Template format failed: {ex}, using fallback")
        rel = f"{show_san}/Season {s00}/{show_san} - {s00e00} - {title_san}"

    rel = _normalize_rel(rel)

    # Append the extension unless the template already produced it ({ext})
    if ext and not rel.endswith(ext):
        rel = rel + ext

    return rel

# Movie editions, written the Plex way: "Aliens (1986) {edition-Special Edition}.mkv".
# Plex then shows both cuts of a movie under one title.
_EDITION_NAMES = {
    "special": "Special Edition", "extended": "Extended Edition", "collector": "Collector's Edition",
    "criterion": "Criterion Collection", "deluxe": "Deluxe Edition", "theatrical": "Theatrical Cut",
    "ultimate": "Ultimate Edition", "fan": "Fan Edit", "imax": "IMAX",
}
_EDITION_SKIP = {"limited", "festival"}          # release types, not cuts of the film
_PLEX_EDITION = re.compile(r"\{edition-([^}]+)\}", re.IGNORECASE)
_EDITION_EXTRA = [   # names guessit doesn't know; only looked for after the year
    (re.compile(r"(?i)(?<![a-z])final[ ._-]cut(?![a-z])"), "Final Cut"),
    (re.compile(r"(?i)(?<![a-z])redux(?![a-z])"), "Redux"),
    (re.compile(r"(?i)(?<![a-z])(?:(\d{1,3}(?:st|nd|rd|th))[ ._-])?anniversary[ ._-]edition(?![a-z])"),
     "Anniversary Edition"),
]
_YEAR_IN_NAME = re.compile(r"(?<!\d)(?:19|20)\d\d(?!\d)")

def detect_edition(name, raw=None):
    """The edition of a movie from its file name ("" if none): a Plex
    {edition-...} tag already in the name, else what guessit found
    ("Director's Cut", "Extended Edition", "IMAX", ...)."""
    m = _PLEX_EDITION.search(name)
    if m:
        return m.group(1).strip()
    found = []
    ed = (raw or {}).get("edition")
    for e in (ed if isinstance(ed, list) else [ed] if ed else []):
        key = str(e).lower()
        if key not in _EDITION_SKIP:
            label = _EDITION_NAMES.get(key, str(e))
            if label not in found:
                found.append(label)
    year = _YEAR_IN_NAME.search(name)
    if year:                                       # "The.Final.Cut.2004" is a title, not an edition
        after = name[year.end():]
        for rx, label in _EDITION_EXTRA:
            m = rx.search(after)
            if m:
                label = f"{m.group(1)} {label}" if m.lastindex and m.group(1) else label
                if label not in found:
                    found.append(label)
    return " ".join(found)

def fmt_movie(title, year, ext, template, video_codec="", audio_codec="", audio_channels="", media_source="", resolution="", edition=""):
    if not template or not template.strip():
        template = DEFAULTS["format_movie"]
    if isinstance(year, list):
        year = year[0] if year else None


    # No year: leave it out ("Title" rather than "Title (Unknown)", which Plex
    # would read as part of the title); _normalize_rel drops the empty "()".
    y = str(year) if year else ""

    ctx = {
        "title": sanitize(title),
        "year": y,
        "edition": f"{{edition-{sanitize(edition)}}}" if edition else "",
        "edition_name": sanitize(edition) if edition else "",
        "vc": video_codec,
        "c": audio_codec,
        "ach": audio_channels,
        "res": resolution,
        "ext": ext.lstrip("."),
    }

    try:
        rel = template.format(**ctx)
    except Exception as e:
        log.warning(f"Template format failed: {e}, using default")
        rel = f"{sanitize(title)} ({y})/{sanitize(title)} ({y})"

    rel = _normalize_rel(rel)

    # Append the extension unless the template already produced it ({ext})
    if not rel.endswith(ext) and ext:
        rel = rel + ext

    return rel

# ═══════════════════════════════════════════════════════════════════
#  RENAMER
# ═══════════════════════════════════════════════════════════════════
@dataclass
class RenameOp:
    src: Path; dst: Path; success: bool = False; error: str = ""
    method: str = "move"      # how the file was placed: move | copy | hardlink | none
    note: str = ""            # e.g. why a hard link became a copy

@dataclass
class RenameResult:
    ops: List[RenameOp] = field(default_factory=list)
    @property
    def succeeded(self): return [o for o in self.ops if o.success]
    @property
    def failed(self): return [o for o in self.ops if not o.success]

def _same_path(a: Path, b: Path) -> bool:
    """Windows-safe path equality (case-insensitive, separator-agnostic)."""
    return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))

def _is_inside(child: Path, root: Path) -> bool:
    """True if child is a strict descendant of root."""
    child_n = os.path.normcase(os.path.normpath(str(child)))
    root_n = os.path.normcase(os.path.normpath(str(root)))
    return child_n != root_n and child_n.startswith(root_n + os.sep)

RENAME_ACTIONS = ("move", "copy", "hardlink")

def _cross_device(e):
    """os.link failed because the two paths are on different drives."""
    return getattr(e, "errno", None) == errno.EXDEV or getattr(e, "winerror", None) == 17

class Renamer:
    """Puts files at their new names — moving, copying or hard-linking them —
    and undoes that. Every batch goes into a RenameHistory; with a history
    file, renames can still be undone after the app was closed."""

    def __init__(self, history=None):
        self.history = history if history is not None else RenameHistory(None)
        self.last_batch_id = None

    @staticmethod
    def _place(src, dst, action="move"):
        op = RenameOp(src=src, dst=dst, method=action)
        try:
            if not src.exists():
                raise FileNotFoundError(f"Source no longer exists: {src}")
            same = _same_path(dst, src)
            if dst.exists() and not same:
                raise FileExistsError(f"Exists: {dst}")
            if same and action != "move":
                op.method, op.success = "none", True      # already there: nothing to copy
                return op
            dst.parent.mkdir(parents=True, exist_ok=True)
            if action == "copy":
                shutil.copy2(str(src), str(dst))
            elif action == "hardlink":
                try:
                    os.link(str(src), str(dst))
                except OSError as e:
                    # Other drive, FAT/exFAT or a share without hard links: copy instead
                    log.info(f"Hard link not possible ({e}); copying {src.name}")
                    shutil.copy2(str(src), str(dst))
                    op.method = "copy"
                    op.note = ("copied: hard links only work on the same drive"
                               if _cross_device(e) else "copied: this drive can't hard-link")
            else:
                shutil.move(str(src), str(dst))
            op.success = True
        except Exception as e:
            op.error = str(e)
        return op

    def execute(self, ops, dest_root=None, action="move", source="manual"):
        """Place each (src, dst) pair on its own; one undo step for all."""
        return self.execute_groups([(op, []) for op in ops], dest_root, action, source)

    def execute_groups(self, groups, dest_root=None, action="move", source="manual"):
        """groups: [((video src, dst), [(subtitle src, dst), ...]), ...].
        A video's subtitles are placed only if the video was, so a failure
        never leaves its subtitles alone in the library. One undo step for all."""
        result, done = RenameResult(), []
        for (src, dst), sub_ops in groups:
            op = self._place(src, dst, action)
            result.ops.append(op)
            if op.success:
                done.append(op)
            for s, d in sub_ops:
                if not op.success:
                    result.ops.append(RenameOp(src=s, dst=d, error="Skipped: its video was not moved"))
                    continue
                sop = self._place(s, d, action)
                result.ops.append(sop)
                if sop.success:
                    done.append(sop)
        self.last_batch_id = None
        if done:
            try:
                self.last_batch_id = self.history.add([(o.src, o.dst, o.method) for o in done],
                                                      dest_root, source)["id"]
            except OSError as e:
                log.warning(f"Could not save the rename history: {e}")
        return result

    def can_undo(self):
        return self.history.last_undoable() is not None

    def undo_last(self):
        batch = self.history.last_undoable()
        return self.undo_batch(batch["id"]) if batch else RenameResult()

    def undo_batch(self, batch_id):
        """Put a batch back: moved files go back where they were; copies and
        hard links are deleted (the originals never moved). A copy is only
        deleted while its original still exists and it wasn't changed."""
        batch = self.history.get(batch_id)
        result = RenameResult()
        if not batch or batch.get("undone"):
            return result
        dest_root = batch.get("dest_root") or None
        for entry in reversed(batch["ops"]):
            src, dst, method = Path(entry["src"]), Path(entry["dst"]), entry.get("method", "move")
            undo = RenameOp(src=dst, dst=src, method=method)
            try:
                if method == "download":          # a subtitle downloaded after the rename
                    if dst.exists():
                        dst.unlink()
                elif method == "none":
                    pass
                elif method == "move":
                    if not dst.exists():
                        raise FileNotFoundError(f"File not found: {dst}")
                    if src.exists() and not _same_path(src, dst):
                        raise FileExistsError(f"Original location occupied: {src}")
                    src.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(dst), str(src))
                else:
                    if not dst.exists():
                        raise FileNotFoundError(f"File not found: {dst}")
                    if not src.exists():
                        raise FileNotFoundError(f"The original is gone, so the copy was kept: {dst}")
                    if method == "copy" and dst.stat().st_size != src.stat().st_size:
                        raise ValueError(f"The copy was changed since, so it was kept: {dst}")
                    dst.unlink()
                undo.success = True
                if dest_root:
                    _cleanup_empty_dirs(dst.parent, stop=Path(dest_root))
            except Exception as e:
                undo.error = str(e)
            result.ops.append(undo)
        if not result.ops or any(o.success for o in result.ops):
            self.history.mark_undone(batch_id)
        return result

def detect_conflicts(ops):
    return [(s, d) for s, d in ops if d.exists() and not _same_path(d, s)]

def _cleanup_empty_dirs(folder: Path, stop: Optional[Path] = None):
    """Remove empty directories walking upward. Never removes `stop` itself,
    and never climbs outside of it when given."""
    try:
        while folder.exists() and not any(folder.iterdir()):
            if stop is not None and not _is_inside(folder, stop):
                break
            parent = folder.parent
            folder.rmdir()
            folder = parent
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════════════
#  API: SHARED HELPERS
# ═══════════════════════════════════════════════════════════════════
def _title_score(query: str, candidate: str) -> float:
    """Fuzzy title similarity resistant to the token-subset trap: pure
    token_set_ratio scores 100 for any candidate that merely CONTAINS all the
    query's words (e.g. 'Dr Strange' vs 'The Strange Case of Dr Jekyll...').
    Blending with token_sort_ratio penalizes all those extra words."""
    q = (query or "").lower()
    c = (candidate or "").lower()
    return 0.4 * fuzz.token_set_ratio(q, c) + 0.6 * fuzz.token_sort_ratio(q, c)

# ═══════════════════════════════════════════════════════════════════
#  API: TMDB
# ═══════════════════════════════════════════════════════════════════
class TMDBClient:
    BASE = "https://api.themoviedb.org/3"
    def __init__(self, key):
        self.key = key
        self.s = requests.Session()
        self.s.params = {"api_key": key, "language": "en-US"}
    def _get(self, ep, **p):
        try:
            r = self.s.get(f"{self.BASE}{ep}", params=p, timeout=10)
            r.raise_for_status(); return r.json()
        except Exception: return None
    def _best(self, query, results, key):
        if not results: return None
        return max(results, key=lambda r: _title_score(query, r.get(key,"")))
    def search_movie(self, title, year=None):
        p = {"query": title}
        if year: p["year"] = year
        d = self._get("/search/movie", **p)
        if not d or not d.get("results"): return None
        b = self._best(title, d["results"], "title")
        if not b: return None
        yr = None
        rd = b.get("release_date") or ""
        if rd[:4].isdigit(): yr = int(rd[:4])
        return {"title": b.get("title", title), "year": yr, "id": b.get("id")}
    def search_movie_all(self, title, year=None):
        p = {"query": title, "include_adult": "false"}
        if year: p["year"] = year
        d = self._get("/search/movie", **p)
        if not d or not d.get("results"): return []
        log.debug(f"TMDB movie search raw results: {d['results'][:2]}")
        results = []
        for r in d["results"][:10]:
            yr = None
            if r.get("release_date"):
                try: yr = int(r["release_date"][:4])
                except (ValueError, TypeError): pass
            results.append({
                "title": r.get("title", title),
                "year": yr,
                "id": r.get("id"),
                "overview": r.get("overview", ""),
                "poster": r.get("poster_path", ""),
                "rating": r.get("vote_average", 0),
            })
        return results
    def search_tv(self, title):
        d = self._get("/search/tv", query=title)
        if not d or not d.get("results"): return None
        b = self._best(title, d["results"], "name")
        return {"show": b.get("name", title), "id": b.get("id")} if b else None
    def search_tv_all(self, title):
        d = self._get("/search/tv", query=title, include_adult=False)
        if not d or not d.get("results"): return []
        log.debug(f"TMDB TV search raw results: {d['results'][:2]}")
        results = []
        for r in d["results"][:10]:
            results.append({
                "show": r.get("name", title),
                "id": r.get("id"),
                "overview": r.get("overview", ""),
                "poster": r.get("poster_path", ""),
                "first_air_date": r.get("first_air_date", ""),
                "rating": r.get("vote_average", 0),
            })
        return results
    def get_tv_details(self, tv_id):
        d = self._get(f"/tv/{tv_id}")
        if not d: return None
        return {
            "title": d.get("name", ""),
            "overview": d.get("overview", ""),
            "poster": d.get("poster_path", ""),
            "first_air_date": d.get("first_air_date", ""),
            "rating": d.get("vote_average", 0),
        }
    def get_movie_details(self, movie_id):
        d = self._get(f"/movie/{movie_id}")
        if not d: return None
        return {
            "title": d.get("title", ""),
            "overview": d.get("overview", ""),
            "poster": d.get("poster_path", ""),
            "release_date": d.get("release_date", ""),
            "rating": d.get("vote_average", 0),
        }
    def get_episode(self, show_id, season, episode):
        d = self._get(f"/tv/{show_id}/season/{season}/episode/{episode}")
        if not d: return None
        return {"title": d.get("name", f"Episode {episode}")}

# ═══════════════════════════════════════════════════════════════════
#  API: CINEMETA
# ═══════════════════════════════════════════════════════════════════
class CinemetaClient:
    """Movie title/year lookup via Cinemeta (Stremio's public catalog) — no
    key required. Used when TMDB is not configured or returns nothing."""
    BASE = "https://v3-cinemeta.strem.io"
    def __init__(self):
        self.s = requests.Session()
    def _get(self, ep):
        try:
            r = self.s.get(f"{self.BASE}{ep}", timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception:
            return None
    @staticmethod
    def _year_of(meta):
        info = str(meta.get("releaseInfo") or "")
        return int(info[:4]) if info[:4].isdigit() else None
    @staticmethod
    def _rating_of(meta):
        try:
            return float(meta.get("imdbRating") or 0)
        except (TypeError, ValueError):
            return 0
    def _search(self, title):
        from urllib.parse import quote
        d = self._get(f"/catalog/movie/top/search={quote(str(title))}.json")
        return (d or {}).get("metas") or []
    def search_movie(self, title, year=None):
        metas = self._search(title)
        if not metas: return None
        scored = []
        for rank, m in enumerate(metas):
            score = _title_score(str(title), m.get("name") or "")
            if year and self._year_of(m) == year:
                score += 10
            scored.append((score, rank, m))
        best_score = max(s for s, _, _ in scored)
        if best_score < 70:
            return None
        # Among the plausible matches (score >= 70 and within 20 points of the
        # best), keep Cinemeta's own ordering, which is by popularity.

        viable = [t for t in scored if t[0] >= 70 and t[0] >= best_score - 20]
        viable.sort(key=lambda t: t[1])
        best = viable[0][2]
        return {"title": best.get("name", title), "year": self._year_of(best), "id": best.get("id")}
    def search_movie_all(self, title, year=None):
        metas = self._search(title)
        out = []
        for m in metas[:10]:
            out.append({
                "title": m.get("name", title),
                "year": self._year_of(m),
                "id": m.get("id"),
                "overview": m.get("description") or "",
                "poster": m.get("poster") or "",
                "rating": self._rating_of(m),
            })
        return out
    def get_movie_details(self, imdb_id):
        d = self._get(f"/meta/movie/{imdb_id}.json")
        meta = (d or {}).get("meta")
        if not meta: return None
        yr = self._year_of(meta)
        return {"title": meta.get("name", ""), "release_date": str(yr) if yr else ""}

# ═══════════════════════════════════════════════════════════════════
#  API: TVMAZE
# ═══════════════════════════════════════════════════════════════════
class TVMazeClient:
    def __init__(self): self.s = requests.Session()
    def _get(self, ep, **p):
        try:
            r = self.s.get(f"https://api.tvmaze.com{ep}", params=p, timeout=10)
            if r.status_code == 404: return None
            r.raise_for_status(); return r.json()
        except Exception: return None
    def search_show(self, title):
        d = self._get("/search/shows", q=title)
        if not d: return None
        best = max(d, key=lambda x: _title_score(title, x["show"]["name"]))
        s = best["show"]
        premiered = s.get("premiered", "")
        year = premiered[:4] if premiered else ""
        return {"show": s["name"], "id": s["id"], "year": year}
    def search_show_all(self, title):
        d = self._get("/search/shows", q=title)
        if not d: return []
        log.debug(f"TVMaze raw results: {d[:2]}")
        results = []
        for item in d[:10]:
            s = item.get("show", {})
            premiered = s.get("premiered") or ""
            year = premiered[:4] if premiered else ""
            results.append({
                "show": s.get("name", title),
                "id": s.get("id"),
                "overview": s.get("summary") or "",
                "poster": (s.get("image") or {}).get("medium", ""),
                "first_air_date": premiered,
                "rating": (s.get("rating") or {}).get("average") or 0,
                "year": year,
            })
        return results
    def get_episode(self, show_id, season, episode):
        d = self._get(f"/shows/{show_id}/episodebynumber", season=season, number=episode)
        return {"title": d.get("name", f"Episode {episode}")} if d else None

    def get_episode_by_date(self, show_id, air_date):
        d = self._get(f"/shows/{show_id}/episodesbydate", date=str(air_date))
        if isinstance(d, list) and d:
            return {"title": d[0].get("name") or str(air_date)}
        return None

    def get_show_info(self, show_id: int):
        data = self._get(f"/shows/{show_id}")
        if not data:
            return None
        premiered = data.get("premiered", "")
        year = premiered[:4] if premiered else ""
        return {
            "show": data.get("name", ""),
            "id": show_id,
            "year": year,
        }

    def get_show_by_tvdb_id(self, tvdb_id):
        d = self._get("/lookup/shows", thetvdb=tvdb_id)
        if not d:
            log.debug(f"TVMaze: No show found for TVDB ID {tvdb_id}")
            return None
        return {
            "show": d.get("name"),
            "id": d.get("id"),
            "overview": d.get("summary", ""),
            "poster": d.get("image", {}).get("medium", "") if d.get("image") else "",
            "first_air_date": d.get("premiered", ""),
            "rating": d.get("rating", {}).get("average", 0) if d.get("rating") else 0,
        }

# ═══════════════════════════════════════════════════════════════════
#  API: ANIDB
# ═══════════════════════════════════════════════════════════════════
CACHE_DIR = _APP_DIR / "cache"
TITLES_CACHE = CACHE_DIR / "anidb_titles.xml.gz"

class AniDBClient:
    def __init__(self): self._titles = None
    def _ensure(self):
        if self._titles is not None: return
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        age = (time.time() - TITLES_CACHE.stat().st_mtime) if TITLES_CACHE.exists() else 999999
        if age > 86400 * 7:
            try:
                r = requests.get("https://anidb.net/api/anime-titles.xml.gz",
                                 headers={"User-Agent": f"Mozilla/5.0 PathRenamer/{APP_VERSION}"}, timeout=15)
                r.raise_for_status(); TITLES_CACHE.write_bytes(r.content)
            except Exception: pass
        self._titles = []
        if not TITLES_CACHE.exists(): return
        try:
            with gzip.open(TITLES_CACHE, "rb") as f: root = ET.parse(f).getroot()
            for anime in root.findall("anime"):
                try: aid = int(anime.get("aid","0"))
                except: continue
                for t in anime.findall("title"):
                    lang = t.get("{http://www.w3.org/XML/1998/namespace}lang","")
                    if (lang == "en" or t.get("type","") == "main") and t.text:
                        self._titles.append((aid, t.text.strip()))
        except Exception: pass
    def search_anime(self, title):
        self._ensure()
        if not self._titles: return None
        best = max(self._titles, key=lambda x: fuzz.token_set_ratio(title.lower(), x[1].lower()))
        score = fuzz.token_set_ratio(title.lower(), best[1].lower())
        return {"show": best[1], "id": best[0]} if score >= 60 else None
    def search_anime_all(self, title):
        self._ensure()
        if not self._titles: return []
        matches = []
        for aid, name in self._titles:
            score = fuzz.token_set_ratio(title.lower(), name.lower())
            if score >= 40:
                matches.append({"show": name, "id": aid, "score": score})
        matches.sort(key=lambda x: x["score"], reverse=True)
        return matches[:10]
    def get_episode(self, anime_id, season, episode):
        try:
            r = requests.get("http://api.anidb.net:9001/httpapi",
                params={"request":"anime","client":"pathrenamer","clientver":1,"protover":1,"aid":anime_id},
                headers={"User-Agent":f"Mozilla/5.0 PathRenamer/{APP_VERSION}"}, timeout=15)
            root = ET.fromstring(r.text)
            eps = root.find("episodes")
            if eps is None: return None
            XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
            for ep in eps.findall("episode"):
                epno = ep.find("epno")
                if epno is None or epno.get("type","1") != "1": continue
                try:
                    if int(epno.text) == episode:
                        title = None
                        for t in ep.findall("title"):
                            if t.get(XML_LANG) == "en":
                                title = t.text
                                break
                        if title is None:
                            t = ep.find("title")
                            title = t.text if t is not None else None
                        return {"title": title or f"Episode {episode}"}
                except (ValueError, TypeError): continue
        except Exception: pass
        return None

# ═══════════════════════════════════════════════════════════════════
#  MATCH ENGINE
# ═══════════════════════════════════════════════════════════════════
@dataclass(eq=False)
class Candidate:
    source: Path
    dest_rel: str
    media_type: str
    confidence: float
    conflict: bool = False
    error: str = ""
    subtitles: List[Tuple[Path, str]] = field(default_factory=list)
    video_codec: str = ""
    audio_codec: str = ""
    audio_channels: str = ""
    media_source: str = ""
    resolution: str = ""
    # What the name was built from, so one row can be fixed by hand
    title: str = ""                       # show or movie title used
    year: str = ""                        # its year, when known
    season: Optional[int] = None
    episodes: List[int] = field(default_factory=list)
    manual: bool = False                  # new name typed by the user

def derive_title_for(path: Path, media_type: str = "tv") -> str:
    """Best-guess show/movie title for a file.

    The file name wins when it carries the show/movie name itself; the folder
    (or the show folder above a "Season N" folder) is used when the file name
    has no title, only an episode title or a scene short name, or when both
    agree. See parser.analyse_path."""
    return analyse_path(path, media_type).title or "Unknown"


class MatchEngine:
    def __init__(self, cfg, tvmaze=None, anidb=None, tmdb=None, cinemeta=None):
        self.cfg = cfg; self.tvmaze = tvmaze; self.anidb = anidb
        self.tmdb = tmdb; self.cinemeta = cinemeta
    def match(self, path, media_type):
        return self.match_with_override(path, media_type)

    def _transform_subtitle_dest(self, cand, is_subtitle, output_counts=None):
        """For a subtitle file matched on its own (no video picked), turn the
        video-style destination into a Plex subtitle name next to where the
        video goes: "Show S01E01 - Title.eng.forced.srt".

        output_counts: {destination folder: set of names used}, shared across
        a scan so two subtitles never get the same name.
        """
        if not is_subtitle or cand.error:
            return cand
        src = cand.source
        lang, flags = detect_subtitle_language(src)
        dest_path = Path(cand.dest_rel)          # built as if the subtitle were the video
        taken = set() if output_counts is None else output_counts.setdefault(
            str(dest_path.parent).lower(), set())
        name = unique_subtitle_filename(dest_path.stem, lang, src.suffix.lower(), flags, taken)
        cand.dest_rel = str(dest_path.parent / name).replace("\\", "/")
        return cand

    def search_all_matches(self, path, media_type):
        try:
            guess = analyse_path(path, media_type)
            return self.search_title(guess.title or "Unknown", media_type, guess.year)
        except Exception as e:
            log.exception(f"search_all_matches failed for {path.name}: {e}")
            return []

    def search_title(self, title, media_type, year=None):
        """Shows or movies matching a title, for the match chooser."""
        try:
            matches = []
            if media_type == "movie":
                if self.tmdb:
                    matches = self.tmdb.search_movie_all(title, year)
                    for m in matches:
                        m["src"] = "tmdb"
                    log.debug(f"Using TMDB for movie search: {title}")
                if not matches and self.cinemeta:
                    matches = self.cinemeta.search_movie_all(title, year)
                    for m in matches:
                        m["src"] = "cinemeta"
                    log.debug(f"Using Cinemeta for movie search: {title}")
            elif media_type == "anime":
                if self.tvmaze:
                    matches = self.tvmaze.search_show_all(title)
                    for m in matches:
                        m["src"] = "tvmaze"
                    log.debug(f"Using TVMaze for anime search: {title}")
                if not matches and self.anidb:
                    matches = self.anidb.search_anime_all(title)
                    for m in matches:
                        m["src"] = "anidb"
                    log.debug(f"Using AniDB for anime search: {title}")
            else:
                if self.tvmaze:
                    matches = self.tvmaze.search_show_all(title)
                    for m in matches:
                        m["src"] = "tvmaze"
                    log.debug(f"Using TVMaze for TV show search: {title}")
            return matches
        except Exception as e:
            log.exception(f"search_title failed for {title!r}: {e}")
            return []

    @staticmethod
    def _scalar(raw, key, default=""):
        """guessit may return a scalar or a list; always return a scalar."""
        v = raw.get(key, default)
        if isinstance(v, list):
            v = v[0] if v else default
        return v

    def match_with_override(self, path, media_type, selected_title=None, selected_id=None,
                            season=None, episodes=None):
        """season / episodes: numbers chosen by the user for this file (Change
        Episode, Number in Order); they replace what the name says."""
        try:
            ext = path.suffix.lower()
            # Title, year, season, episode(s) / air date from the file name and
            # its folders. The file's own SxxEyy always wins; a folder's only
            # fills in what the file name lacks (e.g. obfuscated file names).
            guess = analyse_path(path, media_type)
            if episodes:
                guess.episodes = [int(e) for e in episodes]
                guess.date = None
                if season is not None:
                    guess.season = int(season)
            raw = guess.raw
            title = guess.title
            tvmaze_log.debug(f"TITLE_DETECT: file='{path.name}' -> '{title}' (from {guess.title_source or 'nothing'})")
            video_codec = self._scalar(raw, "video_codec")
            audio_codec = self._scalar(raw, "audio_codec")
            audio_channels = self._scalar(raw, "audio_channels")
            media_source = self._scalar(raw, "source")
            resolution = self._scalar(raw, "screen_size")
            if (ext in VIDEO_EXT and self.cfg.get("read_media_info", True)
                    and not (video_codec and audio_codec and audio_channels and resolution)):
                # What the name doesn't say is read from the file itself
                info = read_media_info(path)
                if info:
                    resolution = resolution or info.resolution
                    video_codec = video_codec or info.video_codec
                    if not audio_codec or audio_codec == info.audio_codec:
                        # channels only when they belong to the codec the name gives
                        audio_codec = audio_codec or info.audio_codec
                        audio_channels = audio_channels or info.audio_channels

            if selected_title:
                title = selected_title
            if not title:
                return Candidate(path, path.name, media_type, 0.0,
                                 error="No title found in the file or folder name")

            if media_type == "movie":
                return self._movie_with_title(path, title, guess.year, ext, video_codec, audio_codec, audio_channels, media_source, resolution, selected_id,
                                              edition=detect_edition(path.stem, raw))

            if not guess.episodes and not guess.date:
                # Better to skip than to invent S01E01 (every such file would collide)
                return Candidate(path, path.name, media_type, 0.0,
                                 error="No season/episode number in the name")
            season = guess.season if guess.season is not None else 1
            episodes = guess.episodes
            episode = episodes[0] if episodes else None
            log.info(f"Parsed {path.name}: title='{title}' S{season} E{episodes} date={guess.date} year={guess.year}")
            extra = dict(year=guess.year, episodes=episodes, air_date=guess.date,
                         fallback_title=guess.episode_title if guess.date else "")
            if media_type == "anime":
                return self._anime_with_title(path, title, season, episode, ext, video_codec, audio_codec, audio_channels, media_source, resolution, selected_id, **extra)
            else:
                return self._tv_with_title(path, title, season, episode, ext, video_codec, audio_codec, audio_channels, media_source, resolution, selected_id, **extra)
        except Exception as e:
            log.exception(f"match_with_override failed for {path.name}: {e}")
            return Candidate(path, path.name, media_type, 0.0, error=str(e))

    def _episode_titles(self, client, show_id, season, episodes, air_date):
        """Episode title(s) from TVMaze / AniDB. Multi-episode files get the
        distinct titles joined ("Part 1 & Part 2"); date-based shows look the
        episode up by air date."""
        if air_date:
            getter = getattr(client, "get_episode_by_date", None)
            e = getter(show_id, air_date) if getter else None
            return e.get("title") if e else None
        titles = []
        for n in episodes[:3]:
            e = client.get_episode(show_id, season, n)
            t = e.get("title") if e else None
            if t and t not in titles:
                titles.append(t)
        return " & ".join(titles) if titles else None

    @staticmethod
    def _split_selected_id(selected_id):
        """Selected ids carry their source database as ("tvmaze"|"anidb"|"tmdb", id).
        Bare ids (older callers) are assumed to be TVMaze."""
        if not selected_id:
            return None, None
        if isinstance(selected_id, (tuple, list)) and len(selected_id) == 2:
            return selected_id[0], selected_id[1]
        return "tvmaze", selected_id

    def _tv_with_title(self, path, title, season, episode, ext, video_codec="", audio_codec="", audio_channels="", media_source="", resolution="", selected_id=None, year=None, episodes=None, air_date=None, fallback_title=""):
        episodes = list(episodes) if episodes else ([episode] if episode is not None else [])
        default_title = fallback_title or (air_date.isoformat() if air_date else f"Episode {episode}")
        show, ep_title, conf = title, default_title, 0.95
        show_year = ""
        id_src, sid = self._split_selected_id(selected_id)

        # A show picked in the match selector is used by id; otherwise search
        # TVMaze by the detected title.
        if sid and id_src == "tvmaze" and self.tvmaze:
            # Real show name + premiere year for the folder name
            show_info = self.tvmaze.get_show_info(sid)
            if show_info:
                show = show_info.get("show", title)
                show_year = show_info.get("year", "")
                log.debug(f"TVMaze show info: {show}, year: '{show_year}'")
            t = self._episode_titles(self.tvmaze, sid, season, episodes, air_date)
            if t:
                ep_title = t
                log.debug(f"TVMaze episode title: {ep_title}")
        elif self.tvmaze:
            # Automatic match
            tvmaze_log.debug(f"TVMAZE_SEARCH: Searching for title='{title}'")
            s = self.tvmaze.search_show(title)
            if s:
                show = s["show"]
                show_year = s.get("year", "")
                tvmaze_log.debug(f"TVMAZE_FOUND: show='{show}', year='{show_year}', id={s['id']}")
                log.debug(f"TVMaze found: {show}, year: '{show_year}'")
                t = self._episode_titles(self.tvmaze, s["id"], season, episodes, air_date)
                if t:
                    ep_title = t
                    conf = 0.95
                    log.debug(f"TVMaze episode: {ep_title}")
            else:
                tvmaze_log.debug(f"TVMAZE_NOT_FOUND: No results for title='{title}'")
                log.debug(f"TVMaze search returned nothing for: {title}")
                conf = 0.5
        else:
            conf = 0.5

        if not show_year and year:
            show_year = str(year)       # year from the file/folder name, e.g. "Doctor Who (2005)"
        ep_type = self.cfg.get("episode_tv", "dvd")
        rel = fmt_tv(show, season, episode, ep_title, ext, self.cfg.get("format_tv",""), video_codec, audio_codec, audio_channels, media_source, resolution, ep_type, ep_title, show_year, episodes=episodes, air_date=air_date)
        c = Candidate(path, rel, "tv", conf, video_codec=video_codec, audio_codec=audio_codec, audio_channels=audio_channels, media_source=media_source, resolution=resolution,
                      title=show, season=season, episodes=list(episodes), year=str(show_year or ""))
        if ext not in SUB_EXT:
            self._subs(path, rel, c)
        return c

    def _movie_with_title(self, path, title, year, ext, video_codec="", audio_codec="", audio_channels="", media_source="", resolution="", selected_id=None, edition=""):
        conf = 0.9 if year else 0.6
        id_src, sid = self._split_selected_id(selected_id)
        matched = False
        if sid and id_src == "tmdb" and self.tmdb:
            d = self.tmdb.get_movie_details(sid)
            if d:
                title = d.get("title") or title
                rd = d.get("release_date") or ""
                if rd[:4].isdigit():
                    year = int(rd[:4])
                conf, matched = 0.95, True
        elif sid and id_src == "cinemeta" and self.cinemeta:
            d = self.cinemeta.get_movie_details(sid)
            if d:
                title = d.get("title") or title
                rd = d.get("release_date") or ""
                if rd[:4].isdigit():
                    year = int(rd[:4])
                conf, matched = 0.9, True
        if not matched and self.tmdb:
            m = self.tmdb.search_movie(title, year)
            if m:
                title = m.get("title") or title
                year = m.get("year") or year
                conf, matched = 0.95, True
        if not matched and self.cinemeta:
            m = self.cinemeta.search_movie(title, year)
            if m:
                title = m.get("title") or title
                year = m.get("year") or year
                conf, matched = 0.9, True
        rel = fmt_movie(title, year, ext, self.cfg.get("format_movie",""), video_codec, audio_codec, audio_channels, media_source, resolution, edition)
        c = Candidate(path, rel, "movie", conf, video_codec=video_codec, audio_codec=audio_codec, audio_channels=audio_channels, media_source=media_source, resolution=resolution,
                      title=title, year=str(year or ""))
        if ext not in SUB_EXT:
            self._subs(path, rel, c)
        return c

    def _anime_with_title(self, path, title, season, episode, ext, video_codec="", audio_codec="", audio_channels="", media_source="", resolution="", selected_id=None, year=None, episodes=None, air_date=None, fallback_title=""):
        episodes = list(episodes) if episodes else ([episode] if episode is not None else [])
        default_title = fallback_title or (air_date.isoformat() if air_date else f"Episode {episode}")
        show, ep_title, conf = title, default_title, 0.90
        show_year = ""
        id_src, sid = self._split_selected_id(selected_id)

        if sid and id_src == "tvmaze" and self.tvmaze:
            show_info = self.tvmaze.get_show_info(sid)
            if show_info:
                show = show_info.get("show", title)
                show_year = show_info.get("year", "")
                log.debug(f"TVMaze anime show info: {show}, year: '{show_year}'")
            t = self._episode_titles(self.tvmaze, sid, season, episodes, air_date)
            if t:
                ep_title = t
                conf = 0.95
                log.debug(f"TVMaze anime episode: {ep_title}")
        elif sid and id_src == "anidb" and self.anidb:
            t = self._episode_titles(self.anidb, sid, season, episodes, air_date)
            if t:
                ep_title = t
                conf = 0.95
        elif not sid:
            # Automatic match: TVMaze first, AniDB as fallback
            conf = 0.5
            if self.tvmaze:
                s = self.tvmaze.search_show(title)
                if s:
                    show = s["show"]
                    show_year = s.get("year", "")
                    t = self._episode_titles(self.tvmaze, s["id"], season, episodes, air_date)
                    if t:
                        ep_title = t
                        conf = 0.90
            if conf < 0.9 and self.anidb:
                s = self.anidb.search_anime(title)
                if s:
                    show, conf = s["show"], 0.90
                    t = self._episode_titles(self.anidb, s["id"], season, episodes, air_date)
                    if t:
                        ep_title = t

        if not show_year and year:
            show_year = str(year)
        ep_type = self.cfg.get("episode_anime", "dvd")
        rel = fmt_tv(show, season, episode, ep_title, ext, self.cfg.get("format_anime", self.cfg.get("format_tv","")), video_codec, audio_codec, audio_channels, media_source, resolution, ep_type, ep_title, show_year, episodes=episodes, air_date=air_date)
        c = Candidate(path, rel, "anime", conf, video_codec=video_codec, audio_codec=audio_codec, audio_channels=audio_channels, media_source=media_source, resolution=resolution,
                      title=show, season=season, episodes=list(episodes), year=str(show_year or ""))
        if ext not in SUB_EXT:
            self._subs(path, rel, c)
        return c

    def _subs(self, video, video_rel, cand):
        """Add the video's subtitles to cand, each with its Plex name next to
        the renamed video: "Movie (2010).eng.srt", "Movie (2010).spa.forced.srt".
        Two subtitles with the same language and flags are numbered
        ("Movie (2010).2.eng.srt") so neither overwrites the other."""
        if not self.cfg.get("rename_subtitles", True):
            return
        dest = Path(video_rel)
        taken = set()
        for sub in find_subtitle_siblings(video):
            if _same_path(sub, video):
                continue
            lang, flags = detect_subtitle_language(sub, video.stem)
            name = unique_subtitle_filename(dest.stem, lang, sub.suffix.lower(), flags, taken)
            cand.subtitles.append((sub, str(dest.parent / name).replace("\\", "/")))


# ═══════════════════════════════════════════════════════════════════
#  MATCHING A LIST OF FILES (used by the window's worker and the command line)
# ═══════════════════════════════════════════════════════════════════
DUPLICATE_PREFIX = "Same new name as "

def mark_duplicates(cands, dest_root):
    """Flag every candidate whose new path an earlier one already takes
    (e.g. a 720p and a 1080p copy of the same episode); the first keeps it."""
    for c in cands:
        if c.error.startswith(DUPLICATE_PREFIX):
            c.error = ""
    owners = {}
    for c in cands:
        if c.error:
            continue
        key = os.path.normcase(os.path.normpath(str(Path(dest_root or ".") / c.dest_rel)))
        owner = owners.setdefault(key, c)
        if owner is not c:
            c.error = f"{DUPLICATE_PREFIX}{owner.source.name}"


def match_files(files, engine, media_type, dest_root, selected_overrides=None, episode_overrides=None):
    """Match files one by one and yield a Candidate for each (a subtitle that
    goes with a matched video is not yielded on its own). Conflicts with
    existing files and two files taking the same new name are flagged."""
    selected_overrides = selected_overrides or {}       # str(path) -> (title, id)
    episode_overrides = episode_overrides or {}         # str(path) -> (season, [episodes])
    sub_output_counts, dest_owner = {}, {}
    log.info(f"Scan started: {len(files)} file(s), type={media_type}, dest={dest_root}")
    # Videos first: their subtitles are renamed with them, so a subtitle
    # file that was also added to the list on its own is not done twice.
    ordered = ([f for f in files if f.suffix.lower() not in SUB_EXT] +
               [f for f in files if f.suffix.lower() in SUB_EXT])
    claimed = set()      # subtitles already renamed together with a video
    for f in ordered:
        try:
            log.debug(f"Matching: {f.name}")
            if f.suffix.lower() in SUB_EXT and os.path.normcase(str(f)) in claimed:
                log.debug("  -> renamed with its video")
                continue
            if not f.exists():
                yield Candidate(f, f.name, media_type, 0.0,
                                error="Source file no longer exists (already renamed or removed)")
                continue
            key = str(f)
            selected_title, selected_id = selected_overrides.get(key, (None, None))
            season, episodes = episode_overrides.get(key, (None, None))
            cand = engine.match_with_override(f, media_type, selected_title, selected_id,
                                              season=season, episodes=episodes)
            # Subtitle files picked directly: give them Plex subtitle names
            if f.suffix.lower() in SUB_EXT:
                cand = engine._transform_subtitle_dest(cand, True, sub_output_counts)
            log.debug(f"  -> dest_rel={cand.dest_rel}  conf={cand.confidence:.2f}  err={cand.error!r}")
            dest = Path(dest_root) / cand.dest_rel
            if dest.exists() and not _same_path(dest, f):
                cand.conflict = True
                log.warning(f"  CONFLICT: {dest}")
            if not cand.error:
                # e.g. a 720p and a 1080p copy of the same episode: only the
                # first one can take the name, so flag the others now
                dkey = os.path.normcase(os.path.normpath(str(dest)))
                owner = dest_owner.setdefault(dkey, f)
                if not _same_path(owner, f):
                    cand.error = f"{DUPLICATE_PREFIX}{owner.name}"
                    log.warning(f"  DUPLICATE: {f.name} and {owner.name} -> {dest}")
                else:
                    claimed.update(os.path.normcase(str(s)) for s, _ in cand.subtitles)
            yield cand
        except Exception as e:
            log.exception(f"Error matching {f.name}: {e}")
            yield Candidate(f, f.name, media_type, 0.0, error=str(e))
    log.info("Scan finished.")


def rename_groups(cands, dst):
    """(video op, [subtitle ops]) per renameable candidate, so subtitles follow their video."""
    groups = []
    for cand in cands:
        if cand.error:
            continue
        groups.append(((cand.source, Path(dst) / cand.dest_rel),
                       [(s, Path(dst) / rel) for s, rel in cand.subtitles]))
    return groups


ACTION_WORDS = {  # rename action -> (verb, past tense)
    "move": ("Move", "Renamed"), "copy": ("Copy", "Copied"), "hardlink": ("Hard-link", "Hard-linked"),
}


def place_groups(renamer, groups, dst, action="move", source="manual", cleanup_root=None, cleanup=True):
    """Rename (move / copy / hard-link) the groups, then remove folders left
    empty under cleanup_root. A subtitle whose new name already exists stays
    where it is. Returns (result, a one-line summary for people)."""
    sub_skipped = sum(1 for _, sub_ops in groups for s in sub_ops if detect_conflicts([s]))
    groups = [(main_op, [s for s in sub_ops if not detect_conflicts([s])]) for main_op, sub_ops in groups]
    if action not in RENAME_ACTIONS:
        action = "move"
    result = renamer.execute_groups(groups, dest_root=dst, action=action, source=source)
    ok_subs = sum(1 for op in result.succeeded if op.src.suffix.lower() in SUB_EXT)
    ok_files = len(result.succeeded) - ok_subs
    fallbacks = sum(1 for op in result.succeeded if op.note)
    # Remove folders left empty in the source tree (only moving empties them)
    if action == "move" and cleanup and cleanup_root and os.path.isdir(str(cleanup_root)):
        for op in result.succeeded:
            if _is_inside(op.src, Path(cleanup_root)):
                _cleanup_empty_dirs(op.src.parent, stop=Path(cleanup_root))
    msg = f"{ACTION_WORDS[action][1]} {ok_files} file(s)"
    if ok_subs:
        msg += f" and {ok_subs} subtitle(s)"
    if result.failed:
        msg += f", {len(result.failed)} failed"
    if fallbacks:
        msg += f" · {fallbacks} copied instead (hard links only work on the same drive)"
    if sub_skipped:
        msg += f" · {sub_skipped} subtitle(s) left in place (new name already exists)"
    return result, msg


def detect_media_type(path, chosen="auto"):
    """TV or movie by the name, unless a type was chosen. "Dune Part Two
    2024" has a part number and a year: a movie."""
    if chosen in ("tv", "movie", "anime"):
        return chosen
    guess = analyse_path(Path(path), "tv")
    year = guess.year or (guess.raw or {}).get("year")
    episodic = guess.season is not None or guess.date or (guess.episodes and not year)
    return "tv" if episodic else "movie"


def make_engine(cfg, anidb=None):
    """A MatchEngine with its own connections to the metadata sources."""
    key = (cfg.get("tmdb_api_key") or "").strip()
    return MatchEngine(cfg=dict(cfg),
                       tvmaze=TVMazeClient() if cfg.get("use_tvmaze", True) else None,
                       anidb=(anidb or AniDBClient()) if cfg.get("use_anidb", True) else None,
                       tmdb=TMDBClient(key) if key else None,
                       cinemeta=CinemetaClient() if cfg.get("use_cinemeta", True) else None)
