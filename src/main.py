import sys, os, json, re, shutil, gzip, time, xml.etree.ElementTree as ET, logging, traceback, html, functools
import dataclasses
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple, Any

APP_VERSION = "1.4.0"

# ── Logging / crash handler ───────────────────────────────────────────────────
_APPDATA_BASE = Path(os.getenv("APPDATA", Path.home()))
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

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger("PathRenamer")
tvmaze_log = logging.getLogger("TVMaze")
tvmaze_log.setLevel(logging.DEBUG)
tvmaze_handler = logging.FileHandler(_LOG_DIR / "tvmaze_debug.log", encoding="utf-8")
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
import requests
import re
from parser import analyse_path
from history import RenameHistory, describe as describe_batch
from scanner import (SUBTITLE_EXTENSIONS, detect_subtitle_language, is_language_only_name,
                     unique_subtitle_filename)
from rapidfuzz import fuzz
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QComboBox, QFileDialog, QStatusBar,
    QMessageBox, QProgressBar, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QDialog, QFormLayout, QCheckBox, QDialogButtonBox,
    QGroupBox, QTabWidget, QListWidget, QListWidgetItem, QScrollArea,
    QGridLayout, QFrame, QScrollBar, QSizePolicy, QTextEdit,
    QMenu, QInputDialog, QStyledItemDelegate, QSplitter, QStackedWidget, QButtonGroup,
)
from PyQt6.QtGui import (QColor, QAction, QActionGroup, QDesktopServices, QDragEnterEvent,
                         QDropEvent, QKeySequence, QShortcut, QPainter, QPalette)
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PyQt6.QtCore import (Qt, QThread, pyqtSignal, QObject, QUrl, QTimer, QVariantAnimation,
                          QEasingCurve, QByteArray, QEvent)

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
    "format_movie": "{title} ({year})/{title} ({year}) {edition} {res} {vc} {ach} {c}",
    "format_anime": "{show} ({year})/Season {s00}/{show} {s00e00} - {title}",
    "episode_tv": "dvd",
    "episode_anime": "dvd",
    "rename_subtitles": True,
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
    "presets": {},                 # name -> saved settings (PRESET_KEYS)
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
                      title=show, season=season, episodes=list(episodes))
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
                      title=title)
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
                      title=show, season=season, episodes=list(episodes))
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
#  THEMES  (palettes and stylesheets live in themes.py)
# ═══════════════════════════════════════════════════════════════════
from themes import (THEMES, THEME_NAMES, BASE_THEMES, ROLES, palette_for, custom_palette,
                    is_color, app_stylesheet, base_stylesheet, tab_stylesheet)

# ═══════════════════════════════════════════════════════════════════
#  SETTINGS DIALOG
# ═══════════════════════════════════════════════════════════════════
class SettingsDialog(QDialog):
    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = dict(cfg)
        self.setWindowTitle("Settings"); self.setMinimumWidth(600)
        self.setMinimumHeight(500)
        L = QVBoxLayout(self)

        theme = self.cfg.get("theme", "dark")
        pages = [
            ("Folders", self._build_folders_tab(theme)),
            ("Formats", self._build_formats_tab(theme)),
            ("Appearance", self._build_appearance_tab(theme)),
            ("Options", self._build_options_tab(theme)),
            ("API Keys", self._build_api_tab(theme)),
            ("About", self._build_about_tab(theme)),
        ]
        self.nav = None
        if self.cfg.get("layout", "modern") == "modern":
            # Sidebar navigation (styled by the #settingsModern rules)
            self.setObjectName("settingsModern")
            self.setMinimumWidth(720)
            L.setContentsMargins(0, 0, 0, 12)
            body = QHBoxLayout(); body.setSpacing(0)
            self.nav = QListWidget(); self.nav.setObjectName("settingsNav")
            self.nav.setFixedWidth(170)
            self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            stack = QStackedWidget()
            for name, page in pages:
                self.nav.addItem(name)
                page.layout().setContentsMargins(18, 14, 18, 8)
                stack.addWidget(page)
            self.nav.currentRowChanged.connect(stack.setCurrentIndex)
            self.nav.setCurrentRow(0)
            body.addWidget(self.nav); body.addWidget(stack, 1)
            L.addLayout(body, 1)
        else:
            tabs = QTabWidget()
            tabs.setStyleSheet(tab_stylesheet(palette_for(self.cfg)))
            for name, page in pages:
                tabs.addTab(page, name)
            L.addWidget(tabs)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._save); btns.rejected.connect(self.reject)
        if self.nav is not None:
            row = QHBoxLayout(); row.setContentsMargins(0, 0, 14, 0); row.addWidget(btns)
            L.addLayout(row)
        else:
            L.addWidget(btns)

    def _get_tab_style(self, theme):
        return tab_stylesheet(palette_for(self.cfg))

    def _edit_custom_theme(self):
        main_window = self.parent()
        preview = getattr(main_window, "_apply_theme", None)
        dlg = CustomThemeDialog(self.cfg, self, preview=preview)
        if dlg.exec():
            self.cfg["custom_theme"] = dlg.result_theme()
            self.theme_combo.setCurrentIndex([k for k, _ in THEME_NAMES].index("custom"))
        if preview:
            preview(palette_for(main_window.cfg))   # the window keeps its theme until OK

    def _build_folders_tab(self, theme):
        w = QWidget()
        L = QVBoxLayout(w)
        L.setContentsMargins(10, 10, 10, 10)

        grp = QGroupBox("Library Root Folders")
        gl = QFormLayout(grp)

        self.root_tv = QLineEdit(self.cfg.get("root_tv", ""))
        self.root_tv.setPlaceholderText("e.g., D:/Plex/TV Shows")
        btn_tv = QPushButton("Browse")
        btn_tv.clicked.connect(lambda: self._browse_folder(self.root_tv))
        gl.addRow("TV Shows:", self._make_browse_row(self.root_tv, btn_tv))

        self.root_movie = QLineEdit(self.cfg.get("root_movie", ""))
        self.root_movie.setPlaceholderText("e.g., D:/Plex/Movies")
        btn_movie = QPushButton("Browse")
        btn_movie.clicked.connect(lambda: self._browse_folder(self.root_movie))
        gl.addRow("Movies:", self._make_browse_row(self.root_movie, btn_movie))

        self.root_anime = QLineEdit(self.cfg.get("root_anime", ""))
        self.root_anime.setPlaceholderText("e.g., D:/Plex/Anime")
        btn_anime = QPushButton("Browse")
        btn_anime.clicked.connect(lambda: self._browse_folder(self.root_anime))
        gl.addRow("Anime:", self._make_browse_row(self.root_anime, btn_anime))

        L.addWidget(grp)
        L.addStretch()
        return w

    def _make_browse_row(self, lineedit, button):
        row = QHBoxLayout()
        row.addWidget(lineedit, 1)
        row.addWidget(button)
        return row

    def _browse_folder(self, lineedit):
        f = QFileDialog.getExistingDirectory(self, "Select Folder", lineedit.text())
        if f:
            lineedit.setText(f)

    def _build_formats_tab(self, theme):
        w = QWidget()
        L = QVBoxLayout(w)
        L.setContentsMargins(10, 10, 10, 10)

        grp = QGroupBox("Naming Templates")
        gl = QFormLayout(grp)

        self.fmt_tv = QLineEdit(self.cfg.get("format_tv", ""))
        self.fmt_tv.setPlaceholderText("{show}/Season {s00}/{show} - {s00e00} - {title}")
        gl.addRow("TV Shows:", self.fmt_tv)

        self.fmt_movie = QLineEdit(self.cfg.get("format_movie", ""))
        self.fmt_movie.setPlaceholderText(DEFAULTS["format_movie"])
        gl.addRow("Movies:", self.fmt_movie)

        self.fmt_anime = QLineEdit(self.cfg.get("format_anime", ""))
        self.fmt_anime.setPlaceholderText("{show}/Season {s00}/{show} - {s00e00} - {title}")
        gl.addRow("Anime:", self.fmt_anime)

        L.addWidget(grp)

        help_grp = QGroupBox("Text Format Editor")
        help_l = QVBoxLayout(help_grp)

        edit_btn = QPushButton("Edit Text Format →")
        edit_btn.setStyleSheet("font-size: 14px; padding: 8px;")
        edit_btn.clicked.connect(lambda: FormatEditorDialog(self.cfg, self).exec())
        help_l.addWidget(edit_btn)

        hint = QLabel("Customize how files are renamed with variables like {show}, {s00e00}, {title}, etc.")
        hint.setStyleSheet("color: #888; font-size: 11px; margin-top: 8px;")
        help_l.addWidget(hint)

        L.addWidget(help_grp)

        grp3 = QGroupBox("Episode Numbering")
        gl3 = QFormLayout(grp3)

        self.episode_tv = QComboBox()
        self.episode_tv.addItems(["DVD", "Absolute", "Streaming"])
        ep_map = {"dvd": 0, "absolute": 1, "streaming": 2}
        self.episode_tv.setCurrentIndex(ep_map.get(self.cfg.get("episode_tv", "dvd"), 0))
        gl3.addRow("TV Shows:", self.episode_tv)

        self.episode_anime = QComboBox()
        self.episode_anime.addItems(["DVD", "Absolute", "Streaming"])
        self.episode_anime.setCurrentIndex(ep_map.get(self.cfg.get("episode_anime", "dvd"), 0))
        gl3.addRow("Anime:", self.episode_anime)

        L.addWidget(grp3)
        L.addStretch()
        return w

    def _build_appearance_tab(self, theme):
        w = QWidget()
        L = QVBoxLayout(w)
        L.setContentsMargins(10, 10, 10, 10)

        grp = QGroupBox("Theme")
        gl = QFormLayout(grp)

        self.theme_combo = QComboBox()
        for key, label in THEME_NAMES:
            self.theme_combo.addItem(label, key)
        keys = [k for k, _ in THEME_NAMES]
        current = self.cfg.get("theme", "dark")
        self.theme_combo.setCurrentIndex(keys.index(current) if current in keys else 0)
        theme_row = QHBoxLayout()
        theme_row.addWidget(self.theme_combo, 1)
        btn_custom = QPushButton("Edit Custom Theme…")
        btn_custom.setToolTip("Choose every color of the Custom theme yourself")
        btn_custom.clicked.connect(self._edit_custom_theme)
        theme_row.addWidget(btn_custom)
        gl.addRow("Color Scheme:", theme_row)

        L.addWidget(grp)

        grp_layout = QGroupBox("Layout")
        fl = QFormLayout(grp_layout)
        self.layout_combo = QComboBox()
        self.layout_combo.addItems(["Modern — side-by-side panes", "Classic — original layout"])
        self.layout_combo.setCurrentIndex(LAYOUTS.index(self.cfg.get("layout", "modern"))
                                          if self.cfg.get("layout") in LAYOUTS else 0)
        fl.addRow("Window layout:", self.layout_combo)

        self.panel_combo = QComboBox()
        self.panel_combo.addItems(["Automatic (by window width)", "Side by side", "Stacked"])
        pm = self.cfg.get("panel_mode", "auto")
        self.panel_combo.setCurrentIndex(PANEL_MODES.index(pm) if pm in PANEL_MODES else 0)
        fl.addRow("File list position:", self.panel_combo)

        self.density_combo = QComboBox()
        self.density_combo.addItems(["Comfortable", "Compact"])
        dn = self.cfg.get("density", "comfortable")
        self.density_combo.setCurrentIndex(DENSITIES.index(dn) if dn in DENSITIES else 0)
        fl.addRow("Row spacing:", self.density_combo)

        self.show_details_chk = QCheckBox("Show the details panel (where each file and subtitle goes)")
        self.show_details_chk.setChecked(self.cfg.get("show_details", True))
        fl.addRow(self.show_details_chk)

        self.animations_chk = QCheckBox("Smooth animations")
        self.animations_chk.setChecked(self.cfg.get("animations", True))
        fl.addRow(self.animations_chk)

        layout_hint = QLabel("The Modern options apply to the Modern layout. "
                             "Colors always follow the theme above.")
        layout_hint.setWordWrap(True)
        layout_hint.setStyleSheet("color:#888;font-size:11px")
        fl.addRow(layout_hint)

        def _modern_only(index):
            for wdg in (self.panel_combo, self.density_combo, self.show_details_chk, self.animations_chk):
                wdg.setEnabled(index == 0)
        self.layout_combo.currentIndexChanged.connect(_modern_only)
        _modern_only(self.layout_combo.currentIndex())
        L.addWidget(grp_layout)

        grp2 = QGroupBox("Table Display")
        gl2 = QFormLayout(grp2)

        self.show_new_path = QCheckBox("Show full new path in table")
        self.show_new_path.setChecked(self.cfg.get("show_full_path", True))
        gl2.addRow(self.show_new_path)

        self.show_detected = QCheckBox("Show detected match name")
        self.show_detected.setChecked(self.cfg.get("show_detected", True))
        gl2.addRow(self.show_detected)

        L.addWidget(grp2)
        L.addStretch()
        return w

    def _build_options_tab(self, theme):
        w = QWidget()
        L = QVBoxLayout(w)
        L.setContentsMargins(10, 10, 10, 10)

        grp = QGroupBox("File Operations")
        gl = QFormLayout(grp)

        self.action_combo = QComboBox()
        for key, label in (("move", "Move the files (default)"),
                           ("copy", "Copy the files (originals stay)"),
                           ("hardlink", "Hard-link the files (torrents keep seeding)")):
            self.action_combo.addItem(label, key)
        self.action_combo.setCurrentIndex(max(0, self.action_combo.findData(self.cfg.get("rename_action", "move"))))
        self.action_combo.setToolTip(
            "Hard links put the file in your library without using extra space and without\n"
            "moving the download, so your torrent client keeps seeding. They only work when the\n"
            "downloads and the library are on the same drive; otherwise the file is copied.")
        gl.addRow("When renaming:", self.action_combo)

        self.rename_subs = QCheckBox("Rename subtitle files alongside video")
        self.rename_subs.setChecked(self.cfg.get("rename_subtitles", True))
        gl.addRow(self.rename_subs)

        self.cleanup_folders = QCheckBox("Delete empty folders after rename")
        self.cleanup_folders.setChecked(self.cfg.get("cleanup_empty_folders", True))
        gl.addRow(self.cleanup_folders)

        self.confirm_rename = QCheckBox("Show confirmation before renaming")
        self.confirm_rename.setChecked(self.cfg.get("confirm_rename", True))
        gl.addRow(self.confirm_rename)

        self.confirm_conflicts = QCheckBox("Ask before skipping conflicting files")
        self.confirm_conflicts.setChecked(self.cfg.get("confirm_conflicts", True))
        gl.addRow(self.confirm_conflicts)

        L.addWidget(grp)

        grp2 = QGroupBox("Behavior")
        gl2 = QFormLayout(grp2)

        self.remember_folders = QCheckBox("Remember last source folder")
        self.remember_folders.setChecked(self.cfg.get("remember_folders", True))
        gl2.addRow(self.remember_folders)

        self.auto_detect_type = QCheckBox("Auto-detect media type from folder structure")
        self.auto_detect_type.setChecked(self.cfg.get("auto_detect_type", False))
        gl2.addRow(self.auto_detect_type)

        L.addWidget(grp2)
        L.addStretch()
        return w

    def _build_api_tab(self, theme):
        w = QWidget()
        L = QVBoxLayout(w)
        L.setContentsMargins(10, 10, 10, 10)

        grp = QGroupBox("Metadata Sources")
        gl = QFormLayout(grp)

        self.use_tvmaze = QCheckBox("Use TVMaze for TV show metadata & posters")
        self.use_tvmaze.setChecked(self.cfg.get("use_tvmaze", True))
        gl.addRow(self.use_tvmaze)

        self.use_anidb = QCheckBox("Use AniDB for anime metadata")
        self.use_anidb.setChecked(self.cfg.get("use_anidb", True))
        gl.addRow(self.use_anidb)

        self.use_cinemeta = QCheckBox("Use Cinemeta for movie titles && years (no key)")
        self.use_cinemeta.setChecked(self.cfg.get("use_cinemeta", True))
        gl.addRow(self.use_cinemeta)

        note = QLabel("TVMaze (TV), AniDB (anime) and Cinemeta (movies) are free — no key needed.")
        note.setStyleSheet("color:#888;font-size:11px")
        gl.addRow("", note)

        L.addWidget(grp)

        grp2 = QGroupBox("TMDB (Movies)")
        gl2 = QFormLayout(grp2)

        self.tmdb_key = QLineEdit(self.cfg.get("tmdb_api_key", ""))
        self.tmdb_key.setPlaceholderText("Paste your free TMDB v3 API key here")
        self.tmdb_key.setEchoMode(QLineEdit.EchoMode.Password)
        key_row = QHBoxLayout()
        key_row.addWidget(self.tmdb_key, 1)
        get_key = QPushButton("Get Free Key")
        get_key.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl("https://www.themoviedb.org/settings/api")))
        key_row.addWidget(get_key)
        gl2.addRow("API Key:", key_row)

        note2 = QLabel("Optional. With a key, movies are matched against TMDB for correct "
                       "titles and years; without one, movie names "
                       "come from the filename only.")
        note2.setWordWrap(True)
        note2.setStyleSheet("color:#888;font-size:11px")
        gl2.addRow("", note2)

        L.addWidget(grp2)
        L.addStretch()
        return w

    def _build_about_tab(self, theme):
        w = QWidget()
        L = QVBoxLayout(w)
        L.setContentsMargins(10, 10, 10, 10)

        grp = QGroupBox("Support PathRenamer")
        gl = QVBoxLayout(grp)

        kofi_btn = QPushButton("☕ Buy me a coffee on Ko-fi")
        kofi_btn.setStyleSheet("font-size: 14px; padding: 10px; background: #ff7744; color: white; font-weight: bold; border: none;")
        kofi_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://ko-fi.com/pathrenamer")))
        gl.addWidget(kofi_btn)

        desc = QLabel(
            "PathRenamer is free. "
            "A lot of time went into this - if you find it useful, consider supporting development!"
        )
        desc.setStyleSheet("color: #888; font-size: 11px; margin-top: 8px;")
        desc.setWordWrap(True)
        gl.addWidget(desc)

        version = QLabel(f"Version: {APP_VERSION}")
        version.setStyleSheet("color: #666; font-size: 11px; margin-top: 8px;")
        gl.addWidget(version)

        L.addWidget(grp)
        L.addStretch()
        return w

    def _save(self):
        ep_map = {0: "dvd", 1: "absolute", 2: "streaming"}

        self.cfg.update({
            "root_tv": self.root_tv.text().strip(),
            "root_movie": self.root_movie.text().strip(),
            "root_anime": self.root_anime.text().strip(),

            "format_tv": self.fmt_tv.text().strip(),
            "format_movie": self.fmt_movie.text().strip(),
            "format_anime": self.fmt_anime.text().strip(),
            "episode_tv": ep_map[self.episode_tv.currentIndex()],
            "episode_anime": ep_map[self.episode_anime.currentIndex()],
            "theme": self.theme_combo.currentData(),
            "layout": LAYOUTS[self.layout_combo.currentIndex()],
            "panel_mode": PANEL_MODES[self.panel_combo.currentIndex()],
            "density": DENSITIES[self.density_combo.currentIndex()],
            "show_details": self.show_details_chk.isChecked(),
            "animations": self.animations_chk.isChecked(),
            "show_full_path": self.show_new_path.isChecked(),
            "show_detected": self.show_detected.isChecked(),
            "rename_subtitles": self.rename_subs.isChecked(),
            "rename_action": self.action_combo.currentData(),
            "cleanup_empty_folders": self.cleanup_folders.isChecked(),
            "confirm_rename": self.confirm_rename.isChecked(),
            "confirm_conflicts": self.confirm_conflicts.isChecked(),
            "remember_folders": self.remember_folders.isChecked(),
            "auto_detect_type": self.auto_detect_type.isChecked(),
            "use_tvmaze": self.use_tvmaze.isChecked(),
            "use_anidb": self.use_anidb.isChecked(),
            "use_cinemeta": self.use_cinemeta.isChecked(),
            "tmdb_api_key": self.tmdb_key.text().strip(),
            "first_run": False,
        })
        cfg_save(self.cfg)
        self.accept()

