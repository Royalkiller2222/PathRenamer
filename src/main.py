import sys, os, json, re, shutil, gzip, time, xml.etree.ElementTree as ET, logging, traceback, html, functools
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple, Any

APP_VERSION = "1.3.0"

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


import requests
import re
from parser import analyse_path
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
                          QEasingCurve, QByteArray)

# ═══════════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════════
CONFIG_PATH = _APP_DIR / "config.json"
DEFAULTS = {
    "tmdb_api_key": "",
    "last_source_folder": "",
    "root_tv": "",
    "root_movie": "",
    "root_anime": "",
    "media_type": "tv",
    "format_tv": "{show} ({year})/Season {s00}/{show} {s00e00} - {title}",
    "format_movie": "{title} ({year})/{title} ({year}) {res} {vc} {ach} {c}",
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
}

LAYOUTS = ("modern", "classic")
PANEL_MODES = ("auto", "side", "stacked")
DENSITIES = ("comfortable", "compact")

# Previous default movie templates. cfg_load() upgrades configs that still
# use one of them to the current default.
_OLD_MOVIE_DEFAULTS = {
    "{title} ({year})/{title} ({year})",
    "{title} ({year})/{title} ({year}).{ext}",
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
        show, video_show = t[0], tag[0]
        if not show:                     # "Subs/S01E02.eng.srt"
            return in_sub_dir
        return bool(video_show) and (video_show.startswith(show) or show.startswith(video_show))

    def belongs(stem, in_sub_dir):
        if _sub_matches_video(stem, vstem):
            # "Movie.Extended.en.srt" belongs to "Movie.Extended.mkv", not "Movie.mkv"
            return not any(len(o) > len(vstem) and _sub_matches_video(stem, o) for o in others)
        if not ((only_video and (in_sub_dir or is_language_only_name(stem)))
                or same_episode(stem, in_sub_dir)):
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
            if belongs(nd.strip(), True):
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

def fmt_movie(title, year, ext, template, video_codec="", audio_codec="", audio_channels="", media_source="", resolution=""):
    if not template or not template.strip():
        template = "{title} ({year})/{title} ({year}) {res} {vc} {ach} {c}"
    if isinstance(year, list):
        year = year[0] if year else None


    # No year: leave it out ("Title" rather than "Title (Unknown)", which Plex
    # would read as part of the title); _normalize_rel drops the empty "()".
    y = str(year) if year else ""

    ctx = {
        "title": sanitize(title),
        "year": y,
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

class Renamer:
    def __init__(self): self._history = []

    @staticmethod
    def _move(src, dst):
        op = RenameOp(src=src, dst=dst)
        try:
            if not src.exists():
                raise FileNotFoundError(f"Source no longer exists: {src}")
            if dst.exists() and not _same_path(dst, src):
                raise FileExistsError(f"Exists: {dst}")
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            op.success = True
        except Exception as e:
            op.error = str(e)
        return op

    def execute(self, ops, dest_root=None):
        """Move each (src, dst) pair on its own; one undo step for all."""
        return self.execute_groups([(op, []) for op in ops], dest_root)

    def execute_groups(self, groups, dest_root=None):
        """groups: [((video src, dst), [(subtitle src, dst), ...]), ...].
        A video's subtitles are moved only if the video was, so a failed move
        never leaves its subtitles alone in the library. One undo step for all."""
        result, batch = RenameResult(), []
        for (src, dst), sub_ops in groups:
            op = self._move(src, dst)
            result.ops.append(op)
            if op.success:
                batch.append(op)
            for s, d in sub_ops:
                if not op.success:
                    result.ops.append(RenameOp(src=s, dst=d, error="Skipped: its video was not moved"))
                    continue
                sop = self._move(s, d)
                result.ops.append(sop)
                if sop.success:
                    batch.append(sop)
        if batch: self._history.append((batch, dest_root))
        return result
    def can_undo(self): return bool(self._history)
    def undo_last(self):
        if not self._history: return RenameResult()
        batch, dest_root = self._history.pop()
        result = RenameResult()
        for op in reversed(batch):
            undo = RenameOp(src=op.dst, dst=op.src)
            try:
                if not op.dst.exists():
                    raise FileNotFoundError(f"File not found: {op.dst}")
                if op.src.exists():
                    raise FileExistsError(f"Original location occupied: {op.src}")
                op.src.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(op.dst), str(op.src))
                undo.success = True
                if dest_root:
                    _cleanup_empty_dirs(op.dst.parent, stop=Path(dest_root))
            except Exception as e:
                undo.error = str(e)
            result.ops.append(undo)
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
            title = guess.title or "Unknown"
            year = guess.year

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
            log.exception(f"search_all_matches failed for {path.name}: {e}")
            return []

    @staticmethod
    def _scalar(raw, key, default=""):
        """guessit may return a scalar or a list; always return a scalar."""
        v = raw.get(key, default)
        if isinstance(v, list):
            v = v[0] if v else default
        return v

    def match_with_override(self, path, media_type, selected_title=None, selected_id=None):
        try:
            ext = path.suffix.lower()
            # Title, year, season, episode(s) / air date from the file name and
            # its folders. The file's own SxxEyy always wins; a folder's only
            # fills in what the file name lacks (e.g. obfuscated file names).
            guess = analyse_path(path, media_type)
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
                return self._movie_with_title(path, title, guess.year, ext, video_codec, audio_codec, audio_channels, media_source, resolution, selected_id)

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
        c = Candidate(path, rel, "tv", conf, video_codec=video_codec, audio_codec=audio_codec, audio_channels=audio_channels, media_source=media_source, resolution=resolution)
        if ext not in SUB_EXT:
            self._subs(path, rel, c)
        return c

    def _movie_with_title(self, path, title, year, ext, video_codec="", audio_codec="", audio_channels="", media_source="", resolution="", selected_id=None):
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
        rel = fmt_movie(title, year, ext, self.cfg.get("format_movie",""), video_codec, audio_codec, audio_channels, media_source, resolution)
        c = Candidate(path, rel, "movie", conf, video_codec=video_codec, audio_codec=audio_codec, audio_channels=audio_channels, media_source=media_source, resolution=resolution)
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
        c = Candidate(path, rel, "anime", conf, video_codec=video_codec, audio_codec=audio_codec, audio_channels=audio_channels, media_source=media_source, resolution=resolution)
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
#  THEMES
# ═══════════════════════════════════════════════════════════════════
# Dark (green accents), Midnight (blue accents), Light (indigo accents)
DARK = """
QMainWindow,QWidget{background:#0b100b;color:#c2dcc2;font-family:'Segoe UI',Arial,sans-serif;font-size:13px}
QGroupBox{border:1px solid #234023;border-radius:6px;margin-top:8px;padding-top:8px;font-weight:bold;color:#4ade80}
QGroupBox::title{subcontrol-origin:margin;left:10px;padding:0 4px}
QLineEdit,QComboBox{background:#131b13;border:1px solid #234023;border-radius:4px;padding:5px 8px;color:#c2dcc2}
QLineEdit:focus,QComboBox:focus{border:1px solid #4ade80}
QComboBox::drop-down{border:none}
QTextEdit{background:#131b13;border:1px solid #234023;color:#c2dcc2}
QPushButton{background:#162116;border:1px solid #2a4d2a;border-radius:5px;padding:6px 16px;color:#c2dcc2;font-weight:bold}
QPushButton:hover{background:#1e2e1e}
QPushButton:pressed{background:#274027}
QPushButton:disabled{color:#4a5f4a;background:#0f150f}
QPushButton#btn_preview{background:#0e2416;border-color:#4ade80;color:#4ade80}
QPushButton#btn_preview:hover{background:#123120}
QPushButton#btn_rename{background:#123c1e;border-color:#86efac;color:#86efac}
QPushButton#btn_rename:hover{background:#174d27}
QPushButton#btn_rename:disabled{background:#0d1f12;color:#3f5f4a;border-color:#1e3a26}
QPushButton#btn_remove{background:#3d1414;border-color:#f87171;color:#f87171}
QPushButton#btn_remove:hover{background:#4e1a1a}
QPushButton#btn_remove:disabled{background:#241010;color:#6a3a3a;border-color:#3a1a1a}
QPushButton#btn_undo{background:#3a2a10;border-color:#fbbf24;color:#fbbf24}
QPushButton#btn_undo:hover{background:#4a3615}
QPushButton#btn_undo:disabled{background:#241d0a;color:#6a5a2a;border-color:#3a2f14}
QStatusBar{background:#080c08;color:#7ba37b;border-top:1px solid #1a281a}
QProgressBar{border:1px solid #234023;border-radius:4px;background:#131b13;text-align:center;color:#c2dcc2;height:16px}
QProgressBar::chunk{background:#4ade80;border-radius:3px}
QMenuBar{background:#080c08;color:#c2dcc2}
QMenuBar::item:selected{background:#162116}
QMenu{background:#0b100b;border:1px solid #234023}
QMenu::item:selected{background:#162116}
QTableWidget{gridline-color:#182418;border:1px solid #182418}
QHeaderView::section{background:#0b100b;color:#c2dcc2;padding:6px;border:none;border-bottom:2px solid #234023;font-weight:bold}
QTableWidget::item{padding:4px 8px}
QListWidget{background:#0b100b;border:1px solid #234023;border-radius:4px}
QListWidget::item{padding:6px 8px;border-radius:2px;color:#c2dcc2}
QListWidget::item:selected{background:#162116;color:#c2dcc2}
QListWidget::item:hover{background:#101710}
QLabel#preview_label{background:#131b13;border:1px solid #234023;border-radius:4px;padding:8px;font-size:13px}
"""

# Midnight
MIDNIGHT = """
QMainWindow,QWidget{background:#060a12;color:#bcd0f0;font-family:'Segoe UI',Arial,sans-serif;font-size:13px}
QGroupBox{border:1px solid #1b2b4a;border-radius:6px;margin-top:8px;padding-top:8px;font-weight:bold;color:#4d9fff}
QGroupBox::title{subcontrol-origin:margin;left:10px;padding:0 4px}
QLineEdit,QComboBox{background:#0d1524;border:1px solid #1b2b4a;border-radius:4px;padding:5px 8px;color:#bcd0f0}
QLineEdit:focus,QComboBox:focus{border:1px solid #4d9fff}
QComboBox::drop-down{border:none}
QTextEdit{background:#0d1524;border:1px solid #1b2b4a;color:#bcd0f0}
QPushButton{background:#101a2e;border:1px solid #22355c;border-radius:5px;padding:6px 16px;color:#bcd0f0;font-weight:bold}
QPushButton:hover{background:#16233d}
QPushButton:pressed{background:#1d2f52}
QPushButton:disabled{color:#44577e;background:#0a1120}
QPushButton#btn_preview{background:#0d2138;border-color:#4d9fff;color:#4d9fff}
QPushButton#btn_preview:hover{background:#122c4a}
QPushButton#btn_rename{background:#0f2f1e;border-color:#3fb950;color:#3fb950}
QPushButton#btn_rename:hover{background:#143d28}
QPushButton#btn_rename:disabled{background:#0b1f16;color:#2d5a3f;border-color:#1a3a2a}
QPushButton#btn_remove{background:#331116;border-color:#f85149;color:#f85149}
QPushButton#btn_remove:hover{background:#43161d}
QPushButton#btn_remove:disabled{background:#220e12;color:#6a3038;border-color:#3a1a20}
QPushButton#btn_undo{background:#332508;border-color:#d29922;color:#d29922}
QPushButton#btn_undo:hover{background:#43310c}
QPushButton#btn_undo:disabled{background:#221a08;color:#6a5424;border-color:#3a2e12}
QStatusBar{background:#04070d;color:#5d739e;border-top:1px solid #131e33}
QProgressBar{border:1px solid #1b2b4a;border-radius:4px;background:#0d1524;text-align:center;color:#bcd0f0;height:16px}
QProgressBar::chunk{background:#4d9fff;border-radius:3px}
QMenuBar{background:#04070d;color:#bcd0f0}
QMenuBar::item:selected{background:#101a2e}
QMenu{background:#060a12;border:1px solid #1b2b4a}
QMenu::item:selected{background:#101a2e}
QTableWidget{gridline-color:#111c30;border:1px solid #111c30}
QHeaderView::section{background:#060a12;color:#bcd0f0;padding:6px;border:none;border-bottom:2px solid #1b2b4a;font-weight:bold}
QTableWidget::item{padding:4px 8px}
QListWidget{background:#060a12;border:1px solid #1b2b4a;border-radius:4px}
QListWidget::item{padding:6px 8px;border-radius:2px;color:#bcd0f0}
QListWidget::item:selected{background:#101a2e;color:#bcd0f0}
QListWidget::item:hover{background:#0b1220}
QLabel#preview_label{background:#0d1524;border:1px solid #1b2b4a;border-radius:4px;padding:8px;font-size:13px}
"""

# Light
LIGHT = """
QMainWindow,QWidget{background:#faf9f7;color:#262421;font-family:'Segoe UI',Arial,sans-serif;font-size:13px}
QGroupBox{border:1px solid #ddd8d0;border-radius:6px;margin-top:8px;padding-top:8px;font-weight:bold;color:#5b5bd6}
QGroupBox::title{subcontrol-origin:margin;left:10px;padding:0 4px}
QLineEdit,QComboBox{background:#ffffff;border:1px solid #ddd8d0;border-radius:4px;padding:5px 8px;color:#262421}
QLineEdit:focus,QComboBox:focus{border:1px solid #5b5bd6}
QComboBox::drop-down{border:none}
QTextEdit{background:#ffffff;border:1px solid #ddd8d0;color:#262421}
QPushButton{background:#f1efec;border:1px solid #ddd8d0;border-radius:5px;padding:6px 16px;color:#262421;font-weight:bold}
QPushButton:hover{background:#e9e6e1}
QPushButton:pressed{background:#ddd8d0}
QPushButton:disabled{color:#a8a29a;background:#f1efec}
QPushButton#btn_preview{background:#eceafd;border-color:#5b5bd6;color:#4a3fc4}
QPushButton#btn_preview:hover{background:#dedafb}
QPushButton#btn_rename{background:#e3f6e6;border-color:#1a7f37;color:#177a33}
QPushButton#btn_rename:hover{background:#d0efd6}
QPushButton#btn_rename:disabled{background:#eef7f0;color:#8fbf9c;border-color:#c4e2cc}
QPushButton#btn_remove{background:#fdecea;border-color:#c2352b;color:#c2352b}
QPushButton#btn_remove:hover{background:#fbdcd8}
QPushButton#btn_remove:disabled{background:#fbf1f0;color:#dc9a94;border-color:#efccc8}
QPushButton#btn_undo{background:#fdf4d7;border-color:#96660a;color:#96660a}
QPushButton#btn_undo:hover{background:#fbecba}
QPushButton#btn_undo:disabled{background:#fbf6e6;color:#c9ab6e;border-color:#ecdcb4}
QStatusBar{background:#f1efec;color:#706a5f;border-top:1px solid #ddd8d0}
QProgressBar{border:1px solid #ddd8d0;border-radius:4px;background:#ffffff;text-align:center;color:#262421;height:16px}
QProgressBar::chunk{background:#5b5bd6;border-radius:3px}
QMenuBar{background:#f1efec;color:#262421}
QMenuBar::item:selected{background:#e9e6e1}
QMenu{background:#faf9f7;border:1px solid #ddd8d0}
QMenu::item:selected{background:#e9e6e1}
QTableWidget{gridline-color:#e5e1da;border:1px solid #e5e1da}
QHeaderView::section{background:#faf9f7;color:#262421;padding:6px;border:none;border-bottom:2px solid #ddd8d0;font-weight:bold}
QTableWidget::item{padding:4px 8px}
QListWidget{background:#ffffff;border:1px solid #ddd8d0;border-radius:4px}
QListWidget::item{padding:6px 8px;border-radius:2px;color:#262421}
QListWidget::item:selected{background:#e9e6e1;color:#262421}
QListWidget::item:hover{background:#f1efec}
QLabel#preview_label{background:#f1efec;border:1px solid #ddd8d0;border-radius:4px;padding:8px;font-size:13px}
"""

THEMES = {"dark": DARK, "midnight": MIDNIGHT, "light": LIGHT}

# Each theme's colours (all taken from the stylesheets above), for the
# Modern layout and the details panel. Layouts change shapes and spacing,
# never colours.
PALETTES = {
    "dark": dict(bg="#0b100b", surface="#131b13", border="#234023", text="#c2dcc2",
                 accent="#4ade80", button="#162116", button_border="#2a4d2a", hover="#1e2e1e",
                 pressed="#274027", bar="#080c08", muted="#7ba37b", grid="#182418",
                 disabled="#4a5f4a", soft="#101710"),
    "midnight": dict(bg="#060a12", surface="#0d1524", border="#1b2b4a", text="#bcd0f0",
                     accent="#4d9fff", button="#101a2e", button_border="#22355c", hover="#16233d",
                     pressed="#1d2f52", bar="#04070d", muted="#5d739e", grid="#111c30",
                     disabled="#44577e", soft="#0b1220"),
    "light": dict(bg="#faf9f7", surface="#ffffff", border="#ddd8d0", text="#262421",
                  accent="#5b5bd6", button="#f1efec", button_border="#ddd8d0", hover="#e9e6e1",
                  pressed="#ddd8d0", bar="#f1efec", muted="#706a5f", grid="#e5e1da",
                  disabled="#a8a29a", soft="#f1efec"),
}

# Rules for the Modern layout and the sidebar Settings window. Every rule is
# scoped to #modernRoot / #settingsModern, so the Classic layout is untouched.
_MODERN_QSS = """
QWidget#modernRoot{background:%(bg)s}
#modernRoot QFrame#topBar{background:%(bar)s;border:none;border-bottom:1px solid %(border)s}
#modernRoot QFrame#card{background:%(bg)s;border:1px solid %(border)s;border-radius:10px}
#modernRoot QFrame#actionBar{background:%(bg)s;border:none;border-top:1px solid %(border)s}
#modernRoot QLabel#paneTitle{color:%(accent)s;font-weight:bold;font-size:12px;letter-spacing:1px;background:transparent}
#modernRoot QLabel#paneCount,#modernRoot QLabel#muted{color:%(muted)s;background:transparent}
#modernRoot QLabel#summary{color:%(muted)s;background:transparent;padding-left:2px}
#modernRoot QLineEdit#srcEdit{padding:7px 10px;border-radius:8px}
#modernRoot QComboBox{border-radius:8px;padding:6px 10px}
#modernRoot QPushButton{border-radius:8px;padding:7px 16px}
#modernRoot QPushButton#flat{background:transparent;border:1px solid transparent;color:%(muted)s;padding:4px 8px;font-weight:normal}
#modernRoot QPushButton#flat:hover{background:%(hover)s;border-color:%(button_border)s;color:%(text)s}
#modernRoot QPushButton#flat:disabled{color:%(disabled)s}
#modernRoot QPushButton#link{background:transparent;border:none;color:%(accent)s;padding:0 4px;font-weight:normal;text-decoration:underline}
#modernRoot QPushButton#seg{padding:7px 14px}
#modernRoot QPushButton#seg:checked{background:%(pressed)s;color:%(accent)s;border-color:%(accent)s}
#modernRoot QFrame#dropZone{background:%(bg)s;border:2px dashed %(border)s;border-radius:10px}
#modernRoot QFrame#dropZone[hover="true"]{border-color:%(accent)s;background:%(soft)s}
#modernRoot QLabel#dropTitle{color:%(text)s;font-size:15px;font-weight:bold;background:transparent}
#modernRoot QLabel#dropHint{color:%(muted)s;background:transparent}
#modernRoot QLabel#emptyHint{color:%(muted)s;background:transparent;font-size:13px}
#modernRoot QListWidget{border:none;background:%(bg)s;border-radius:6px}
#modernRoot QListWidget::item{padding:%(list_pad)s;border-radius:6px;margin:1px 0}
#modernRoot QTableWidget{border:none;background:%(bg)s}
#modernRoot QHeaderView::section{background:%(bg)s}
#modernRoot QLabel#details{background:transparent;color:%(text)s}
#modernRoot QSplitter::handle{background:transparent}
#modernRoot QSplitter::handle:hover{background:%(border)s}
#modernRoot QProgressBar#slimProgress{border:none;border-radius:2px;background:%(surface)s;max-height:4px;min-height:4px}
#modernRoot QProgressBar#slimProgress::chunk{background:%(accent)s;border-radius:2px}
#modernRoot QScrollBar:vertical{background:transparent;width:10px;margin:2px}
#modernRoot QScrollBar:horizontal{background:transparent;height:10px;margin:2px}
#modernRoot QScrollBar::handle:vertical{background:%(border)s;border-radius:3px;min-height:28px}
#modernRoot QScrollBar::handle:horizontal{background:%(border)s;border-radius:3px;min-width:28px}
#modernRoot QScrollBar::handle:hover{background:%(button_border)s}
#modernRoot QScrollBar::add-line,#modernRoot QScrollBar::sub-line{width:0;height:0;border:none;background:none}
#modernRoot QScrollBar::add-page,#modernRoot QScrollBar::sub-page{background:none}
QDialog#settingsModern QListWidget#settingsNav{background:%(bar)s;border:none;border-right:1px solid %(border)s;padding:8px 6px;outline:0}
QDialog#settingsModern QListWidget#settingsNav::item{padding:9px 14px;border-radius:8px;margin:1px 0;color:%(text)s}
QDialog#settingsModern QListWidget#settingsNav::item:selected{background:%(pressed)s;color:%(accent)s}
QDialog#settingsModern QListWidget#settingsNav::item:hover:!selected{background:%(hover)s}
QDialog#settingsModern QGroupBox{border-radius:10px;margin-top:12px;padding-top:12px}
QDialog#settingsModern QPushButton{border-radius:8px}
QDialog#settingsModern QLineEdit,QDialog#settingsModern QComboBox{border-radius:8px}
"""

def modern_qss(theme, density="comfortable"):
    pal = dict(PALETTES.get(theme, PALETTES["dark"]))
    pal["list_pad"] = "3px 8px" if density == "compact" else "7px 10px"
    return _MODERN_QSS % pal

def app_stylesheet(theme, density="comfortable"):
    """The theme's stylesheet plus the (scoped) Modern rules."""
    return THEMES.get(theme, DARK) + modern_qss(theme, density)

def tab_style_for(theme):
    """QTabWidget/QTabBar styling matched to each theme's palette."""
    if theme == "midnight":
        return """
            QTabWidget::pane { border: 1px solid #1b2b4a; background: #060a12; }
            QTabBar::tab { background: #0d1524; color: #bcd0f0; padding: 8px 16px; border: 1px solid #1b2b4a; border-bottom: none; }
            QTabBar::tab:selected { background: #060a12; color: #4d9fff; }
            QTabBar::tab:hover:!selected { background: #16233d; }
        """
    elif theme == "light":
        return """
            QTabWidget::pane { border: 1px solid #ddd8d0; background: #faf9f7; }
            QTabBar::tab { background: #f1efec; color: #262421; padding: 8px 16px; border: 1px solid #ddd8d0; border-bottom: none; }
            QTabBar::tab:selected { background: #faf9f7; color: #5b5bd6; }
            QTabBar::tab:hover:!selected { background: #e9e6e1; }
        """
    else:
        return """
            QTabWidget::pane { border: 1px solid #234023; background: #0b100b; }
            QTabBar::tab { background: #162116; color: #c2dcc2; padding: 8px 16px; border: 1px solid #234023; border-bottom: none; }
            QTabBar::tab:selected { background: #0b100b; color: #4ade80; }
            QTabBar::tab:hover:!selected { background: #1e2e1e; }
        """

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
            tabs.setStyleSheet(self._get_tab_style(theme))
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
        return tab_style_for(theme)

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
        self.fmt_movie.setPlaceholderText("{title} ({year})/{title} ({year}) {res} {vc} {ach} {c}")
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
        self.theme_combo.addItems(["Dark", "Midnight", "Light"])
        theme_map = {"dark": 0, "midnight": 1, "light": 2}
        self.theme_combo.setCurrentIndex(theme_map.get(self.cfg.get("theme", "dark"), 0))
        gl.addRow("Color Scheme:", self.theme_combo)

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
        theme_map = {0: "dark", 1: "midnight", 2: "light"}
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
            "theme": theme_map[self.theme_combo.currentIndex()],
            "layout": LAYOUTS[self.layout_combo.currentIndex()],
            "panel_mode": PANEL_MODES[self.panel_combo.currentIndex()],
            "density": DENSITIES[self.density_combo.currentIndex()],
            "show_details": self.show_details_chk.isChecked(),
            "animations": self.animations_chk.isChecked(),
            "show_full_path": self.show_new_path.isChecked(),
            "show_detected": self.show_detected.isChecked(),
            "rename_subtitles": self.rename_subs.isChecked(),
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
ROW_COLORS_DARK = {
    "ok":       (QColor("#1d4023"), QColor("#a8f0a8")),
    "conflict": (QColor("#4a1d1d"), QColor("#ff9090")),
    "low":      (QColor("#3d3517"), QColor("#f0d890")),
    "error":    (QColor("#33203d"), QColor("#cc80ff")),
}
ROW_COLORS_LIGHT = {
    "ok":       (QColor("#e3f6e6"), QColor("#14662b")),
    "conflict": (QColor("#fdecea"), QColor("#a72a20")),
    "low":      (QColor("#fdf4d7"), QColor("#7a5605")),
    "error":    (QColor("#f3e8fd"), QColor("#6d28d9")),
}
COLUMNS     = ["Original Filename","Detected Match","New Path","Subtitles","Status"]
STATUS_COL  = 4

class _ElideDelegate(QStyledItemDelegate):
    """Cut long text at a chosen end: paths keep their (informative) end."""
    def __init__(self, mode, parent=None):
        super().__init__(parent)
        self._mode = mode
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        option.textElideMode = self._mode

class RenameTable(QTableWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._cands = []
        self._dest_root = ""
        self._show_detected = True
        self._show_full_path = True
        self._modern = False
        self._elide_middle = _ElideDelegate(Qt.TextElideMode.ElideMiddle, self)
        self._elide_left = _ElideDelegate(Qt.TextElideMode.ElideLeft, self)
        self._row_colors = ROW_COLORS_DARK
        self.setColumnCount(len(COLUMNS))
        self.setHorizontalHeaderLabels(COLUMNS)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.verticalHeader().setVisible(False)
        hh = self.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hh.setStretchLastSection(True)

        self.cellClicked.connect(self._on_cell_clicked)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._build_context_menu)

    def set_display_options(self, show_detected=True, show_full_path=True):
        self._show_detected = show_detected
        self._show_full_path = show_full_path
        self.setColumnHidden(1, not show_detected)

    def set_modern(self, modern: bool, density: str = "comfortable"):
        """Modern layout: one line per row (no wrapping), long paths cut at the
        start, smooth scrolling, subtitle counts instead of names. Classic
        keeps the original wrapped rows."""
        self._modern = modern
        hh, vh = self.horizontalHeader(), self.verticalHeader()
        if modern:
            self.setWordWrap(False)
            self.setShowGrid(False)
            self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
            self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
            self.setItemDelegateForColumn(0, self._elide_middle)
            self.setItemDelegateForColumn(2, self._elide_left)
            vh.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
            vh.setDefaultSectionSize(26 if density == "compact" else 34)
            hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
            hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
            hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self.horizontalHeaderItem(STATUS_COL).setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.setColumnWidth(1, 200)
        else:
            self.setWordWrap(True)
            self.setShowGrid(True)
            self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerItem)
            self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerItem)
            for col in (0, 2):
                self.setItemDelegateForColumn(col, None)
            vh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
            vh.setDefaultSectionSize(30)
            hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
            hh.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        if self._cands:
            self.populate(self._cands, self._dest_root)

    def set_light_mode(self, light: bool):
        self._row_colors = ROW_COLORS_LIGHT if light else ROW_COLORS_DARK
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
        if not self._modern:
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

    def __init__(self, files, engine, media_type, dest_root, selected_overrides=None):
        super().__init__()
        self.files = files; self.engine = engine
        self.media_type = media_type; self.dest_root = dest_root
        self.selected_overrides = selected_overrides or {}
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

                cand = self.engine.match_with_override(f, self.media_type, selected_title, selected_id)

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
                        cand.error = f"Same new name as {owner.name}"
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

        self.movie_template = QLineEdit(self.cfg.get("format_movie", "{title} ({year})/{title} ({year}) {res} {vc} {ach} {c}"))
        self.movie_template.setPlaceholderText("{title} ({year})/{title} ({year}) {res} {vc} {ach} {c}")
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

        accent = {"dark": "#4ade80", "midnight": "#4d9fff", "light": "#5b5bd6"}.get(
            self.cfg.get("theme", "dark"), "#4ade80")
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
        template = self.movie_template.text() or "{title} ({year})/{title} ({year}) {res} {vc} {ach} {c}"
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

    def _get_tab_style(self, theme):
        return tab_style_for(theme)

    def _apply_theme(self):
        theme_name = self.cfg.get("theme", "dark")
        self.setStyleSheet(THEMES.get(theme_name, DARK) + self._get_tab_style(theme_name))


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
        self.pal = PALETTES["dark"]
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
        self.renamer = Renamer()
        self._cands = []
        self._thread = self._worker = None
        self._scanning = False
        self._layout_name = None
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

        vm = mb.addMenu("View")
        self.act_theme_dark = QAction("Dark Theme", self)
        self.act_theme_dark.setCheckable(True)
        self.act_theme_dark.setChecked(self.cfg.get("theme", "dark") == "dark")
        self.act_theme_dark.triggered.connect(lambda: self._set_theme("dark"))
        vm.addAction(self.act_theme_dark)

        self.act_theme_midnight = QAction("Midnight Theme", self)
        self.act_theme_midnight.setCheckable(True)
        self.act_theme_midnight.setChecked(self.cfg.get("theme", "dark") == "midnight")
        self.act_theme_midnight.triggered.connect(lambda: self._set_theme("midnight"))
        vm.addAction(self.act_theme_midnight)

        self.act_theme_light = QAction("Light Theme", self)
        self.act_theme_light.setCheckable(True)
        self.act_theme_light.setChecked(self.cfg.get("theme", "dark") == "light")
        self.act_theme_light.triggered.connect(lambda: self._set_theme("light"))
        vm.addAction(self.act_theme_light)

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
        self.table.set_light_mode(self.cfg.get("theme", "dark") == "light")

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
        self.btn_rename = QPushButton("✔  Rename Files")
        self.btn_rename.setObjectName("btn_rename"); self.btn_rename.setEnabled(False)
        self.btn_rename.setToolTip("Move the files to their new names (Ctrl+Enter)")
        self.btn_rename.clicked.connect(self._rename)

        # Modern-only pieces
        self.drop_zone = DropZone(); self.drop_zone.clicked.connect(self._browse_src)
        self.details = DetailsPanel()
        self.summary = QLabel(); self.summary.setObjectName("summary")
        self.lib_label = ElidedLabel(); self.lib_label.setObjectName("muted")
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
            self.preview_count, self.preview_empty,
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
        # The previous layout's frames and splitters are deleted below
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
        pl.addLayout(ph)
        self._preview_stack = QStackedWidget()
        self._preview_stack.addWidget(self.preview_empty); self._preview_stack.addWidget(self.table)
        pl.addWidget(self._preview_stack, 1)

        self._split_preview = QSplitter(Qt.Orientation.Vertical)
        self._split_preview.setChildrenCollapsible(False); self._split_preview.setHandleWidth(10)
        self._split_preview.addWidget(preview); self._split_preview.addWidget(self.details)
        self._split_preview.setStretchFactor(0, 1); self._split_preview.setStretchFactor(1, 0)
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
        """Slide the details panel in or out under the preview."""
        sp = self._split_preview
        if sp is None:
            return
        animate = animate and self.cfg.get("animations", True) and self.isVisible()
        if show == (not self.details.isHidden()):
            return
        total = sum(sp.sizes()) or sp.height()
        target = self._details_height(total)
        # Size policy "Ignored" lets the splitter squeeze the panel below its
        # normal minimum while it slides; restored when done.
        self.details.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Ignored)
        def step(v):
            sp.setSizes([max(total - v, 1), v])
        def done_show():
            self.details.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        def done_hide():
            self.details.hide()
            self.details.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        if show:
            self.details.show()
            self._update_details()
            step(0)
            _animate(self, 0, target, step, on_done=done_show, enabled=animate)
        else:
            current = sp.sizes()[1] if len(sp.sizes()) > 1 else 0
            _animate(self, current, 0, step, on_done=done_hide, enabled=animate)

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
        if forced:
            start = 0 if self.tvdb_edit.isHidden() else self.tvdb_edit.width()
            self.tvdb_edit.show()
            _animate(self, start, 130, self.tvdb_edit.setFixedWidth, enabled=animate)
            if animate:
                self.tvdb_edit.setFocus()
        elif self.tvdb_edit.isHidden():
            self.tvdb_edit.setFixedWidth(0)
        else:
            _animate(self, self.tvdb_edit.width(), 0, self.tvdb_edit.setFixedWidth,
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
            QTimer.singleShot(0, self._apply_split_sizes)
        self._apply_theme()
        self._update_layout_menu()
        self._update_library_label()
        self._update_summary()

    def _set_theme(self, theme):
        self.cfg["theme"] = theme
        cfg_save(self.cfg)
        self._apply_theme()
        self._update_theme_menu()

    def _apply_theme(self):
        theme = self.cfg.get("theme", "dark")
        self.setStyleSheet(app_stylesheet(theme, self.cfg.get("density", "comfortable")))
        self.table.set_light_mode(theme == "light")
        self.details.pal = PALETTES.get(theme, PALETTES["dark"])
        self._update_details()

    def _update_theme_menu(self):
        theme = self.cfg.get("theme", "dark")
        self.act_theme_dark.setChecked(theme == "dark")
        self.act_theme_midnight.setChecked(theme == "midnight")
        self.act_theme_light.setChecked(theme == "light")

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

        # Reset the table and run the matcher in a worker thread.
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

        self._thread = QThread()
        self._worker = ScanWorker(files, engine, media_type, dst, selected_overrides)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(lambda c, t: self.pbar.setValue(c))
        self._worker.candidate_ready.connect(self._on_candidate)
        self._worker.finished.connect(self._on_scan_done)
        self._thread.start()

    def _on_candidate(self, cand):
        self._cands.append(cand)
        self.table.append_candidate(cand, self._get_destination())
        self._update_summary()

    def _on_scan_done(self):
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(2000)
        self._scanning = False
        self.pbar.setVisible(False)
        self.btn_preview.setEnabled(True)
        self._update_summary()
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

    def _rename(self):
        dst = self._get_destination()
        if not dst:
            QMessageBox.warning(self, "PathRenamer", "No destination folder set.")
            return

        # Each group is (video op, [subtitle ops]) so subtitles follow their video.
        groups = []
        for cand in self._cands:
            if cand.error:
                continue
            main_op = (cand.source, Path(dst) / cand.dest_rel)
            sub_ops = [(s, Path(dst) / rel) for s, rel in cand.subtitles]
            groups.append((main_op, sub_ops))

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

        # A subtitle whose new name already exists stays where it is
        sub_skipped = sum(1 for _, sub_ops in groups for s in sub_ops if detect_conflicts([s]))
        groups = [(main_op, [s for s in sub_ops if not detect_conflicts([s])])
                  for main_op, sub_ops in groups]

        if not groups:
            self.status.showMessage("Nothing to rename after skipping conflicts.")
            return

        if self.cfg.get("confirm_rename", True):
            n_vids = len(groups)
            n_subs = sum(len(sub_ops) for _, sub_ops in groups)
            msg = f"Move {n_vids} file(s)"
            if n_subs:
                msg += f" and {n_subs} subtitle(s)"
            msg += f" into:\n{dst}\n\nProceed?"
            reply = QMessageBox.question(
                self, "Confirm Rename", msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        result = self.renamer.execute_groups(groups, dest_root=dst)
        ok_count = len(result.succeeded)
        fail_count = len(result.failed)
        ok_subs = sum(1 for op in result.succeeded if op.src.suffix.lower() in SUB_EXT)

        # Remove folders left empty in the source tree.
        if self.cfg.get("cleanup_empty_folders", True):
            src_root = self.src.text().strip()
            if src_root and os.path.isdir(src_root):
                for op in result.succeeded:
                    _cleanup_empty_dirs(op.src.parent, stop=Path(src_root))

        msg = f"Renamed {ok_count - ok_subs} file(s)"
        if ok_subs:
            msg += f" and {ok_subs} subtitle(s)"
        if fail_count:
            msg += f", {fail_count} failed"
        if sub_skipped:
            msg += f" · {sub_skipped} subtitle(s) left in place (new name already exists)"
        self.status.showMessage(msg)
        if fail_count:
            details = "\n".join(f"{o.src.name}: {o.error}" for o in result.failed[:10])
            QMessageBox.warning(self, "Some Renames Failed", details)

        # Moved files are gone from their old location; drop them from the list.
        moved = [op.src for op in result.succeeded]
        self._purge_files_from_list(moved)

        self._cands = []
        self.table.clear_all()
        self.btn_rename.setEnabled(False)
        self._update_undo_buttons()
        self._update_summary()
        self._update_details()

    def _undo(self):
        if not self.renamer.can_undo():
            return
        result = self.renamer.undo_last()
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