# ═══════════════════════════════════════════════════════════════════
#  RENAME TABLE
# ═══════════════════════════════════════════════════════════════════
# Row colours per status: (background, foreground)
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

def row_colors(pal):
    """Preview row colours per status, (background, text), from a theme palette."""
    return {k: (QColor(pal[f"row_{k}_bg"]), QColor(pal[f"row_{k}_fg"]))
            for k in ("ok", "conflict", "low", "error")}

COLUMNS     = ["Original Filename","Detected Match","New Path","Subtitles","Status"]
STATUS_COL  = 4

class _WrapDelegate(QStyledItemDelegate):
    """Rows tall enough for every wrapped line plus the cell padding, so the
    last line is never cut to "…"."""
    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        width = self.parent().columnWidth(index.column()) - 34      # minus padding and margins
        text = index.data() or ""
        if width > 20 and text:
            fm = option.fontMetrics
            # Qt breaks at spaces and, for long words such as paths, anywhere:
            # measure both ways and keep the taller
            needed = max(fm.boundingRect(0, 0, width, 100000, int(flags), text).height()
                         for flags in (Qt.TextFlag.TextWordWrap,
                                       Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextWrapAnywhere))
            size.setHeight(max(size.height(), needed + 12))
        return size


class RenameTable(QTableWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._cands = []
        self._dest_root = ""
        self._show_detected = True
        self._show_full_path = True
        self._modern = False
        self._row_colors = row_colors(THEMES["dark"])
        self.setColumnCount(len(COLUMNS))
        self.setHorizontalHeaderLabels(COLUMNS)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.verticalHeader().setVisible(False)
        hh = self.horizontalHeader()
        self._user_sized = False
        self._wrap = _WrapDelegate(self)
        self.viewport().installEventFilter(self)
        hh.sectionResized.connect(self._on_section_resized)
        self.set_modern(False)

        self.cellClicked.connect(self._on_cell_clicked)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._build_context_menu)

    def set_display_options(self, show_detected=True, show_full_path=True):
        self._show_detected = show_detected
        self._show_full_path = show_full_path
        self.setColumnHidden(1, not show_detected)
        if not getattr(self, "_user_sized", False):
            self._fit_columns()

    # Starting share of the width for each column; every column can be
    # resized by dragging its header edge, and text wraps to fit.
    COLUMN_SHARES = (0.28, 0.17, 0.33, 0.11, 0.11)

    def set_modern(self, modern: bool, density: str = "comfortable"):
        """Modern layout: smooth scrolling, no grid, subtitle counts instead of
        names. In both layouts all columns are resizable and long text wraps
        onto more lines instead of being cut off."""
        self._modern = modern
        hh, vh = self.horizontalHeader(), self.verticalHeader()
        self.setWordWrap(True)
        self.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.setItemDelegate(self._wrap)
        for col in range(len(COLUMNS)):
            hh.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        hh.setStretchLastSection(False)
        hh.setMinimumSectionSize(60)
        hh.setDefaultSectionSize(80)          # also the stretched last column's minimum
        vh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        if modern:
            self.setShowGrid(False)
            self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
            self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
            vh.setMinimumSectionSize(24 if density == "compact" else 32)
            hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self.horizontalHeaderItem(STATUS_COL).setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        else:
            self.setShowGrid(True)
            self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerItem)
            self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerItem)
            vh.setMinimumSectionSize(30)
            hh.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self._user_sized = False
        self._fit_columns()
        if self._cands:
            self.populate(self._cands, self._dest_root)

    def _fit_columns(self):
        """Share the width between the columns until the user resizes one."""
        width = self.viewport().width()
        visible = [c for c in range(len(COLUMNS)) if not self.isColumnHidden(c)]
        if width < 100 or not visible:
            return
        if self.verticalScrollBar().isVisible():
            width = self.viewport().width()
        width -= 2
        total = sum(self.COLUMN_SHARES[c] for c in visible)
        last = visible[-1]                  # the last one stretches to fill what's left
        last_w = max(int(width * self.COLUMN_SHARES[last] / total),
                     self.horizontalHeader().sectionSizeHint(last),
                     self.horizontalHeader().defaultSectionSize())
        rest = sum(self.COLUMN_SHARES[c] for c in visible[:-1]) or 1
        self._fitting = True
        try:
            used = 0
            for c in visible[:-1]:
                w = max(60, int((width - last_w) * self.COLUMN_SHARES[c] / rest))
                self.setColumnWidth(c, w)
                used += w
            self.setColumnWidth(last, max(60, width - used))
        finally:
            self._fitting = False

    def _on_section_resized(self, *_):
        # Only a drag on a header edge counts as the user choosing widths
        if not getattr(self, "_fitting", False) and QApplication.mouseButtons() != Qt.MouseButton.NoButton:
            self._user_sized = True
        self.resizeRowsToContents()

    def resizeEvent(self, event):
        self._fitting = True
        try:
            super().resizeEvent(event)
        finally:
            self._fitting = False
        if not getattr(self, "_user_sized", False):
            self._fit_columns()

    def eventFilter(self, obj, event):
        # The visible area also shrinks when a scroll bar appears: refit then too
        if obj is self.viewport() and event.type() == QEvent.Type.Resize \
                and not getattr(self, "_user_sized", False) and not getattr(self, "_fitting", False):
            QTimer.singleShot(0, self._fit_columns)
        return super().eventFilter(obj, event)

    def set_palette(self, pal):
        self._row_colors = row_colors(pal)
        if self._cands:
            self.populate(self._cands, self._dest_root)

    def _fill_row(self, row, c):
        if self._show_full_path and self._dest_root:
            new_path = str(Path(self._dest_root) / c.dest_rel)
        else:
            new_path = c.dest_rel
        detected = Path(c.dest_rel).stem
        if c.error:
            bg, fg, status = *self._row_colors["error"], f"⚠ Error: {c.error[:50]}"
        elif c.conflict:
            bg, fg, status = *self._row_colors["conflict"], "✖ Conflict"
        elif c.confidence >= 0.85: bg, fg, status = *self._row_colors["ok"], "✔ Ready"
        else:
            bg, fg, status = *self._row_colors["low"], f"? Low ({int(c.confidence*100)}%)"

        sub_count = len(c.subtitles) if hasattr(c, "subtitles") and c.subtitles else 0
        if self._modern:
            sub_text = f"{sub_count} subtitle{'s' if sub_count != 1 else ''}" if sub_count else "—"
        elif sub_count > 0:
            sub_names = ", ".join(s.name for s, _ in c.subtitles[:5])
            if sub_count > 5:
                sub_text = f"{sub_names} +{sub_count-5} more"
            else:
                sub_text = sub_names
        else:
            sub_text = "None"
        # Hovering the Subtitles cell shows where each subtitle goes
        sub_tip = "\n".join(f"{s.name}  →  {Path(d).name}" for s, d in c.subtitles) if sub_count else ""

        texts = [c.source.name, detected, new_path, sub_text, status]
        tips = [str(c.source), "", new_path, sub_tip, c.error]
        for col, text in enumerate(texts):
            item = QTableWidgetItem(text)
            item.setBackground(bg); item.setForeground(fg)
            if tips[col]:
                item.setToolTip(tips[col])
            if col == STATUS_COL: item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.setItem(row, col, item)
        self.resizeRowToContents(row)

    def populate(self, cands, dest_root):
        self._cands = cands
        self._dest_root = dest_root
        self.setRowCount(len(cands))
        for row, c in enumerate(cands):
            self._fill_row(row, c)

    def append_candidate(self, c, dest_root):
        self._dest_root = dest_root
        if c not in self._cands:
            self._cands.append(c)
        row = self.rowCount()
        self.setRowCount(row + 1)
        self._fill_row(row, c)

    def replace_candidate(self, row, c, dest_root):
        self._dest_root = dest_root
        self._cands[row] = c
        self._fill_row(row, c)

    def refresh_rows(self):
        """Redraw every row (after duplicates / conflicts were re-checked)."""
        rows = self.selected_rows()
        self.populate(self._cands, self._dest_root)
        self._select_rows(rows)

    def move_rows(self, rows, delta):
        """Move the given rows up (-1) or down (+1) together; returns their new rows."""
        rows = sorted(set(rows))
        if not rows or rows[0] + delta < 0 or rows[-1] + delta >= len(self._cands):
            return rows
        moving = [self._cands[r] for r in rows]
        rest = [c for i, c in enumerate(self._cands) if i not in set(rows)]
        at = rows[0] + delta
        self._cands = rest[:at] + moving + rest[at:]
        self.populate(self._cands, self._dest_root)
        new_rows = list(range(at, at + len(moving)))
        self._select_rows(new_rows)
        return new_rows

    def _select_rows(self, rows):
        self.clearSelection()
        mode = self.selectionMode()
        self.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        for r in rows:
            if 0 <= r < self.rowCount():
                self.selectRow(r)
        self.setSelectionMode(mode)

    def clear_all(self): self._cands = []; self.setRowCount(0)
    def get_cands(self): return self._cands
    def conflict_count(self): return sum(1 for c in self._cands if c.conflict)
    def error_count(self):    return sum(1 for c in self._cands if c.error)
    def selected_rows(self): return sorted(set(item.row() for item in self.selectedItems()), reverse=True)
    def selected_items(self): return list(self.selectedItems())
    def remove_selected(self):
        rows = self.selected_rows()
        for row in rows:
            self.removeRow(row)
        self._cands = [c for i, c in enumerate(self._cands) if i not in rows]

    def _on_cell_clicked(self, row, col):
        if col == STATUS_COL:
            item = self.item(row, STATUS_COL)
            if item and "Error:" in item.text():
                c = self._cands[row]
                dlg = QDialog()
                dlg.setWindowTitle("Error Details")
                dlg.setMinimumSize(500, 200)
                L = QVBoxLayout(dlg)
                text = QTextEdit(c.error if c.error else "Unknown error")
                text.setReadOnly(True)
                L.addWidget(text)
                btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
                btns.accepted.connect(dlg.accept)
                L.addWidget(btns)
                dlg.exec()

    def _build_context_menu(self, pos):
        menu = QMenu()
        copy_action = QAction("Copy", self)
        copy_action.triggered.connect(self._copy_selection)
        menu.addAction(copy_action)
        menu.exec(self.mapToGlobal(pos))

    def _copy_selection(self):
        selected = self.selectedItems()
        if selected:
            text = "\t".join(item.text() for item in selected)
            QApplication.clipboard().setText(text)

# ═══════════════════════════════════════════════════════════════════
#  BACKGROUND WORKER
# ═══════════════════════════════════════════════════════════════════
class ScanWorker(QObject):
    progress       = pyqtSignal(int, int)
    candidate_ready = pyqtSignal(object)
    finished       = pyqtSignal()

    def __init__(self, files, engine, media_type, dest_root, selected_overrides=None,
                 episode_overrides=None):
        super().__init__()
        self.files = files; self.engine = engine
        self.media_type = media_type; self.dest_root = dest_root
        self.selected_overrides = selected_overrides or {}       # str(path) -> (title, id)
        self.episode_overrides = episode_overrides or {}         # str(path) -> (season, [episodes])
        self._sub_output_counts = {}
        self._dest_owner = {}     # normalised destination -> first source file renamed to it

    def run(self):
        log.info(f"Scan started: {len(self.files)} file(s), type={self.media_type}, dest={self.dest_root}")
        # Videos first: their subtitles are renamed with them, so a subtitle
        # file that was also added to the list on its own is not done twice.
        files = ([f for f in self.files if f.suffix.lower() not in SUB_EXT] +
                 [f for f in self.files if f.suffix.lower() in SUB_EXT])
        claimed = set()      # subtitles already renamed together with a video
        for i, f in enumerate(files):
            try:
                log.debug(f"Matching: {f.name}")
                if f.suffix.lower() in SUB_EXT and os.path.normcase(str(f)) in claimed:
                    log.debug("  -> renamed with its video")
                    continue

                if not f.exists():
                    self.candidate_ready.emit(Candidate(
                        f, f.name, self.media_type, 0.0,
                        error="Source file no longer exists (already renamed or removed)"))
                    continue

                key = str(f)
                selected_title, selected_id = None, None
                if key in self.selected_overrides:
                    selected_title, selected_id = self.selected_overrides[key]

                season, episodes = self.episode_overrides.get(key, (None, None))
                cand = self.engine.match_with_override(f, self.media_type, selected_title, selected_id,
                                                       season=season, episodes=episodes)

                # Subtitle files picked directly: give them Plex subtitle names
                src_ext = f.suffix.lower()
                if src_ext in SUB_EXT:
                    cand = self.engine._transform_subtitle_dest(cand, True, self._sub_output_counts)

                log.debug(f"  -> dest_rel={cand.dest_rel}  conf={cand.confidence:.2f}  err={cand.error!r}")
                dest = Path(self.dest_root) / cand.dest_rel
                if dest.exists() and not _same_path(dest, f):
                    cand.conflict = True
                    log.warning(f"  CONFLICT: {dest}")
                if not cand.error:
                    # e.g. a 720p and a 1080p copy of the same episode: only the
                    # first one can take the name, so flag the others now
                    key = os.path.normcase(os.path.normpath(str(dest)))
                    owner = self._dest_owner.setdefault(key, f)
                    if not _same_path(owner, f):
                        cand.error = f"{DUPLICATE_PREFIX}{owner.name}"
                        log.warning(f"  DUPLICATE: {f.name} and {owner.name} -> {dest}")
                    else:
                        claimed.update(os.path.normcase(str(s)) for s, _ in cand.subtitles)
                self.candidate_ready.emit(cand)
            except Exception as e:
                log.exception(f"Error matching {f.name}: {e}")
                self.candidate_ready.emit(Candidate(f, f.name, self.media_type, 0.0, error=str(e)))
            finally:
                self.progress.emit(i + 1, len(files))
        log.info("Scan finished.")
        self.finished.emit()

# ═══════════════════════════════════════════════════════════════════
#  MATCH SELECTOR DIALOG
# ═══════════════════════════════════════════════════════════════════
class MatchSelectorDialog(QDialog):
    def __init__(self, filename, matches, parent=None):
        super().__init__(parent)
        self.matches = matches
        self.selected_match = None
        self.setWindowTitle(f"Select Match - {filename}")
        self.setMinimumSize(900, 650)
        self._build_ui()

    def _build_ui(self):
        L = QVBoxLayout(self)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(550)

        grid = QWidget()
        gl = QGridLayout(grid)
        gl.setSpacing(20)

        for i, m in enumerate(self.matches):
            card = self._create_match_card(m)
            gl.addWidget(card, i // 2, i % 2)

        scroll.setWidget(grid)
        L.addWidget(scroll)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        btns.rejected.connect(self.reject)
        L.addWidget(btns)

    def _create_match_card(self, match):
        card = QFrame()
        card.setFrameStyle(QFrame.Shape.Box)
        card.setLineWidth(1)
        card.setMinimumHeight(280)
        card.setMinimumWidth(380)

        hl = QHBoxLayout(card)

        poster_frame = QFrame()
        poster_frame.setFixedWidth(120)
        poster_frame.setMinimumHeight(180)
        poster_layout = QVBoxLayout(poster_frame)
        poster_layout.setContentsMargins(0, 0, 0, 0)

        poster_url = match.get("poster", "")
        log.debug(f"Match card poster_url: '{poster_url}' for '{match.get('title', match.get('show', 'Unknown'))}'")
        if not poster_url:
            poster_label = QLabel("No Image")
            poster_label.setFixedSize(120, 180)
            poster_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            poster_label.setStyleSheet("color: #666; background: #2a2a3a; border: 1px solid #45475a;")
            poster_layout.addWidget(poster_label)
        else:
            poster_label = QLabel("Loading...")
            poster_label.setFixedSize(120, 180)
            poster_label.setScaledContents(False)
            poster_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            poster_label.setStyleSheet("color: #666; background: #2a2a3a; border: 1px solid #45475a;")
            poster_layout.addWidget(poster_label)

            if poster_url.startswith("http"):
                self._load_poster(poster_label, poster_url)
            elif poster_url.startswith("/"):
                full_url = "https://image.tmdb.org/t/p/w200" + poster_url
                self._load_poster(poster_label, full_url)
            else:
                poster_label.setText("No Image")
                poster_label.setStyleSheet("color: #666; background: #2a2a3a;")

        info_frame = QWidget()
        info_layout = QVBoxLayout(info_frame)

        title_text = match.get("title", match.get("show", "Unknown"))
        title = QLabel(f"<b>{title_text}</b>")
        title.setWordWrap(True)
        title.setStyleSheet("font-size: 14px;")
        info_layout.addWidget(title)

        year = match.get("year", "") or match.get("first_air_date", "") or match.get("release_date", "")
        if year and len(str(year)) == 4:
            info_layout.addWidget(QLabel(f"Year: {year}"))

        rating = match.get("rating", 0)
        if rating:
            info_layout.addWidget(QLabel(f"Rating: {rating:.1f}/10"))

        overview = match.get("overview", "")
        if overview:
            desc = QLabel(overview[:200] + "..." if len(overview) > 200 else overview)
            desc.setWordWrap(True)
            desc.setStyleSheet("color: #888; font-size: 11px;")
            info_layout.addWidget(desc)

        btn = QPushButton("Use This Match")
        btn.setMinimumHeight(30)
        btn.clicked.connect(lambda: self._select(match))
        info_layout.addWidget(btn)

        hl.addWidget(poster_frame)
        hl.addWidget(info_frame, 1)

        return card

    def _load_poster(self, label, url):
        try:
            import requests
            response = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            if response.status_code == 200:
                from PyQt6.QtGui import QPixmap
                pixmap = QPixmap()
                pixmap.loadFromData(response.content)
                if not pixmap.isNull() and pixmap.width() > 0:
                    label.setText("")
                    label.setPixmap(pixmap.scaled(120, 180, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                    label.setStyleSheet("background: transparent; border: 1px solid #45475a;")
                    log.info(f"Poster loaded successfully: {url}")
                else:
                    label.setText("No Image")
                    label.setStyleSheet("color: #666; background: #2a2a3a;")
                    log.warning("Poster image empty or invalid")
            else:
                label.setText("No Image")
                label.setStyleSheet("color: #666; background: #2a2a3a;")
                log.warning(f"Poster HTTP error: {response.status_code}")
        except Exception as e:
            log.warning(f"Poster fetch exception: {e}")
            label.setText("No Image")
            label.setStyleSheet("color: #666; background: #2a2a3a;")

    def _on_poster_timeout(self, reply, label):
        if reply.isRunning():
            reply.abort()
        label.setText("Timeout")
        label.setStyleSheet("color: #666; background: #2a2a3a;")
        log.warning("Poster request timed out")

    def _on_poster_loaded(self, reply, label):
        try:
            if reply.error() == 0:
                data = reply.readAll()
                from PyQt6.QtGui import QPixmap
                pixmap = QPixmap()
                pixmap.loadFromData(data)
                if not pixmap.isNull() and pixmap.width() > 0:
                    label.setText("")
                    label.setPixmap(pixmap.scaled(120, 180, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                    label.setStyleSheet("background: transparent; border: 1px solid #45475a;")
                    log.info("Poster loaded successfully")
                else:
                    label.setText("No Image")
                    label.setStyleSheet("color: #666; background: #2a2a3a;")
                    log.warning("Poster image empty or invalid")
            else:
                label.setText("No Image")
                label.setStyleSheet("color: #666; background: #2a2a3a;")
                log.warning(f"Poster HTTP error: {reply.error()} - {reply.errorString()}")
            reply.deleteLater()
        except Exception as e:
            label.setText("No Image")
            label.setStyleSheet("color: #666; background: #2a2a3a;")
            log.warning(f"Poster load exception: {e}")

    def _select(self, match):
        self.selected_match = match
        self.accept()

# ═══════════════════════════════════════════════════════════════════
#  CUSTOM THEME EDITOR
# ═══════════════════════════════════════════════════════════════════
class CustomThemeDialog(QDialog):
    """Every colour of the Custom theme, grouped, with a live preview in the
    main window. Starts from a built-in theme; themes can be exported and
    imported as small JSON files."""

    def __init__(self, cfg, parent=None, preview=None):
        super().__init__(parent)
        self.setWindowTitle("Custom Theme")
        self.setMinimumSize(600, 640)
        self._preview = preview
        custom = cfg.get("custom_theme")
        if isinstance(custom, dict) and custom.get("colors"):
            self._base = custom.get("base") if custom.get("base") in THEMES else "dark"
            self._pal = custom_palette(custom)
        else:            # first time: start from the theme in use
            current = cfg.get("theme", "dark")
            self._base = current if current in THEMES else "dark"
            self._pal = dict(THEMES[self._base])
        self._rows = {}
        self._labels = {role: label for role, label, _ in ROLES}

        L = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Start from:"))
        self.base_combo = QComboBox()
        for key, label in THEME_NAMES:
            if key in THEMES:
                self.base_combo.addItem(label, key)
        self.base_combo.setCurrentIndex(max(0, self.base_combo.findData(self._base)))
        top.addWidget(self.base_combo)
        btn_load = QPushButton("Use Its Colors")
        btn_load.setToolTip("Replace every color below with the chosen theme's colors")
        btn_load.clicked.connect(self._load_base)
        top.addWidget(btn_load)
        top.addStretch()
        btn_import = QPushButton("Import…"); btn_import.clicked.connect(self._import)
        btn_export = QPushButton("Export…"); btn_export.clicked.connect(self._export)
        top.addWidget(btn_import); top.addWidget(btn_export)
        L.addLayout(top)

        hint = QLabel("Click a color square to pick a color, or type a hex code like #1e1e24. "
                      "The window shows your changes as you make them.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888;font-size:11px")
        L.addWidget(hint)

        body = QWidget()
        bl = QVBoxLayout(body); bl.setContentsMargins(0, 0, 6, 0)
        groups = []
        for role, label, group in ROLES:
            if not groups or groups[-1][0] != group:
                groups.append((group, []))
            groups[-1][1].append((role, label))
        for group, roles in groups:
            box = QGroupBox(group)
            form = QFormLayout(box)
            for role, label in roles:
                swatch = QPushButton()
                swatch.setFixedSize(40, 24)
                swatch.setCursor(Qt.CursorShape.PointingHandCursor)
                swatch.setToolTip(f"Pick the color for “{label}”")
                swatch.clicked.connect(lambda _=False, r=role: self._pick(r))
                edit = QLineEdit()
                edit.setFixedWidth(96)
                edit.setMaxLength(7)
                edit.editingFinished.connect(lambda r=role: self._typed(r))
                row = QHBoxLayout()
                row.addWidget(swatch); row.addWidget(edit); row.addStretch()
                form.addRow(label + ":", row)
                self._rows[role] = (swatch, edit)
            bl.addWidget(box)
        bl.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(body)
        L.addWidget(scroll, 1)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept); btns.rejected.connect(self.reject)
        L.addWidget(btns)
        for role in self._rows:
            self._show(role)
        self._apply_preview()

    def result_theme(self):
        """What the settings keep: {"base": theme, "colors": every role}."""
        return {"base": self._base, "colors": dict(self._pal)}

    def palette(self):
        return dict(self._pal)

    def set_color(self, role, value):
        if role in self._pal and is_color(value):
            self._pal[role] = value.lower()
            self._show(role)
            self._apply_preview()
            return True
        return False

    def _show(self, role):
        swatch, edit = self._rows[role]
        color = self._pal[role]
        swatch.setStyleSheet(f"QPushButton{{background:{color};border:1px solid #808080;border-radius:4px;padding:0}}")
        edit.setText(color)

    def _apply_preview(self):
        if self._preview:
            try:
                self._preview(dict(self._pal))
            except Exception as e:           # a preview must never break editing
                log.debug(f"Theme preview failed: {e}")

    def _pick(self, role):
        from PyQt6.QtWidgets import QColorDialog
        color = QColorDialog.getColor(QColor(self._pal[role]), self, self._labels[role])
        if color.isValid():
            self.set_color(role, color.name())

    def _typed(self, role):
        _, edit = self._rows[role]
        text = edit.text().strip()
        if text and not text.startswith("#"):
            text = "#" + text
        if not self.set_color(role, text):
            edit.setText(self._pal[role])          # not a colour: put the old one back

    def _load_base(self):
        self._base = self.base_combo.currentData()
        self._pal = dict(THEMES[self._base])
        for role in self._rows:
            self._show(role)
        self._apply_preview()

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export Theme", "PathRenamer theme.json",
                                              "Theme files (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"pathrenamer_theme": 1, **self.result_theme()}, f, indent=2)
        except OSError as e:
            QMessageBox.warning(self, "Export Theme", f"Couldn't save the theme:\n{e}")

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import Theme", "", "Theme files (*.json)")
        if path:
            self.import_file(path)

    def import_file(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or not isinstance(data.get("colors"), dict):
                raise ValueError("not a PathRenamer theme file")
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Import Theme", f"Couldn't read the theme:\n{e}")
            return False
        self._base = data.get("base") if data.get("base") in THEMES else self._base
        self._pal = custom_palette({"base": self._base, "colors": data["colors"]})
        self.base_combo.setCurrentIndex(max(0, self.base_combo.findData(self._base)))
        for role in self._rows:
            self._show(role)
        self._apply_preview()
        return True


# ═══════════════════════════════════════════════════════════════════
#  RENAME HISTORY
# ═══════════════════════════════════════════════════════════════════
class HistoryDialog(QDialog):
    """Past renames (kept between sessions); any of them can be undone."""

    def __init__(self, renamer, parent=None):
        super().__init__(parent)
        self.renamer = renamer
        self.undo_results = []
        self.setWindowTitle("Rename History")
        self.setMinimumSize(820, 520)
        L = QVBoxLayout(self)
        hint = QLabel("Every rename is listed here, newest first, also after closing the app. "
                      "Select one to see its files; Undo puts them back.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888;font-size:11px")
        L.addWidget(hint)
        split = QSplitter(Qt.Orientation.Vertical)
        self.batch_list = QTableWidget(0, 3)
        self.batch_list.setHorizontalHeaderLabels(["When", "What", "Status"])
        self.batch_list.verticalHeader().setVisible(False)
        self.batch_list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.batch_list.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.batch_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        hh = self.batch_list.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.batch_list.itemSelectionChanged.connect(self._show_files)
        split.addWidget(self.batch_list)
        self.files = QTableWidget(0, 2)
        self.files.setHorizontalHeaderLabels(["From", "To"])
        self.files.verticalHeader().setVisible(False)
        self.files.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.files.setTextElideMode(Qt.TextElideMode.ElideLeft)
        for col in (0, 1):
            self.files.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        split.addWidget(self.files)
        L.addWidget(split, 1)
        row = QHBoxLayout()
        self.btn_clear = QPushButton("Clear History")
        self.btn_clear.setToolTip("Forget every entry (files are not touched)")
        self.btn_clear.clicked.connect(self._clear)
        row.addWidget(self.btn_clear)
        row.addStretch()
        self.btn_undo = QPushButton("↩  Undo This Rename")
        self.btn_undo.setObjectName("btn_undo")
        self.btn_undo.clicked.connect(self._undo_selected)
        row.addWidget(self.btn_undo)
        close = QPushButton("Close"); close.clicked.connect(self.accept)
        row.addWidget(close)
        L.addLayout(row)
        self._fill()

    def _fill(self):
        self._batches = self.renamer.history.batches()
        self.batch_list.setRowCount(len(self._batches))
        for i, b in enumerate(self._batches):
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(b.get("time", 0)))
            what = describe_batch(b) + ("  (watch folder)" if b.get("source") == "watch" else "")
            status = "Undone" if b.get("undone") else "Done"
            for col, text in enumerate((when, what, status)):
                self.batch_list.setItem(i, col, QTableWidgetItem(text))
        if self._batches:
            self.batch_list.selectRow(0)
        else:
            self.files.setRowCount(0)
        self.btn_clear.setEnabled(bool(self._batches))
        self._update_button()

    def _selected(self):
        rows = sorted({i.row() for i in self.batch_list.selectedItems()})
        return self._batches[rows[0]] if rows and rows[0] < len(self._batches) else None

    def _show_files(self):
        b = self._selected()
        ops = b["ops"] if b else []
        self.files.setRowCount(len(ops))
        for i, op in enumerate(ops):
            for col, text in enumerate((op["src"], op["dst"])):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                self.files.setItem(i, col, item)
        self._update_button()

    def _update_button(self):
        b = self._selected()
        self.btn_undo.setEnabled(bool(b) and not b.get("undone"))

    def _undo_selected(self):
        b = self._selected()
        if not b or b.get("undone"):
            return
        verb = "removes the copies" if all(op.get("method") in ("copy", "hardlink", "none") for op in b["ops"]) \
            else "moves the files back"
        reply = QMessageBox.question(self, "Undo Rename",
                                     f"Undo “{describe_batch(b)}”? This {verb}.",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        result = self.renamer.undo_batch(b["id"])
        self.undo_results.append(result)
        if result.failed:
            details = "\n".join(f"{o.src.name}: {o.error}" for o in result.failed[:10])
            QMessageBox.warning(self, "Some Undos Failed", details)
        row = self.batch_list.currentRow()
        self._fill()
        if 0 <= row < self.batch_list.rowCount():
            self.batch_list.selectRow(row)

    def _clear(self):
        reply = QMessageBox.question(self, "Clear History",
                                     "Forget all past renames? Your files stay where they are, "
                                     "but these renames can't be undone afterwards.",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.renamer.history.clear()
            self._fill()


# ═══════════════════════════════════════════════════════════════════
#  EPISODE NUMBER DIALOG
# ═══════════════════════════════════════════════════════════════════
class EpisodeNumbersDialog(QDialog):
    """Season and episode for one file ("Change Episode…"), or a show name,
    season and first episode to number several files in list order
    ("Number in Order…")."""

    def __init__(self, parent=None, title="", season=1, first=1, last=None, count=1, number_mode=False):
        super().__init__(parent)
        from PyQt6.QtWidgets import QSpinBox
        self.number_mode = number_mode
        self.count = count
        self.setWindowTitle("Number in Order" if number_mode else "Change Episode")
        self.setMinimumWidth(420)
        L = QVBoxLayout(self)
        if number_mode:
            hint = QLabel(f"The {count} selected file(s) get consecutive episode numbers in the "
                          "order shown in the list. Use Move Up / Move Down (Ctrl+↑ / Ctrl+↓) "
                          "to put them in the right order first.")
        else:
            hint = QLabel("Use this when the file name has the wrong episode number, or none.")
        hint.setWordWrap(True)
        L.addWidget(hint)
        form = QFormLayout()
        self.title_edit = QLineEdit(title)
        self.title_edit.setPlaceholderText("Show name")
        form.addRow("Show:", self.title_edit)
        self.season_spin = QSpinBox(); self.season_spin.setRange(0, 999)
        self.season_spin.setValue(season if season is not None else 1)
        form.addRow("Season:", self.season_spin)
        self.first_spin = QSpinBox(); self.first_spin.setRange(0, 9999); self.first_spin.setValue(first or 1)
        form.addRow("First episode:" if number_mode else "Episode:", self.first_spin)
        self.last_spin = None
        if not number_mode:
            self.last_spin = QSpinBox(); self.last_spin.setRange(0, 9999)
            self.last_spin.setValue(last if last else self.first_spin.value())
            self.last_spin.setToolTip("For a file with two or more episodes (S01E01-E02)")
            form.addRow("Last episode:", self.last_spin)
            self.first_spin.valueChanged.connect(
                lambda v: self.last_spin.setValue(max(v, self.last_spin.value())))
        L.addLayout(form)
        self.preview = QLabel(); self.preview.setStyleSheet("color:#888")
        L.addWidget(self.preview)
        for w in (self.season_spin, self.first_spin) + ((self.last_spin,) if self.last_spin else ()):
            w.valueChanged.connect(self._update_preview)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept); btns.rejected.connect(self.reject)
        L.addWidget(btns)
        self._update_preview()

    def _update_preview(self):
        s, first = self.season_spin.value(), self.first_spin.value()
        if self.number_mode:
            last = first + self.count - 1
            self.preview.setText(f"S{s:02d}E{first:02d}" + (f" … S{s:02d}E{last:02d}" if self.count > 1 else ""))
        else:
            last = max(first, self.last_spin.value())
            self.preview.setText(f"S{s:02d}E{first:02d}" + (f"-E{last:02d}" if last > first else ""))

    def values(self):
        """(show name, season, [episodes per file]) — one list per file."""
        s, first = self.season_spin.value(), self.first_spin.value()
        if self.number_mode:
            eps = [[first + i] for i in range(self.count)]
        else:
            eps = [list(range(first, max(first, self.last_spin.value()) + 1))]
        return self.title_edit.text().strip(), s, eps


# ═══════════════════════════════════════════════════════════════════
#  FORMAT EDITOR
# ═══════════════════════════════════════════════════════════════════
# Template editor with live preview (opened from Settings → Formats)
class FormatEditorDialog(QDialog):
    # (variable, description, example, also available for movies)
    VARIABLES = [
        ("{show}", "Show name", "Dandadan", False),
        ("{s}", "Season number (1, 2, etc.)", "1", False),
        ("{s00}", "Season zero-padded", "01", False),
        ("{e}", "Episode number (1, 2, etc.)", "1", False),
        ("{e00}", "Episode zero-padded", "01", False),
        ("{s00e00}", "Plex format (S01E01)", "S01E01", False),
        ("{title}", "Episode/Movie title", "Episode Title", True),
        ("{year}", "Release year", "2024", True),
        ("{edition}", "Plex edition tag, when the name has one", "{edition-Director's Cut}", True),
        ("{vc}", "Video codec", "H.264", True),
        ("{c}", "Audio codec", "AAC", True),
        ("{ach}", "Audio channels", "2.0", True),
        ("{res}", "Resolution", "1080p", True),
        ("{ext}", "File extension (no dot)", "mkv", True),
    ]

    def _detect_episode_type(self, template_text):
        if not template_text:
            return "dvd"
        if "{e00}" in template_text and "{s00e00}" not in template_text:
            return "absolute"
        return "dvd"

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.parent = parent
        self.setWindowTitle("Text Format Editor")
        self.setMinimumSize(700, 550)
        self._build_ui()
        self._apply_theme()

    def _build_ui(self):
        L = QVBoxLayout(self)
        L.setContentsMargins(15, 15, 15, 15)

        self.tabs = QTabWidget()
        L.addWidget(self.tabs)

        self.build_tv_tab()
        self.build_anime_tab()
        self.build_movie_tab()

        btn_box = QDialogButtonBox()
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.setStyleSheet("min-width: 80px;")
        self.apply_btn.clicked.connect(self._apply)
        btn_box.addButton(self.apply_btn, QDialogButtonBox.ButtonRole.ActionRole)

        ok_btn = QPushButton("OK")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._save)
        btn_box.addButton(ok_btn, QDialogButtonBox.ButtonRole.AcceptRole)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_box.addButton(cancel_btn, QDialogButtonBox.ButtonRole.RejectRole)

        L.addWidget(btn_box)

    def build_tv_tab(self):
        w = QWidget()
        L = QVBoxLayout(w)
        L.setContentsMargins(10, 10, 10, 10)

        grp = QGroupBox("Preview")
        gl = QFormLayout(grp)

        self.tv_example = QLabel("Dandadan/Season 01/Dandadan S01E01 - That's How Love Starts, Ya Know?.mkv")
        self.tv_example.setObjectName("preview_label")
        self.tv_example.setWordWrap(True)
        gl.addRow("Example:", self.tv_example)

        L.addWidget(grp)

        grp2 = QGroupBox("Template")
        gl2 = QFormLayout(grp2)

        self.tv_template = QLineEdit(self.cfg.get("format_tv", "{show} ({year})/Season {s00}/{show} {s00e00} - {title}"))
        self.tv_template.setPlaceholderText("{show} ({year})/Season {s00}/{show} {s00e00} - {title}")
        self.tv_template.textChanged.connect(self._update_tv_preview)
        gl2.addRow("Template:", self.tv_template)

        self.tv_ep_type = QComboBox()
        self.tv_ep_type.addItems(["DVD", "Absolute", "Streaming"])
        saved_ep = self.cfg.get("format_tv", "")
        ep_type = self._detect_episode_type(saved_ep)
        self.tv_ep_type.setCurrentIndex({"dvd": 0, "absolute": 1, "streaming": 2}.get(ep_type, 0))
        self.tv_ep_type.currentTextChanged.connect(self._update_tv_preview)
        gl2.addRow("Episode Type:", self.tv_ep_type)

        L.addWidget(grp2)

        self._build_var_buttons(L)

        self.tabs.addTab(w, "TV Shows")

        # Show the preview for the current template
        self._update_tv_preview()

    def build_anime_tab(self):
        w = QWidget()
        L = QVBoxLayout(w)
        L.setContentsMargins(10, 10, 10, 10)

        grp = QGroupBox("Preview")
        gl = QFormLayout(grp)

        self.anime_example = QLabel("Dandadan/Season 01/Dandadan S01E01 - That's How Love Starts, Ya Know?.mkv")
        self.anime_example.setObjectName("preview_label")
        self.anime_example.setWordWrap(True)
        gl.addRow("Example:", self.anime_example)

        L.addWidget(grp)

        grp2 = QGroupBox("Template")
        gl2 = QFormLayout(grp2)

        self.anime_template = QLineEdit(self.cfg.get("format_anime", "{show} ({year})/Season {s00}/{show} {s00e00} - {title}"))
        self.anime_template.setPlaceholderText("{show} ({year})/Season {s00}/{show} {s00e00} - {title}")
        self.anime_template.textChanged.connect(self._update_anime_preview)
        gl2.addRow("Template:", self.anime_template)

        self.anime_ep_type = QComboBox()
        self.anime_ep_type.addItems(["DVD", "Absolute", "Streaming"])
        saved_ep = self.cfg.get("format_anime", "")
        ep_type = self._detect_episode_type(saved_ep)
        self.anime_ep_type.setCurrentIndex({"dvd": 0, "absolute": 1, "streaming": 2}.get(ep_type, 0))
        self.anime_ep_type.currentTextChanged.connect(self._update_anime_preview)
        gl2.addRow("Episode Type:", self.anime_ep_type)

        L.addWidget(grp2)

        self._build_var_buttons(L)

        self.tabs.addTab(w, "Anime")

        # Show the preview for the current template
        self._update_anime_preview()

    def build_movie_tab(self):
        w = QWidget()
        L = QVBoxLayout(w)
        L.setContentsMargins(10, 10, 10, 10)

        grp = QGroupBox("Preview")
        gl = QFormLayout(grp)

        self.movie_example = QLabel("Dandadan (2024)/Dandadan (2024).mkv")
        self.movie_example.setObjectName("preview_label")
        self.movie_example.setWordWrap(True)
        gl.addRow("Example:", self.movie_example)

        L.addWidget(grp)

        grp2 = QGroupBox("Template")
        gl2 = QFormLayout(grp2)

        self.movie_template = QLineEdit(self.cfg.get("format_movie", DEFAULTS["format_movie"]))
        self.movie_template.setPlaceholderText(DEFAULTS["format_movie"])
        self.movie_template.textChanged.connect(self._update_movie_preview)
        gl2.addRow("Template:", self.movie_template)

        L.addWidget(grp2)

        self._build_var_buttons(L, movies_only=True)

        self.tabs.addTab(w, "Movies")

        # Show the preview for the current template
        self._update_movie_preview()

    def _build_var_buttons(self, layout, movies_only=False):
        grp = QGroupBox("Variables")
        gl = QGridLayout(grp)

        if movies_only:
            variables = [v for v in self.VARIABLES if v[3]]
        else:
            variables = self.VARIABLES[:8]

        for i, (var, desc, example, _movies) in enumerate(variables):
            row, col = i // 4, i % 4
            btn = QPushButton(var)
            btn.setToolTip(f"{desc}\nExample: {example}")
            btn.clicked.connect(lambda checked, v=var: self._insert_var(v))
            gl.addWidget(btn, row, col)

        accent = palette_for(self.cfg)["accent"]
        link = QLabel(f'<a href="#variables" style="color:{accent}">Variable List</a> '
                      '— view all usable variables')
        link.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        link.linkActivated.connect(lambda _href: self._show_variable_list())
        gl.addWidget(link, (len(variables) - 1) // 4 + 1, 0, 1, 4)
        self.var_list_link = link

        layout.addWidget(grp)

    def _build_variable_list_dialog(self):
        """Dialog listing every template variable with description + example."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Variable List")
        dlg.setMinimumSize(660, 500)
        dlg.setStyleSheet(self.styleSheet())
        L = QVBoxLayout(dlg)

        table = QTableWidget(len(self.VARIABLES), 4, dlg)
        table.setHorizontalHeaderLabels(["Variable", "Description", "Example", "Media Type"])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.verticalHeader().setVisible(False)
        hh = table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        for row, (var, desc, example, movies) in enumerate(self.VARIABLES):
            media = "TV/Anime, Movies" if movies else "TV/Anime"
            for col, text in enumerate([var, desc, example, media]):
                item = QTableWidgetItem(text)
                if col == 0:
                    f = item.font(); f.setBold(True); item.setFont(f)
                table.setItem(row, col, item)

        def _copy_row(row):
            var = table.item(row, 0).text()
            QApplication.clipboard().setText(var)

        def _context_menu(pos):
            item = table.itemAt(pos)
            if item is None:
                return
            table.selectRow(item.row())
            var = table.item(item.row(), 0).text()
            menu = QMenu(table)
            act = menu.addAction(f"Copy {var}")
            act.triggered.connect(lambda: _copy_row(item.row()))
            menu.exec(table.viewport().mapToGlobal(pos))

        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        table.customContextMenuRequested.connect(_context_menu)
        dlg._copy_row = _copy_row
        dlg._table = table
        L.addWidget(table)

        note = QLabel("Tip: click a variable button above the editor to insert it, "
                      "right-click a row here to copy the variable, "
                      "or type any of these directly into a template.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#888;font-size:11px")
        L.addWidget(note)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        btns.accepted.connect(dlg.accept)
        L.addWidget(btns)
        return dlg

    def _show_variable_list(self):
        # Reuse the window if it is already open
        dlg = getattr(self, "_var_list_dlg", None)
        if dlg is not None and dlg.isVisible():
            dlg.raise_()
            dlg.activateWindow()
            return
        self._var_list_dlg = self._build_variable_list_dialog()
        self._var_list_dlg.show()

    def _insert_var(self, var):
        current_tab = self.tabs.currentWidget()
        if current_tab:
            layout = current_tab.layout()
            for i in range(layout.count()):
                item = layout.itemAt(i)
                if item and item.widget():
                    if isinstance(item.widget(), QGroupBox):
                        grp = item.widget()
                        for j in range(grp.layout().count()):
                            child = grp.layout().itemAt(j)
                            if child and child.widget() and isinstance(child.widget(), QLineEdit):
                                le = child.widget()
                                le.insert(var)
                                return

    def _update_tv_preview(self):
        template = self.tv_template.text() or "{show}/Season {s00}/{show} {s00e00} - {title}"
        ep_type = self.tv_ep_type.currentText().lower()
        result = self._format_preview(template, ep_type)
        self.tv_example.setText(result)

    def _update_anime_preview(self):
        template = self.anime_template.text() or "{show}/Season {s00}/{show} {s00e00} - {title}"
        ep_type = self.anime_ep_type.currentText().lower()
        result = self._format_preview(template, ep_type)
        self.anime_example.setText(result)

    def _update_movie_preview(self):
        template = self.movie_template.text() or DEFAULTS["format_movie"]
        result = self._format_movie_preview(template)
        self.movie_example.setText(result)

    def _format_preview(self, template, episode_type="dvd"):
        values = {
            "show": "Dandadan",
            "s": "1",
            "s00": "01",
            "e": "1",
            "e00": "01",
            "s00e00": "S01E01",
            "title": "That's How Love Starts, Ya Know?",
            "year": "2024",
            "vc": "H.264",
            "c": "AAC",
            "ach": "2.0",
            "res": "1080p",
        }
        tpl = template
        if episode_type == "absolute":
            tpl = tpl.replace("{s00e00}", "{e00}")
            tpl = tpl.replace("{s00}", "")
        for key, val in values.items():
            tpl = tpl.replace("{" + key + "}", val)
        tpl = tpl.replace("{ext}", "mkv")
        return tpl

    def _format_movie_preview(self, template):
        values = {
            "title": "Dandadan",
            "year": "2024",
            "edition": "{edition-Director's Cut}",
            "edition_name": "Director's Cut",
            "vc": "H.264",
            "c": "AAC",
            "ach": "2.0",
            "res": "1080p",
        }
        result = template
        for key, val in values.items():
            result = result.replace("{" + key + "}", val)
        result = result.replace("{ext}", "mkv")
        return result

    def _apply(self):
        self.cfg["format_tv"] = self.tv_template.text()
        self.cfg["format_anime"] = self.anime_template.text()
        self.cfg["format_movie"] = self.movie_template.text()
        self.cfg["episode_tv"] = self.tv_ep_type.currentText().lower()
        self.cfg["episode_anime"] = self.anime_ep_type.currentText().lower()
        cfg_save(self.cfg)

        if self.parent and hasattr(self.parent, "fmt_tv"):
            self.parent.fmt_tv.setText(self.cfg["format_tv"])
            self.parent.fmt_anime.setText(self.cfg["format_anime"])
            self.parent.fmt_movie.setText(self.cfg["format_movie"])

        log.info(f"Saved format templates: TV={self.cfg['format_tv']}, Anime={self.cfg['format_anime']}, Movie={self.cfg['format_movie']}, episode_tv={self.cfg['episode_tv']}, episode_anime={self.cfg['episode_anime']}")

    def _save(self):
        self._apply()
        self.accept()

    def _apply_theme(self):
        pal = palette_for(self.cfg)
        self.setStyleSheet(base_stylesheet(pal) + tab_stylesheet(pal))


# ═══════════════════════════════════════════════════════════════════
#  MODERN LAYOUT PIECES
# ═══════════════════════════════════════════════════════════════════
def _animate(owner, start, end, on_value, duration=200, on_done=None, enabled=True):
    """Move a value smoothly from start to end, calling on_value on each step
    (or straight to end when animations are off)."""
    if not enabled or start == end:
        on_value(end)
        if on_done:
            on_done()
        return None
    anim = QVariantAnimation(owner)
    anim.setStartValue(int(start)); anim.setEndValue(int(end)); anim.setDuration(duration)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    anim.valueChanged.connect(on_value)
    if on_done:
        anim.finished.connect(on_done)
    anim.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)
    return anim


class SegmentedControl(QWidget):
    """Toggle buttons where exactly one is on: TV Shows | Movies | Anime."""
    changed = pyqtSignal(int)

    def __init__(self, labels, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(4)
        self._group = QButtonGroup(self); self._group.setExclusive(True)
        for i, text in enumerate(labels):
            b = QPushButton(text); b.setObjectName("seg"); b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self._group.addButton(b, i); lay.addWidget(b)
        self._group.idClicked.connect(self.changed.emit)

    def setCurrentIndex(self, i):
        b = self._group.button(i)
        if b is not None and not b.isChecked():
            b.setChecked(True)

    def currentIndex(self):
        return self._group.checkedId()


class ElidedLabel(QLabel):
    """A one-line label that shortens its text with "…" instead of clipping it."""
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(40)

    def paintEvent(self, event):
        p = QPainter(self)
        r = self.contentsRect()
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideMiddle, r.width())
        self.style().drawItemText(p, r, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                                  self.palette(), self.isEnabled(), text, QPalette.ColorRole.WindowText)


class DropZone(QFrame):
    """The empty file list: a large drag & drop target that also opens Browse."""
    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dropZone")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(self); lay.setSpacing(6)
        title = QLabel("Drop files or folders here"); title.setObjectName("dropTitle")
        hint = QLabel("or click to choose your downloads folder"); hint.setObjectName("dropHint")
        for lbl in (title, hint):
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter); lbl.setWordWrap(True)
        lay.addStretch(); lay.addWidget(title); lay.addWidget(hint); lay.addStretch()

    def set_hover(self, on):
        self.setProperty("hover", "true" if on else "false")
        self.style().unpolish(self); self.style().polish(self)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class DetailsPanel(QFrame):
    """Where the selected file goes, how sure the match is, and the new name
    of each of its subtitles."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.pal = THEMES["dark"]
        lay = QVBoxLayout(self); lay.setContentsMargins(14, 10, 14, 10); lay.setSpacing(4)
        title = QLabel("DETAILS"); title.setObjectName("paneTitle")
        lay.addWidget(title)
        self.body = QLabel(); self.body.setObjectName("details")
        self.body.setWordWrap(True); self.body.setTextFormat(Qt.TextFormat.RichText)
        self.body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame); scroll.setWidget(self.body)
        lay.addWidget(scroll, 1)
        self.show_candidate(None)

    def show_candidate(self, cand, dest_root=""):
        p, esc = self.pal, html.escape
        muted = lambda t: f"<span style='color:{p['muted']}'>{esc(t)}</span>"
        arrow = f"<span style='color:{p['accent']}'>&nbsp;→&nbsp;</span>"
        if cand is None:
            self.body.setText(muted("Select a row to see where the file and its subtitles will go."))
            return
        info = [f"<b>{esc(cand.source.name)}</b>", muted("from " + str(cand.source.parent))]
        if cand.error:
            info.append(muted("Not renamed: ") + esc(cand.error))
        else:
            dest = str(Path(dest_root) / cand.dest_rel) if dest_root else cand.dest_rel
            info.append(arrow + esc(dest))
            info.append(muted("A file with this name already exists there, so it will be skipped.")
                        if cand.conflict else muted(f"Match confidence {int(cand.confidence * 100)}%"))
        if cand.subtitles:
            subs = [f"<b>Subtitles ({len(cand.subtitles)})</b>"]
            subs += [esc(s.name) + arrow + esc(Path(d).name) for s, d in cand.subtitles]
        elif cand.error:
            subs = []
        else:
            subs = [muted("No subtitles found for this file.")]
        if subs:
            # File on the left, its subtitles on the right
            self.body.setText(
                "<table width='100%' cellspacing='0' cellpadding='0'><tr>"
                f"<td width='52%' valign='top' style='padding-right:18px'>{'<br>'.join(info)}</td>"
                f"<td valign='top'>{'<br>'.join(subs)}</td></tr></table>")
        else:
            self.body.setText("<br>".join(info))


# ═══════════════════════════════════════════════════════════════════
#  MAIN WINDOW
# ═══════════════════════════════════════════════════════════════════
class MainWindow(QMainWindow):
    MEDIA_TYPES = ("tv", "movie", "anime")

    def __init__(self):
        super().__init__()
        self.cfg = cfg_load()
        self.renamer = Renamer(RenameHistory(HISTORY_PATH))
        self._cands = []
        self._thread = self._worker = None
        self._scanning = False
        self._layout_name = None
        self._anims = {}              # running panel animations by kind
        self._replace_rows = {}       # rows being re-matched in place
        self._title_overrides = {}    # str(path) -> (title, id) chosen for that file
        self._episode_overrides = {}  # str(path) -> (season, [episodes]) chosen for that file
        self._shown_once = False
        self._split_main = self._split_preview = None
        self._files_stack = self._preview_stack = None
        self.setWindowTitle("PathRenamer")
        self.setAcceptDrops(True)
        # Window icon: bundled next to the script, or in PyInstaller's
        # temp dir (sys._MEIPASS) when running as the frozen EXE.
        try:
            from PyQt6.QtGui import QIcon
            if getattr(sys, "frozen", False):
                base = Path(sys._MEIPASS)
            else:
                base = Path(__file__).parent
            icon_path = base / "newicon.ico"
            if icon_path.exists():
                self.setWindowIcon(QIcon(str(icon_path)))
                log.info(f"Loaded window icon from: {icon_path}")
        except Exception as e:
            log.debug(f"Could not load window icon: {e}")

        self.status = QStatusBar(); self.setStatusBar(self.status)
        self._build_menu()
        self._create_widgets()
        self._apply_layout()
        self._restore_geometry()
        self.status.showMessage("Ready — select a source folder and click Preview Renames.")
        self._refresh_clients()
        self._update_undo_buttons()        # renames saved from earlier sessions can be undone

        if self.cfg.get("first_run", True):
            self._first_run_wizard()

    def _refresh_clients(self):
        self.tvmaze = TVMazeClient() if self.cfg.get("use_tvmaze", True) else None
        self.anidb  = AniDBClient() if self.cfg.get("use_anidb", True) else None
        key = (self.cfg.get("tmdb_api_key") or "").strip()
        self.tmdb   = TMDBClient(key) if key else None
        self.cinemeta = CinemetaClient() if self.cfg.get("use_cinemeta", True) else None

    def _build_menu(self):
        mb = self.menuBar()
        fm = mb.addMenu("File")
        a = QAction("Settings…", self); a.triggered.connect(self._settings)
        a.setShortcut(QKeySequence("Ctrl+,")); fm.addAction(a)
        fm.addSeparator()
        q = QAction("Quit", self); q.triggered.connect(self.close); fm.addAction(q)

        em = mb.addMenu("Edit")
        self.act_undo = QAction("Undo Last Rename", self)
        self.act_undo.triggered.connect(self._undo); self.act_undo.setEnabled(False)
        self.act_undo.setShortcut(QKeySequence("Ctrl+Z"))
        em.addAction(self.act_undo)
        act_hist = QAction("Rename History…", self)
        act_hist.setShortcut(QKeySequence("Ctrl+H"))
        act_hist.triggered.connect(self._show_history)
        em.addAction(act_hist)

        vm = mb.addMenu("View")
        theme_group = QActionGroup(self); theme_group.setExclusive(True)
        self.act_theme = {}
        for key, label in THEME_NAMES:
            act = QAction(f"{label} Theme", self); act.setCheckable(True)
            act.setChecked(self.cfg.get("theme", "dark") == key)
            act.triggered.connect(lambda _=False, k=key: self._set_theme(k))
            theme_group.addAction(act); vm.addAction(act)
            self.act_theme[key] = act
        act_edit = QAction("Edit Custom Theme…", self)
        act_edit.triggered.connect(self._edit_custom_theme)
        vm.addAction(act_edit)

        self.presets_menu = QMenu("Presets", self)
        self.presets_menu.aboutToShow.connect(self._fill_presets_menu)
        mb.addMenu(self.presets_menu)

        vm.addSeparator()
        lm = vm.addMenu("Layout")
        group = QActionGroup(self); group.setExclusive(True)
        self.act_layout = {}
        for key, label in (("modern", "Modern"), ("classic", "Classic")):
            act = QAction(label, self); act.setCheckable(True)
            act.triggered.connect(lambda _=False, k=key: self._set_layout(k))
            group.addAction(act); lm.addAction(act)
            self.act_layout[key] = act
        self.act_details = QAction("Show Details Panel", self)
        self.act_details.setCheckable(True)
        self.act_details.setChecked(self.cfg.get("show_details", True))
        self.act_details.triggered.connect(self._toggle_details)
        vm.addAction(self.act_details)

    # ── Widgets (made once; each layout arranges them) ───────────────
    def _create_widgets(self):
        remembered = self.cfg.get("last_source_folder","") if self.cfg.get("remember_folders", True) else ""
        self.src = QLineEdit(remembered)
        self.src.setPlaceholderText("Folder containing media files to rename...")
        self.src.textChanged.connect(self._on_src_changed)
        self.btn_browse = QPushButton("Browse…"); self.btn_browse.clicked.connect(self._browse_src)
        self.btn_browse.setToolTip("Choose the folder with your media files (Ctrl+O)")

        self.combo = QComboBox(); self.combo.addItems(["TV Shows","Movies","Anime"])
        saved_type = self.cfg.get("media_type", "tv")
        log.debug(f"Startup: media_type from config = '{saved_type}'")
        self.combo.setCurrentIndex({"tv": 0, "movie": 1, "anime": 2}.get(saved_type, 0))
        self.combo.currentIndexChanged.connect(self._on_media_type_changed)
        self.seg = SegmentedControl(["TV Shows", "Movies", "Anime"])
        self.seg.setCurrentIndex(self.combo.currentIndex())
        self.seg.changed.connect(self.combo.setCurrentIndex)

        self.btn_preview = QPushButton("⟳  Preview Renames")
        self.btn_preview.setObjectName("btn_preview")
        self.btn_preview.setToolTip("Look up the new names (F5)")
        self.btn_preview.clicked.connect(self._preview)

        # Modern: matching mode in the toolbar instead of a question on every preview
        self.match_combo = QComboBox(); self.match_combo.addItems(["Automatic match", "Forced match (TVDB ID)"])
        self.match_combo.setToolTip("Automatic: look the show up by its name.\n"
                                    "Forced: use the show with this TVDB ID for every file.")
        self.tvdb_edit = QLineEdit(); self.tvdb_edit.setPlaceholderText("TVDB ID")
        self.tvdb_edit.setFixedWidth(0)
        self.tvdb_edit.returnPressed.connect(self._preview)
        self.match_combo.currentIndexChanged.connect(lambda _: self._on_match_mode_changed())

        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.file_list.setAlternatingRowColors(False)
        model = self.file_list.model()
        for sig in (model.rowsInserted, model.rowsRemoved, model.modelReset):
            sig.connect(lambda *_: self._update_files_ui())
        self.file_list.itemSelectionChanged.connect(self._update_files_ui)
        self.btn_sel_all = QPushButton("Select All"); self.btn_sel_all.clicked.connect(self.file_list.selectAll)
        self.btn_rem_files = QPushButton("Remove"); self.btn_rem_files.clicked.connect(self._remove_selected_files)
        self.btn_clear_files = QPushButton("Clear List"); self.btn_clear_files.clicked.connect(self._clear_file_list)

        self.pbar = QProgressBar(); self.pbar.setVisible(False)
        self.table = RenameTable()
        self.table.set_display_options(
            self.cfg.get("show_detected", True),
            self.cfg.get("show_full_path", True),
        )
        self.table.set_palette(palette_for(self.cfg))

        self.btn_remove = QPushButton("✕ Remove Selected")
        self.btn_remove.setObjectName("btn_remove")
        self.btn_remove.setEnabled(False)
        self.btn_remove.setToolTip("Leave the selected rows out of this rename (Delete)")
        self.btn_remove.clicked.connect(self._remove_selected_rows)
        self.table.itemSelectionChanged.connect(self._on_table_selection)
        self.btn_undo = QPushButton("↩  Undo Last")
        self.btn_undo.setObjectName("btn_undo"); self.btn_undo.setEnabled(False)
        self.btn_undo.setToolTip("Put the last renamed files back (Ctrl+Z)")
        self.btn_undo.clicked.connect(self._undo)
        # Fixing single rows: right-click menu, double-click, shortcuts, Fix button
        def _action(text, slot, shortcut=None, tip=""):
            act = QAction(text, self)
            act.triggered.connect(slot)
            if shortcut:
                act.setShortcut(QKeySequence(shortcut))
                act.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                self.table.addAction(act)
            if tip:
                act.setToolTip(tip)
            return act
        self.act_edit_name = _action("Edit New Name…", self._edit_name, "F2")
        self.act_change_episode = _action("Change Episode…", self._change_episode, "Ctrl+E")
        self.act_choose_match = _action("Choose a Different Match…", self._choose_match, "Ctrl+M")
        self.act_number = _action("Number in Order…", self._number_in_order, "Ctrl+N")
        self.act_move_up = _action("Move Up", lambda: self._move_rows(-1), "Ctrl+Up")
        self.act_move_down = _action("Move Down", lambda: self._move_rows(1), "Ctrl+Down")
        self.table.customContextMenuRequested.disconnect()
        self.table.customContextMenuRequested.connect(self._table_menu)
        self.table.cellDoubleClicked.connect(self._on_table_double_click)
        self.table.itemSelectionChanged.connect(self._update_fix_actions)
        self.btn_fix = QPushButton("Fix ▾")
        self.btn_fix.setObjectName("flat")
        self.btn_fix.setToolTip("Fix the selected rows: edit the name, change the episode,\n"
                                "choose a different match, reorder or number them in order.\n"
                                "(Also on right-click.)")
        fix_menu = QMenu(self.btn_fix)
        for act in (self.act_edit_name, self.act_change_episode, self.act_choose_match, self.act_number):
            fix_menu.addAction(act)
        fix_menu.addSeparator()
        fix_menu.addAction(self.act_move_up); fix_menu.addAction(self.act_move_down)
        self.btn_fix.setMenu(fix_menu)

        self.btn_rename = QPushButton("✔  Rename Files")
        self.btn_rename.setObjectName("btn_rename"); self.btn_rename.setEnabled(False)
        self.btn_rename.setToolTip("Move the files to their new names (Ctrl+Enter)")
        self.btn_rename.clicked.connect(self._rename)

        # Modern-only pieces
        self.drop_zone = DropZone(); self.drop_zone.clicked.connect(self._browse_src)
        self.details = DetailsPanel()
        self.summary = QLabel(); self.summary.setObjectName("summary")
        self.lib_label = ElidedLabel(); self.lib_label.setObjectName("muted")
        self.btn_presets = QPushButton("Presets ▾"); self.btn_presets.setObjectName("flat")
        self.btn_presets.setToolTip("Saved combinations of naming formats, library folders,\n"
                                    "data sources and how files are placed")
        self.btn_presets.setMenu(self.presets_menu)
        self.btn_change_lib = QPushButton("Change…"); self.btn_change_lib.setObjectName("link")
        self.btn_change_lib.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_change_lib.clicked.connect(self._settings)
        self.files_count = QLabel(); self.files_count.setObjectName("paneCount")
        self.preview_count = QLabel(); self.preview_count.setObjectName("paneCount")
        self.preview_empty = QLabel("Click  ⟳ Preview  to see the new names here.")
        self.preview_empty.setObjectName("emptyHint")
        self.preview_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_empty.setWordWrap(True)

        # Widgets a layout doesn't use wait here (hidden) until it is switched back
        self._parking = QWidget(self); self._parking.hide()
        self._persistent = [
            self.src, self.btn_browse, self.combo, self.seg, self.btn_preview, self.match_combo,
            self.tvdb_edit, self.file_list, self.btn_sel_all, self.btn_rem_files, self.btn_clear_files,
            self.pbar, self.table, self.btn_remove, self.btn_undo, self.btn_rename, self.drop_zone,
            self.details, self.summary, self.lib_label, self.btn_change_lib, self.files_count,
            self.preview_count, self.preview_empty, self.btn_fix, self.btn_presets,
        ]

        # Keyboard shortcuts (all layouts)
        for keys, slot in (("F5", self._preview), ("Ctrl+O", self._browse_src),
                           ("Ctrl+Return", self._rename_if_ready), ("Ctrl+Enter", self._rename_if_ready),
                           ("Delete", self._delete_pressed)):
            QShortcut(QKeySequence(keys), self).activated.connect(slot)

    # ── Layouts ──────────────────────────────────────────────────────
    def _apply_layout(self):
        """Build the window in the layout chosen in Settings, keeping the file
        list, preview and everything typed."""
        name = self.cfg.get("layout", "modern")
        if name not in LAYOUTS:
            name = "modern"
        # The previous layout's frames and splitters are deleted below:
        # stop animations that are still moving them
        self._stop_animation()
        self._split_main = self._split_preview = None
        self._files_stack = self._preview_stack = None
        old = self.takeCentralWidget()
        for w in self._persistent:
            w.setParent(self._parking)
        root = QWidget()
        if name == "modern":
            self._arrange_modern(root)
        else:
            self._arrange_classic(root)
        self.setCentralWidget(root)
        if old is not None:
            old.deleteLater()
        self._layout_name = name
        self.pbar.setVisible(self._scanning)       # setParent() hides widgets
        self._on_match_mode_changed(animate=False)
        self._apply_theme()
        self._update_layout_menu()
        self._update_files_ui()
        self._update_summary()
        self._update_library_label()
        self._update_fix_actions()
        if self.isVisible():
            QTimer.singleShot(0, self._apply_split_sizes)

    def _arrange_classic(self, root):
        """The original PathRenamer layout."""
        self.setMinimumSize(1050, 680)
        for w, name in ((self.src, ""), (self.pbar, ""), (self.btn_sel_all, ""),
                        (self.btn_rem_files, ""), (self.btn_clear_files, "")):
            w.setObjectName(name)
        self.btn_sel_all.setText("Select All"); self.btn_rem_files.setText("Remove")
        self.btn_clear_files.setText("Clear List")
        self.pbar.setTextVisible(True)
        self.table.set_modern(False)
        self.file_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerItem)
        self.file_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.file_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.file_list.setMinimumHeight(120)
        for b in (self.btn_sel_all, self.btn_rem_files, self.btn_clear_files):
            b.setEnabled(True)

        L = QVBoxLayout(root); L.setContentsMargins(12,12,12,12); L.setSpacing(10)
        grp = QGroupBox("Source Folder"); gl = QVBoxLayout(grp)
        sr = QHBoxLayout(); sr.addWidget(QLabel("Folder:"))
        sr.addWidget(self.src, 1); sr.addWidget(self.btn_browse)
        gl.addLayout(sr)

        cr = QHBoxLayout(); cr.addWidget(QLabel("Media Type:"))
        self.combo.setFixedWidth(140); cr.addWidget(self.combo)
        cr.addStretch()
        self.btn_preview.setText("⟳  Preview Renames")
        cr.addWidget(self.btn_preview)
        gl.addLayout(cr)
        L.addWidget(grp)

        file_grp = QGroupBox("Selected Files")
        file_l = QVBoxLayout(file_grp)
        file_l.setContentsMargins(4, 8, 4, 4)
        file_l.addWidget(self.file_list)
        hint = QLabel("Drag & drop files here, or Ctrl/Shift+click to select multiple")
        hint.setStyleSheet("color: #6c7086; font-size: 11px; padding: 2px;")
        file_l.addWidget(hint)
        br_file = QHBoxLayout()
        br_file.addStretch()
        for b in (self.btn_sel_all, self.btn_rem_files, self.btn_clear_files):
            br_file.addWidget(b)
        file_l.addLayout(br_file)
        L.addWidget(file_grp)

        L.addWidget(self.pbar)
        L.addWidget(self.table, 1)

        br = QHBoxLayout(); br.addStretch()
        br.addWidget(self.btn_remove)
        br.addWidget(self.btn_undo)
        self.btn_rename.setFixedWidth(160)
        br.addWidget(self.btn_rename); L.addLayout(br)
        # The Modern layout's stacked panes hide the list and table when they
        # leave them; show everything Classic uses
        for w in (self.src, self.btn_browse, self.combo, self.btn_preview, self.file_list,
                  self.btn_sel_all, self.btn_rem_files, self.btn_clear_files, self.table,
                  self.btn_remove, self.btn_undo, self.btn_rename):
            w.show()

    def _arrange_modern(self, root):
        """Toolbar on top, file list and preview side by side (stacked on narrow
        windows), details under the preview, actions at the bottom."""
        self.setMinimumSize(760, 540)
        root.setObjectName("modernRoot")
        density = self.cfg.get("density", "comfortable")
        self.src.setObjectName("srcEdit")
        self.pbar.setObjectName("slimProgress"); self.pbar.setTextVisible(False)
        for b, text in ((self.btn_sel_all, "Select all"), (self.btn_rem_files, "Remove"),
                        (self.btn_clear_files, "Clear")):
            b.setObjectName("flat"); b.setText(text)
        self.table.set_modern(True, density)
        self.file_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        # Long names are shortened in the middle (full path on hover), no sideways scrolling
        self.file_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.file_list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.file_list.setMinimumHeight(0)
        self.btn_rename.setMinimumWidth(150); self.btn_rename.setMaximumWidth(16777215)

        outer = QVBoxLayout(root); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)

        # Toolbar: folder + Preview, then media type, matching and library folder
        top = QFrame(); top.setObjectName("topBar")
        tl = QVBoxLayout(top); tl.setContentsMargins(16, 12, 16, 10); tl.setSpacing(8)
        r1 = QHBoxLayout(); r1.setSpacing(8)
        r1.addWidget(self.src, 1); r1.addWidget(self.btn_browse)
        self.btn_preview.setText("⟳  Preview")
        self.btn_preview.setMinimumWidth(120)
        r1.addWidget(self.btn_preview)
        tl.addLayout(r1)
        r2 = QHBoxLayout(); r2.setSpacing(8)
        r2.addWidget(self.seg); r2.addSpacing(6)
        r2.addWidget(self.match_combo); r2.addWidget(self.tvdb_edit)
        r2.addSpacing(10)
        r2.addWidget(self.lib_label, 1); r2.addWidget(self.btn_change_lib)
        r2.addSpacing(6); r2.addWidget(self.btn_presets)
        tl.addLayout(r2)
        outer.addWidget(top)
        outer.addWidget(self.pbar)

        body = QWidget(); bl = QVBoxLayout(body); bl.setContentsMargins(14, 12, 14, 10)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False); split.setHandleWidth(10)

        files = QFrame(); files.setObjectName("card"); files.setMinimumSize(290, 150)
        fl = QVBoxLayout(files); fl.setContentsMargins(12, 10, 12, 10); fl.setSpacing(6)
        fh = QHBoxLayout(); fh.setSpacing(4)
        t = QLabel("FILES"); t.setObjectName("paneTitle")
        fh.addWidget(t); fh.addSpacing(4); fh.addWidget(self.files_count); fh.addStretch()
        for b in (self.btn_sel_all, self.btn_rem_files, self.btn_clear_files):
            fh.addWidget(b)
        fl.addLayout(fh)
        self._files_stack = QStackedWidget()
        self._files_stack.addWidget(self.drop_zone); self._files_stack.addWidget(self.file_list)
        fl.addWidget(self._files_stack, 1)
        split.addWidget(files)

        preview = QFrame(); preview.setObjectName("card"); preview.setMinimumSize(360, 150)
        pl = QVBoxLayout(preview); pl.setContentsMargins(12, 10, 12, 10); pl.setSpacing(6)
        ph = QHBoxLayout()
        t2 = QLabel("PREVIEW"); t2.setObjectName("paneTitle")
        ph.addWidget(t2); ph.addSpacing(4); ph.addWidget(self.preview_count); ph.addStretch()
        ph.addWidget(self.btn_fix)
        pl.addLayout(ph)
        self._preview_stack = QStackedWidget()
        self._preview_stack.addWidget(self.preview_empty); self._preview_stack.addWidget(self.table)
        pl.addWidget(self._preview_stack, 1)

        self._split_preview = QSplitter(Qt.Orientation.Vertical)
        self._split_preview.setChildrenCollapsible(False); self._split_preview.setHandleWidth(10)
        self._split_preview.addWidget(preview); self._split_preview.addWidget(self.details)
        self._split_preview.setStretchFactor(0, 1); self._split_preview.setStretchFactor(1, 0)
        self.details.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.details.setVisible(self.cfg.get("show_details", True))
        split.addWidget(self._split_preview)
        split.setStretchFactor(0, 0); split.setStretchFactor(1, 1)
        split.splitterMoved.connect(self._on_split_moved)
        self._split_main = split
        bl.addWidget(split)
        outer.addWidget(body, 1)

        bar = QFrame(); bar.setObjectName("actionBar")
        al = QHBoxLayout(bar); al.setContentsMargins(16, 10, 16, 10); al.setSpacing(8)
        al.addWidget(self.summary, 1)
        al.addWidget(self.btn_remove); al.addWidget(self.btn_undo); al.addWidget(self.btn_rename)
        outer.addWidget(bar)
        for w in (self.src, self.btn_browse, self.btn_preview, self.seg, self.match_combo,
                  self.lib_label, self.btn_change_lib, self.files_count, self.preview_count,
                  self.btn_sel_all, self.btn_rem_files, self.btn_clear_files, self.summary,
                  self.btn_remove, self.btn_undo, self.btn_rename, self.btn_fix, self.btn_presets):
            w.show()
        self._update_panel_orientation()

    def _panel_orientation(self):
        mode = self.cfg.get("panel_mode", "auto")
        if mode == "side":
            return Qt.Orientation.Horizontal
        if mode == "stacked":
            return Qt.Orientation.Vertical
        return Qt.Orientation.Horizontal if self.width() >= 980 else Qt.Orientation.Vertical

    def _update_panel_orientation(self):
        if self._split_main is None:
            return
        orient = self._panel_orientation()
        if self._split_main.orientation() != orient:
            self._split_main.setOrientation(orient)
            QTimer.singleShot(0, self._apply_split_sizes)

    def _apply_split_sizes(self):
        """File list share of the window, and the details panel height."""
        split = self._split_main
        if split is None:
            return
        horizontal = split.orientation() == Qt.Orientation.Horizontal
        total = split.width() if horizontal else split.height()
        if total > 0:
            ratio = min(max(float(self.cfg.get("files_ratio", 0.3)), 0.15), 0.7)
            if not horizontal:
                ratio = min(ratio, 0.35)        # stacked: the preview needs the height
            first = int(total * ratio)
            split.setSizes([first, total - first])
        sp = self._split_preview
        if sp is not None and not self.details.isHidden():
            h = sum(sp.sizes()) or sp.height()
            d = self._details_height(h)
            sp.setSizes([max(h - d, 1), d])

    @staticmethod
    def _details_height(total):
        return min(230, max(130, int(total * 0.36)))

    def _on_split_moved(self, *_):
        split = self._split_main
        sizes = split.sizes() if split is not None else []
        if len(sizes) == 2 and sum(sizes) > 0 and split.orientation() == Qt.Orientation.Horizontal:
            self.cfg["files_ratio"] = round(sizes[0] / sum(sizes), 3)

    def _set_details_visible(self, show, animate=True):
        """Slide the details panel in or out under the preview. A click while
        it is still sliding turns it around from where it is."""
        sp = self._split_preview
        if sp is None:
            return
        animate = animate and self.cfg.get("animations", True) and self.isVisible()
        self._stop_animation("details")
        visible = not self.details.isHidden()
        if not show and not visible:
            return
        sizes = sp.sizes()
        total = sum(sizes) or sp.height()
        current = sizes[1] if visible and len(sizes) > 1 else 0
        # Size policy "Ignored" lets the splitter squeeze the panel below its
        # normal minimum while it slides; restored when done.
        self.details.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Ignored)
        def step(v):
            try:
                sp.setSizes([max(total - v, 1), v])
            except RuntimeError:          # layout replaced meanwhile
                pass
        def restore_policy():
            self.details.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        def done_hide():
            self.details.hide()
            restore_policy()
        if show:
            if not visible:
                self.details.show()
                step(0)
            self._update_details()
            self._run_animation("details", current, self._details_height(total), step,
                                on_done=restore_policy, enabled=animate)
        else:
            self._run_animation("details", current, 0, step, on_done=done_hide, enabled=animate)

    def _run_animation(self, key, start, end, on_value, on_done=None, enabled=True):
        """_animate, first stopping the previous animation of the same kind so
        quick repeated clicks always end in the latest state."""
        self._stop_animation(key)
        anim = _animate(self, start, end, on_value, on_done=on_done, enabled=enabled)
        if anim is not None:
            self._anims[key] = anim
            anim.finished.connect(lambda k=key, a=anim: self._anims.get(k) is a and self._anims.pop(k))

    def _stop_animation(self, key=None):
        """Stop one running animation (or all). A stopped animation does not
        run its "done" step."""
        for k in ([key] if key else list(self._anims)):
            anim = self._anims.pop(k, None)
            if anim is not None:
                try:
                    anim.stop()
                except RuntimeError:      # already finished and deleted
                    pass

    def _toggle_details(self, checked):
        self.cfg["show_details"] = bool(checked)
        cfg_save(self.cfg)
        self._set_details_visible(bool(checked))

    def _set_layout(self, name):
        if name == self._layout_name:
            return
        self.cfg["layout"] = name
        cfg_save(self.cfg)
        self._apply_layout()

    def _update_layout_menu(self):
        for key, act in self.act_layout.items():
            act.setChecked(key == self._layout_name)
        self.act_details.setEnabled(self._layout_name == "modern")
        self.act_details.setChecked(self.cfg.get("show_details", True))

    def _on_match_mode_changed(self, animate=True):
        """Modern: show the TVDB ID box (sliding open) when Forced is chosen."""
        forced = self.match_combo.currentIndex() == 1
        if self._layout_name != "modern":
            return
        animate = animate and self.cfg.get("animations", True) and self.isVisible()
        self._stop_animation("tvdb")
        if forced:
            start = 0 if self.tvdb_edit.isHidden() else self.tvdb_edit.width()
            self.tvdb_edit.show()
            self._run_animation("tvdb", start, 130, self.tvdb_edit.setFixedWidth, enabled=animate)
            if animate:
                self.tvdb_edit.setFocus()
        elif self.tvdb_edit.isHidden():
            self.tvdb_edit.setFixedWidth(0)
        else:
            self._run_animation("tvdb", self.tvdb_edit.width(), 0, self.tvdb_edit.setFixedWidth,
                                on_done=self.tvdb_edit.hide, enabled=animate)

    def _restore_geometry(self):
        geo = self.cfg.get("window_geometry")
        if geo:
            try:
                if self.restoreGeometry(QByteArray.fromBase64(geo.encode("ascii"))):
                    return
            except Exception:
                pass
        if self._layout_name == "modern":
            self.resize(1180, 760)

    def closeEvent(self, event):
        try:
            self.cfg["window_geometry"] = bytes(self.saveGeometry().toBase64()).decode("ascii")
            cfg_save(self.cfg)
        except Exception as e:
            log.debug(f"Could not save window size: {e}")
        super().closeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._shown_once:          # (restoring from minimized keeps the user's sizes)
            self._shown_once = True
            QTimer.singleShot(0, self._apply_split_sizes)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_panel_orientation()

    # ── Live parts of the Modern layout ──────────────────────────────
    def _update_files_ui(self):
        try:
            n = self.file_list.count()
        except RuntimeError:          # window is being destroyed
            return
        sel = len(self.file_list.selectedItems())
        self.files_count.setText(f"{n}" + (f"  ·  {sel} selected" if sel else ""))
        if self._files_stack is not None:
            self._files_stack.setCurrentIndex(1 if n else 0)
        if self._layout_name == "modern":
            self.btn_sel_all.setEnabled(n > 0)
            self.btn_clear_files.setEnabled(n > 0)
            self.btn_rem_files.setEnabled(sel > 0)

    def _update_summary(self):
        cands = self.table.get_cands()
        ok = [c for c in cands if not c.error]
        ready = sum(1 for c in ok if not c.conflict and c.confidence >= 0.85)
        check = sum(1 for c in ok if not c.conflict and c.confidence < 0.85)
        conflicts = sum(1 for c in ok if c.conflict)
        errors = len(cands) - len(ok)
        subs = sum(len(c.subtitles) for c in ok if not c.conflict)
        if not cands:
            text = "Scanning…" if self._scanning else "Add files, then click Preview."
        else:
            parts = [f"{ready} ready"]
            if check:
                parts.append(f"{check} to check")
            if conflicts:
                parts.append(f"{conflicts} conflict{'s' if conflicts != 1 else ''}")
            if errors:
                parts.append(f"{errors} can't be renamed")
            if subs:
                parts.append(f"{subs} subtitle{'s' if subs != 1 else ''}")
            text = "  ·  ".join(parts)
        self.summary.setText(text)
        self.preview_count.setText(str(len(cands)) if cands else "")
        if self._preview_stack is not None:
            self._preview_stack.setCurrentIndex(1 if (cands or self._scanning) else 0)

    def _update_details(self):
        if self._layout_name != "modern" or self.details.isHidden():
            return
        rows = self.table.selected_rows()
        cands = self.table.get_cands()
        cand = cands[rows[-1]] if rows and rows[-1] < len(cands) else None
        self.details.show_candidate(cand, self._get_destination())

    def _update_library_label(self):
        kind = {"tv": "TV shows", "movie": "Movies", "anime": "Anime"}[self._media_type()]
        dest = self._get_destination()
        if dest:
            self.lib_label.setText(f"{kind} go to  {dest}")
            self.btn_change_lib.setText("Change…")
        else:
            self.lib_label.setText(f"No library folder set for {kind.lower()} yet")
            self.btn_change_lib.setText("Set folder…")
        self.lib_label.setToolTip(dest)
        self.lib_label.update()

    def _on_table_selection(self):
        try:
            self.btn_remove.setEnabled(bool(self.table.selectedItems()))
        except RuntimeError:          # window is being destroyed
            return
        self._update_details()

    def _rename_if_ready(self):
        if self.btn_rename.isEnabled():
            self._rename()

    def _delete_pressed(self):
        if self.table.hasFocus() and self.table.selectedItems():
            self._remove_selected_rows()
        elif self.file_list.hasFocus() and self.file_list.selectedItems():
            self._remove_selected_files()

    def _browse_src(self):
        f = QFileDialog.getExistingDirectory(self, "Source Folder", self.src.text())
        if f:
            self.src.setText(f)
            if self.cfg.get("remember_folders", True):
                self.cfg["last_source_folder"] = f
                cfg_save(self.cfg)
            files = scan_folder(f)
            if files:
                self._add_files_to_list(files)
                self.status.showMessage(f"{len(files)} file(s) added to list. Select files and click Preview.")
            else:
                files = scan_folder(f, include_subtitles=True)
                if files:
                    self._add_files_to_list(files)
                    self.status.showMessage(f"{len(files)} subtitle file(s) added (no video files found). Select files and click Preview.")

    def _on_src_changed(self, text):
        if not hasattr(self, "_src_timer"):
            self._src_timer = QTimer()
            self._src_timer.setSingleShot(True)
            self._src_timer.timeout.connect(self._scan_src_folder)
        self._pending_src = text
        self._src_timer.start(500)

    def _scan_src_folder(self):
        text = getattr(self, "_pending_src", "").strip()
        if text and os.path.isdir(text):
            files = scan_folder(text)
            if not files:
                files = scan_folder(text, include_subtitles=True)
            if files:
                self._add_files_to_list(files)
    def _settings(self):
        d = SettingsDialog(self.cfg, self)
        accepted = d.exec()
        if accepted:
            self.cfg = d.cfg
        else:
            # Cancelled: reload from disk. The Format Editor (opened from the
            # Settings dialog) may already have saved template changes, so the
            # in-memory copy can't simply be kept.
            self.cfg = cfg_load()
        self._refresh_clients()
        saved_type = self.cfg.get("media_type", "tv")
        idx = {"tv": 0, "movie": 1, "anime": 2}.get(saved_type, 0)
        if self.combo.currentIndex() != idx:
            self.combo.setCurrentIndex(idx)
        self.table.set_display_options(
            self.cfg.get("show_detected", True),
            self.cfg.get("show_full_path", True),
        )
        if self.cfg.get("layout", "modern") != self._layout_name:
            self._apply_layout()
        else:
            self._apply_layout_options()
        self._update_theme_menu()

    def _apply_layout_options(self):
        """Settings changed without switching layout: density, file list
        position, details panel, theme, library folder."""
        if self._layout_name == "modern":
            self.table.set_modern(True, self.cfg.get("density", "comfortable"))
            self._update_panel_orientation()
            self._set_details_visible(self.cfg.get("show_details", True))
        self._apply_theme()
        self._update_layout_menu()
        self._update_library_label()
        self._update_summary()

    def _set_theme(self, theme):
        if theme == "custom" and not self.cfg.get("custom_theme"):
            return self._edit_custom_theme()     # nothing to show yet: make it first
        self.cfg["theme"] = theme
        cfg_save(self.cfg)
        self._apply_theme()
        self._update_theme_menu()

    def _edit_custom_theme(self):
        dlg = CustomThemeDialog(self.cfg, self, preview=self._apply_theme)
        if dlg.exec():
            self.cfg["custom_theme"] = dlg.result_theme()
            self.cfg["theme"] = "custom"
            cfg_save(self.cfg)
        self._apply_theme()
        self._update_theme_menu()

    def _apply_theme(self, pal=None):
        """Style the window with the chosen theme (or with pal, e.g. while the
        Custom theme is being edited)."""
        pal = pal or palette_for(self.cfg)
        self.setStyleSheet(app_stylesheet(pal, self.cfg.get("density", "comfortable")))
        self.table.set_palette(pal)
        self.details.pal = pal
        self._update_details()

    def _update_theme_menu(self):
        theme = self.cfg.get("theme", "dark")
        for key, act in self.act_theme.items():
            act.setChecked(key == theme)

    def _on_media_type_changed(self):
        self.cfg["media_type"] = self._media_type()
        cfg_save(self.cfg)
        self.seg.setCurrentIndex(self.combo.currentIndex())
        self._update_library_label()

    def _get_destination(self):
        mtype = self._media_type()
        log.debug(f"_get_destination: media_type='{mtype}'")
        if mtype == "tv":
            root = self.cfg.get("root_tv", "")
            log.debug(f"_get_destination: root_tv='{root}'")
            return root
        elif mtype == "movie":
            root = self.cfg.get("root_movie", "")
            log.debug(f"_get_destination: root_movie='{root}'")
            return root
        else:
            root = self.cfg.get("root_anime", "")
            log.debug(f"_get_destination: root_anime='{root}'")
            return root

    def _first_run_wizard(self):
        QMessageBox.information(self, "Welcome to PathRenamer",
            "Welcome! Let's set up your library folders.\n\n"
            "Click OK to open the Settings window where you can configure your TV Shows, Movies, and Anime root folders.")
        self._settings()

    def _media_type(self): return ["tv","movie","anime"][self.combo.currentIndex()]

    def _detect_media_type_from_paths(self, files):
        """Guess media type from folder names in the files' paths."""
        joined = " ".join(str(f).lower() for f in files[:20])
        if re.search(r"\banime\b", joined):
            return "anime"
        if re.search(r"\b(movies?|films?)\b", joined):
            return "movie"
        if re.search(r"\b(tv|shows?|series)\b", joined):
            return "tv"
        return None

    def _choose_match_mode(self):
        """("normal" | "forced", TVDB id or None), or (None, None) if cancelled.
        Classic asks in two dialogs; Modern reads the toolbar."""
        if self._layout_name == "modern":
            if self.match_combo.currentIndex() == 1:
                tvdb_id = self.tvdb_edit.text().strip()
                if not tvdb_id:
                    QMessageBox.information(self, "Forced Match",
                                            "Type the show's TVDB ID in the box next to “Forced match”.")
                    self.tvdb_edit.setFocus()
                    return None, None
                return "forced", tvdb_id
            return "normal", None
        match_type, ok = QInputDialog.getItem(
            self, "Match Type", "Select matching mode:",
            ["Normal Match (automatic)", "Forced Match (enter TVDB ID)"], 0, False
        )
        if not ok:
            return None, None
        if "Forced" in match_type:
            forced_id, ok = QInputDialog.getText(self, "Forced Match", "Enter TVDB ID for the show:")
            if not ok or not forced_id.strip():
                return None, None
            return "forced", forced_id.strip()
        return "normal", None

    def _preview(self):
        if self._scanning:
            return
        dst = self._get_destination()
        # Prefer files selected in the list; fall back to the source folder.
        selected_paths = self._get_selected_files()

        if selected_paths:
            # Drop entries whose files were moved/deleted since they were added.
            missing = [p for p in selected_paths if not p.exists()]
            if missing:
                self._purge_files_from_list(missing)
                selected_paths = [p for p in selected_paths if p.exists()]
            files = selected_paths
            log.info(f"Using {len(files)} selected file(s) from list")
            if not files:
                return self.status.showMessage("Selected files no longer exist — list cleaned up.")
            self.status.showMessage(f"Processing {len(files)} selected file(s)…")
        elif self.src.text().strip():
            src = self.src.text().strip()
            files = scan_folder(src)
            log.info(f"Found {len(files)} media file(s) in {src!r}")
            if not files:
                files = scan_folder(src, include_subtitles=True)
                if not files:
                    return self.status.showMessage("No media files found.")
                self.status.showMessage(f"Scanning {len(files)} subtitle file(s)…")
            else:
                self.status.showMessage(f"Scanning {len(files)} file(s)…")
        else:
            return QMessageBox.warning(self, "PathRenamer", "Please either drop files, select files from the list, or choose a source folder.")

        # Optionally switch the media type based on folder names.
        if self.cfg.get("auto_detect_type", False):
            detected = self._detect_media_type_from_paths(files)
            if detected and detected != self._media_type():
                idx = {"tv": 0, "movie": 1, "anime": 2}[detected]
                self.combo.blockSignals(True)
                self.combo.setCurrentIndex(idx)
                self.combo.blockSignals(False)
                self.seg.setCurrentIndex(idx)
                self.cfg["media_type"] = detected
                cfg_save(self.cfg)
                self._update_library_label()
                self.status.showMessage(f"Auto-detected media type: {detected}")
                dst = self._get_destination()

        if not dst:
            return QMessageBox.warning(self, "PathRenamer", "Set destination folder in Settings (tab: Folders).")

        # Matching mode: automatic, or one show forced by its TVDB id
        match_mode, forced_id = self._choose_match_mode()
        if match_mode is None:
            return

        # str(path) -> (title, (source, id)) chosen by the user.
        selected_overrides = {}
        media_type = self._media_type()

        engine = MatchEngine(
            cfg=self.cfg,
            tvmaze=self.tvmaze,
            anidb=self.anidb,
            tmdb=self.tmdb,
            cinemeta=self.cinemeta,
        )

        forced_match = None
        if match_mode == "forced":
            if self.tvmaze:
                try:
                    forced_match = self.tvmaze.get_show_by_tvdb_id(forced_id)
                    if forced_match:
                        log.info(f"Forced match found: {forced_match.get('show')} (TVDB ID: {forced_id})")
                    else:
                        log.warning(f"No show found for TVDB ID: {forced_id}")
                        QMessageBox.warning(self, "Not Found", f"No show found for TVDB ID: {forced_id}")
                        return
                except Exception as e:
                    log.error(f"Failed to lookup TVDB ID {forced_id}: {e}")
                    QMessageBox.warning(self, "Error", f"Failed to lookup TVDB ID: {e}")
                    return
            else:
                QMessageBox.warning(self, "Error", "TVMaze not available for forced match")
                return

            if forced_match:
                for f in files:
                    selected_overrides[str(f)] = (forced_match.get("show", "Unknown"),
                                                  ("tvmaze", forced_match.get("id")))

        else:
            # Normal match: group the files by detected title and let the user
            # confirm the match once per group.
            groups = {}
            for f in files:
                groups.setdefault(derive_title_for(f, media_type), []).append(f)
            log.info(f"Detected {len(groups)} title group(s): {list(groups)}")
            for title, group_files in groups.items():
                matches = engine.search_all_matches(group_files[0], media_type)
                if not matches:
                    continue
                dlg = MatchSelectorDialog(f"{title} — {len(group_files)} file(s)", matches, self)
                if dlg.exec() and dlg.selected_match:
                    selected = dlg.selected_match
                    selected_title = selected.get("show", selected.get("title", ""))
                    selected_id = selected.get("id")
                    selected_src = selected.get("src", "tvmaze")
                    log.info(f"Selected for group '{title}': '{selected_title}' ({selected_src} ID: {selected_id})")
                    for ff in group_files:
                        selected_overrides[str(ff)] = (selected_title, (selected_src, selected_id))

        # Per-file choices start fresh with every preview
        self._title_overrides = dict(selected_overrides)
        self._episode_overrides = {}
        self._start_scan(files, engine, media_type, dst, selected_overrides)

    def _engine(self):
        return MatchEngine(cfg=self.cfg, tvmaze=self.tvmaze, anidb=self.anidb,
                           tmdb=self.tmdb, cinemeta=self.cinemeta)

    def _start_scan(self, files, engine, media_type, dst, selected_overrides=None,
                    episode_overrides=None, replace=None):
        """Match files in a worker thread. replace: {normcase path: row} to
        re-match those rows in place (fixing single rows) instead of a new preview."""
        self._replace_rows = dict(replace or {})
        if not replace:
            self._cands = []
            self.table.clear_all()
        self.table.set_display_options(
            self.cfg.get("show_detected", True),
            self.cfg.get("show_full_path", True),
        )
        self.btn_rename.setEnabled(False)

        self._scanning = True
        self.pbar.setVisible(True)
        self.pbar.setMaximum(len(files))
        self.pbar.setValue(0)
        self.btn_preview.setEnabled(False)
        self._update_summary()
        self._update_details()
        self._update_fix_actions()

        self._thread = QThread()
        self._worker = ScanWorker(files, engine, media_type, dst, selected_overrides, episode_overrides)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(lambda c, t: self.pbar.setValue(c))
        self._worker.candidate_ready.connect(self._on_candidate)
        self._worker.finished.connect(self._on_scan_done)
        self._thread.start()

    def _on_candidate(self, cand):
        row = self._replace_rows.pop(os.path.normcase(str(cand.source)), None) if self._replace_rows else None
        if row is not None and row < self.table.rowCount():
            self.table.replace_candidate(row, cand, self._get_destination())
        else:
            self.table.append_candidate(cand, self._get_destination())
        self._cands = self.table.get_cands()
        self._update_summary()

    def _on_scan_done(self):
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(2000)
        self._scanning = False
        self._replace_rows = {}
        self.pbar.setVisible(False)
        self.btn_preview.setEnabled(True)
        mark_duplicates(self.table.get_cands(), self._get_destination())
        self.table.refresh_rows()
        self._cands = self.table.get_cands()
        self._update_summary()
        self._update_fix_actions()
        if self.table.rowCount() and not self.table.selectedItems() and self._layout_name == "modern":
            self.table.selectRow(0)       # fills the details panel

        total = len(self._cands)
        conflicts = self.table.conflict_count()
        errors = self.table.error_count()

        parts = [f"{total} file(s) matched"]
        if conflicts:
            parts.append(f"{conflicts} conflict(s) shown in red")
        if errors:
            parts.append(f"{errors} error(s)")
        self.status.showMessage("  ·  ".join(parts))

        can_rename = total > 0 and (total - errors) > 0
        self.btn_rename.setEnabled(can_rename)

    ACTION_WORDS = {  # rename action -> (verb, past tense)
        "move": ("Move", "Renamed"), "copy": ("Copy", "Copied"), "hardlink": ("Hard-link", "Hard-linked"),
    }

    def _rename_groups(self, cands, dst):
        """(video op, [subtitle ops]) per renameable candidate, so subtitles follow their video."""
        groups = []
        for cand in cands:
            if cand.error:
                continue
            main_op = (cand.source, Path(dst) / cand.dest_rel)
            sub_ops = [(s, Path(dst) / rel) for s, rel in cand.subtitles]
            groups.append((main_op, sub_ops))
        return groups

    def _rename(self):
        dst = self._get_destination()
        if not dst:
            QMessageBox.warning(self, "PathRenamer", "No destination folder set.")
            return

        groups = self._rename_groups(self._cands, dst)
        if not groups:
            QMessageBox.warning(self, "PathRenamer", "No files to rename.")
            return

        # A video whose destination already exists is skipped together with
        # its subtitles. Subtitles are checked on their own below, so one
        # existing subtitle doesn't block the video.
        conflicted = [g for g in groups if detect_conflicts([g[0]])]
        if conflicted:
            if self.cfg.get("confirm_conflicts", True):
                reply = QMessageBox.question(
                    self, "Conflicts Detected",
                    f"{len(conflicted)} destination file(s) already exist and will be skipped.\n"
                    "Continue with the remaining files?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
            skip = {id(g) for g in conflicted}
            groups = [g for g in groups if id(g) not in skip]
        if not groups:
            self.status.showMessage("Nothing to rename after skipping conflicts.")
            return

        action = self.cfg.get("rename_action", "move")
        if self.cfg.get("confirm_rename", True):
            n_vids = len(groups)
            n_subs = sum(1 for _, sub_ops in groups for s in sub_ops if not detect_conflicts([s]))
            msg = f"{self.ACTION_WORDS.get(action, self.ACTION_WORDS['move'])[0]} {n_vids} file(s)"
            if n_subs:
                msg += f" and {n_subs} subtitle(s)"
            msg += f" into:\n{dst}\n\nProceed?"
            reply = QMessageBox.question(
                self, "Confirm Rename", msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        result, msg = self._place_groups(groups, dst, source="manual")
        self.status.showMessage(msg)
        if result.failed:
            details = "\n".join(f"{o.src.name}: {o.error}" for o in result.failed[:10])
            QMessageBox.warning(self, "Some Renames Failed", details)

        self._cands = []
        self.table.clear_all()
        self.btn_rename.setEnabled(False)
        self._update_summary()
        self._update_details()

    def _place_groups(self, groups, dst, source="manual"):
        """Rename (move / copy / hard-link) the groups and clean up.
        Returns (result, status message)."""
        # A subtitle whose new name already exists stays where it is
        sub_skipped = sum(1 for _, sub_ops in groups for s in sub_ops if detect_conflicts([s]))
        groups = [(main_op, [s for s in sub_ops if not detect_conflicts([s])])
                  for main_op, sub_ops in groups]
        action = self.cfg.get("rename_action", "move")
        if action not in RENAME_ACTIONS:
            action = "move"
        result = self.renamer.execute_groups(groups, dest_root=dst, action=action, source=source)
        ok_subs = sum(1 for op in result.succeeded if op.src.suffix.lower() in SUB_EXT)
        ok_files = len(result.succeeded) - ok_subs
        fallbacks = sum(1 for op in result.succeeded if op.note)

        # Remove folders left empty in the source tree (only moving empties them)
        if action == "move" and self.cfg.get("cleanup_empty_folders", True):
            root = self.src.text().strip()
            if root and os.path.isdir(root):
                for op in result.succeeded:
                    if _is_inside(op.src, Path(root)):
                        _cleanup_empty_dirs(op.src.parent, stop=Path(root))

        msg = f"{self.ACTION_WORDS.get(action, self.ACTION_WORDS['move'])[1]} {ok_files} file(s)"
        if ok_subs:
            msg += f" and {ok_subs} subtitle(s)"
        if result.failed:
            msg += f", {len(result.failed)} failed"
        if fallbacks:
            msg += f" · {fallbacks} copied instead (hard links only work on the same drive)"
        if sub_skipped:
            msg += f" · {sub_skipped} subtitle(s) left in place (new name already exists)"

        # Placed files are done; drop them from the list
        self._purge_files_from_list([op.src for op in result.succeeded])
        self._update_undo_buttons()
        return result, msg

    def _undo(self):
        if not self.renamer.can_undo():
            return
        result = self.renamer.undo_last()
        self._report_undo(result)

    def _report_undo(self, result):
        ok_count = len(result.succeeded)
        fail_count = len(result.failed)
        self.status.showMessage(
            f"Undo complete — {ok_count} file(s) restored." +
            (f" {fail_count} failed." if fail_count else "")
        )
        if fail_count:
            details = "\n".join(f"{o.src.name}: {o.error}" for o in result.failed[:10])
            QMessageBox.warning(self, "Some Undos Failed", details)
        self._update_undo_buttons()

    def _show_history(self):
        dlg = HistoryDialog(self.renamer, self)
        dlg.exec()
        if dlg.undo_results:
            self._report_undo(dlg.undo_results[-1])
        self._update_undo_buttons()

    def _update_undo_buttons(self):
        enabled = self.renamer.can_undo()
        self.btn_undo.setEnabled(enabled)
        self.act_undo.setEnabled(enabled)

    def _remove_selected_rows(self):
        self.table.remove_selected()
        self._cands = self.table.get_cands()
        errors = sum(1 for c in self._cands if c.error)
        self.btn_rename.setEnabled(len(self._cands) - errors > 0)
        self._update_summary()
        self._update_details()

    def _purge_files_from_list(self, paths):
        gone = {os.path.normcase(str(p)) for p in paths}
        for i in range(self.file_list.count() - 1, -1, -1):
            item_path = self.file_list.item(i).data(256)
            if item_path is not None and os.path.normcase(str(item_path)) in gone:
                self.file_list.takeItem(i)

    # ── File list ───────────────────────────────────────────────────
    def _add_files_to_list(self, paths):
        existing = {self.file_list.item(i).data(256) for i in range(self.file_list.count())}
        for p in paths:
            if p not in existing:
                item = QListWidgetItem(p.name)
                item.setData(256, p)
                item.setToolTip(str(p))
                self.file_list.addItem(item)

    def _get_selected_files(self):
        selected = self.file_list.selectedItems()
        if selected:
            return [item.data(256) for item in selected]
        return [self.file_list.item(i).data(256) for i in range(self.file_list.count())]

    def _clear_file_list(self):
        self.file_list.clear()
        self._cands = []
        self.table.clear_all()
        self.btn_rename.setEnabled(False)
        self._update_summary()
        self._update_details()

    def _remove_selected_files(self):
        for item in self.file_list.selectedItems():
            self.file_list.takeItem(self.file_list.row(item))

    # ── Presets ──────────────────────────────────────────────────────
    def _fill_presets_menu(self):
        menu = self.presets_menu
        menu.clear()
        presets = self.cfg.get("presets") or {}
        if presets:
            for name in sorted(presets, key=str.lower):
                act = menu.addAction(f"Use “{name}”")
                act.triggered.connect(lambda _=False, n=name: self._apply_preset(n))
        else:
            none = menu.addAction("No presets yet")
            none.setEnabled(False)
        menu.addSeparator()
        menu.addAction("Save Current Settings as Preset…").triggered.connect(self._save_preset)
        if presets:
            delete = menu.addMenu("Delete Preset")
            for name in sorted(presets, key=str.lower):
                delete.addAction(name).triggered.connect(lambda _=False, n=name: self._delete_preset(n))

    def _save_preset(self, name=None):
        if name is None:
            name, ok = QInputDialog.getText(
                self, "Save Preset",
                "Name for these settings (naming formats, library folders, data sources,\n"
                "media type and how files are placed):", QLineEdit.EchoMode.Normal, "")
            name = name.strip()
            if not ok or not name:
                return
            if name in (self.cfg.get("presets") or {}):
                reply = QMessageBox.question(self, "Save Preset", f"Replace the preset “{name}”?",
                                             QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if reply != QMessageBox.StandardButton.Yes:
                    return
        self.cfg.setdefault("presets", {})[name] = {k: self.cfg.get(k, DEFAULTS.get(k)) for k in PRESET_KEYS}
        cfg_save(self.cfg)
        self.status.showMessage(f"Saved preset “{name}”.")

    def _apply_preset(self, name):
        preset = (self.cfg.get("presets") or {}).get(name)
        if not preset:
            return
        for key in PRESET_KEYS:
            if key in preset:
                self.cfg[key] = preset[key]
        cfg_save(self.cfg)
        self._refresh_clients()
        idx = {"tv": 0, "movie": 1, "anime": 2}.get(self.cfg.get("media_type"), 0)
        if self.combo.currentIndex() != idx:
            self.combo.setCurrentIndex(idx)
        self._update_library_label()
        msg = f"Using preset “{name}”."
        if self.table.rowCount():
            msg += " Click Preview to see the names with it."
        self.status.showMessage(msg)

    def _delete_preset(self, name):
        reply = QMessageBox.question(self, "Delete Preset", f"Delete the preset “{name}”?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            (self.cfg.get("presets") or {}).pop(name, None)
            cfg_save(self.cfg)

    # ── Fixing single rows ───────────────────────────────────────────
    def _rows_for_action(self):
        """Selected rows, top to bottom (none while a preview is running)."""
        if self._scanning:
            self.status.showMessage("Wait for the preview to finish first.")
            return []
        return sorted(self.table.selected_rows())

    def _update_fix_actions(self):
        try:
            cands = self.table.get_cands()
            rows = sorted(self.table.selected_rows())
        except RuntimeError:              # window is being destroyed
            return
        has = bool(rows) and not self._scanning
        episodic = has and all(cands[r].media_type != "movie" for r in rows if r < len(cands))
        for act, ok in ((self.act_edit_name, has and len(rows) == 1),
                        (self.act_change_episode, episodic and len(rows) == 1),
                        (self.act_choose_match, has),
                        (self.act_number, episodic),
                        (self.act_move_up, has and rows[0] > 0),
                        (self.act_move_down, has and rows[-1] < len(cands) - 1)):
            act.setEnabled(bool(ok))
        self.btn_fix.setEnabled(has)

    def _table_menu(self, pos):
        menu = QMenu(self)
        for act in (self.act_edit_name, self.act_change_episode, self.act_choose_match, self.act_number):
            menu.addAction(act)
        menu.addSeparator()
        menu.addAction(self.act_move_up); menu.addAction(self.act_move_down)
        menu.addSeparator()
        rm = QAction("Remove From List", menu); rm.triggered.connect(self._remove_selected_rows)
        rm.setEnabled(bool(self.table.selectedItems()) and not self._scanning)
        menu.addAction(rm)
        cp = QAction("Copy", menu); cp.triggered.connect(self.table._copy_selection)
        menu.addAction(cp)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _on_table_double_click(self, row, col):
        if col == 1:
            self._choose_match()
        elif col != STATUS_COL:
            self._edit_name()

    def _edit_name(self):
        rows = self._rows_for_action()
        if len(rows) != 1:
            return
        row = rows[0]
        cand = self.table.get_cands()[row]
        text, ok = QInputDialog.getText(
            self, "Edit New Name",
            "New name inside your library folder (use / between folders):",
            QLineEdit.EchoMode.Normal, cand.dest_rel)
        if not ok or not text.strip():
            return
        rel = _normalize_rel(text.strip().replace("\\", "/"))
        if not rel:
            return
        ext = cand.source.suffix
        if ext and not rel.lower().endswith(ext.lower()):
            rel += ext
        new = dataclasses.replace(cand, dest_rel=rel, error="", conflict=False, confidence=1.0,
                                  manual=True, subtitles=[])
        if cand.source.suffix.lower() not in SUB_EXT:
            self._engine()._subs(cand.source, rel, new)       # subtitles follow the new name
        dest = Path(self._get_destination()) / rel
        new.conflict = dest.exists() and not _same_path(dest, cand.source)
        self.table.replace_candidate(row, new, self._get_destination())
        self._after_rows_changed()

    def _change_episode(self):
        rows = self._rows_for_action()
        if len(rows) != 1:
            return
        cand = self.table.get_cands()[rows[0]]
        eps = cand.episodes or []
        dlg = EpisodeNumbersDialog(self, title=cand.title, season=cand.season if cand.season is not None else 1,
                                   first=eps[0] if eps else 1, last=eps[-1] if eps else None)
        if dlg.exec():
            title, season, episodes = dlg.values()
            self._rematch(rows, title=title, season=season, episodes=episodes)

    def _number_in_order(self):
        rows = self._rows_for_action()
        if not rows:
            return
        first = self.table.get_cands()[rows[0]]
        eps = first.episodes or []
        dlg = EpisodeNumbersDialog(self, title=first.title, season=first.season if first.season is not None else 1,
                                   first=eps[0] if eps else 1, count=len(rows), number_mode=True)
        if dlg.exec():
            title, season, episodes = dlg.values()
            self._rematch(rows, title=title, season=season, episodes=episodes)

    def _choose_match(self):
        rows = self._rows_for_action()
        if not rows:
            return
        cands = self.table.get_cands()
        first = cands[rows[0]]
        media_type = self._media_type()
        start = first.title or derive_title_for(first.source, media_type)
        title, ok = QInputDialog.getText(self, "Choose a Different Match",
                                         "Search for the show or movie:", QLineEdit.EchoMode.Normal, start)
        if not ok or not title.strip():
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            matches = self._engine().search_title(title.strip(), media_type)
        finally:
            QApplication.restoreOverrideCursor()
        if not matches:
            QMessageBox.information(self, "Choose a Different Match", f"Nothing found for “{title.strip()}”.")
            return
        dlg = MatchSelectorDialog(f"{title.strip()} — {len(rows)} file(s)", matches, self)
        if dlg.exec() and dlg.selected_match:
            sel = dlg.selected_match
            chosen = (sel.get("show", sel.get("title", "")), (sel.get("src", "tvmaze"), sel.get("id")))
            self._rematch(rows, match=chosen)

    def _rematch(self, rows, title=None, match=None, season=None, episodes=None):
        """Look the given rows up again with the user's choices, in place.
        title: a show name typed by the user; match: (title, (source, id)) picked
        in the match chooser; episodes: one list of episode numbers per row."""
        cands = self.table.get_cands()
        paths = [cands[r].source for r in rows]
        for i, (row, path) in enumerate(zip(rows, paths)):
            key = str(path)
            if match:
                self._title_overrides[key] = match
            elif title and title != cands[row].title:
                self._title_overrides[key] = (title, None)
            if episodes:
                self._episode_overrides[key] = (season, episodes[min(i, len(episodes) - 1)])
        sel = {str(p): self._title_overrides[str(p)] for p in paths if str(p) in self._title_overrides}
        eps = {str(p): self._episode_overrides[str(p)] for p in paths if str(p) in self._episode_overrides}
        replace = {os.path.normcase(str(p)): r for p, r in zip(paths, rows)}
        self._start_scan(paths, self._engine(), self._media_type(), self._get_destination(),
                         sel, eps, replace=replace)

    def _move_rows(self, delta):
        rows = self._rows_for_action()
        if rows:
            self.table.move_rows(rows, delta)
            self._after_rows_changed()

    def _after_rows_changed(self):
        mark_duplicates(self.table.get_cands(), self._get_destination())
        self.table.refresh_rows()
        self._cands = self.table.get_cands()
        self.btn_rename.setEnabled(any(not c.error for c in self._cands))
        self._update_summary()
        self._update_details()
        self._update_fix_actions()

    # ── Drag & drop ─────────────────────────────────────────────────
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.drop_zone.set_hover(True)

    def dragLeaveEvent(self, event):
        self.drop_zone.set_hover(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self.drop_zone.set_hover(False)
        files = []
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            if path.is_file():
                # Videos and subtitles only, not .nfo/.txt/.jpg dropped with them
                if path.suffix.lower() in VIDEO_EXT or path.suffix.lower() in SUB_EXT:
                    files.append(path)
            elif path.is_dir():
                folder_files = scan_folder(str(path))
                if not folder_files:
                    folder_files = scan_folder(str(path), include_subtitles=True)
                files.extend(folder_files)
        if files:
            self._add_files_to_list(files)
            self.status.showMessage(f"{len(files)} file(s) added. Select files and click Preview.")


# ═══════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("PathRenamer")
    app.setApplicationVersion(APP_VERSION)
    w = MainWindow(); w.show()
    sys.exit(app.exec())
