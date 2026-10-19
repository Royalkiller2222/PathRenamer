import os
import re
from pathlib import Path
from typing import List, Optional, Tuple


def _dbg(msg):
    pass

VIDEO_EXTENSIONS = {
    ".mkv", ".mp4", ".avi", ".mov", ".wmv", ".m4v",
    ".ts", ".m2ts", ".flv", ".webm", ".mpg", ".mpeg",
}
SUBTITLE_EXTENSIONS = {
    ".srt", ".sub", ".ass", ".ssa", ".vtt", ".idx",
}

SUBTITLE_FOLDER_NAMES = {
    "Subs", "subs", "Subtitles", "subtitle", "sub",
    "captions", "VobSub", "VOBSUB", "English", "字幕",
}

LANG_MAP = {
    "eng": "eng", "english": "eng",
    "en": "en", "enus": "en",
    "spa": "spa", "spanish": "spa", "es": "spa",
    "fre": "fre", "french": "fre", "fr": "fre",
    "ger": "ger", "german": "ger", "de": "ger",
    "ita": "ita", "italian": "ita", "it": "ita",
    "por": "por", "portuguese": "por", "pt": "por",
    "dut": "dut", "dutch": "dut", "nl": "dut",
    "rus": "rus", "russian": "rus", "ru": "rus",
    "jpn": "jpn", "japanese": "jpn", "ja": "jpn",
    "kor": "kor", "korean": "kor", "ko": "kor",
    "chi": "chi", "chinese": "chi", "zh": "chi",
    "ara": "ara", "arabic": "ara", "ar": "ara",
    "hin": "hin", "hindi": "hin",
    "tur": "tur", "turkish": "tur", "tr": "tur",
    "pol": "pol", "polish": "pol", "pl": "pol",
    "swe": "swe", "swedish": "swe", "sv": "swe",
    "dan": "dan", "danish": "dan", "da": "dan",
    "nor": "nor", "norwegian": "nor", "no": "nor",
    "fin": "fin", "finnish": "fin", "fi": "fin",
    "hun": "hun", "hungarian": "hun", "hu": "hun",
    "cze": "cze", "czech": "cze", "cs": "cze",
    "rum": "rum", "romanian": "rum", "ro": "rum",
    "vie": "vie", "vietnamese": "vie", "vi": "vie",
    "tha": "tha", "thai": "tha", "th": "tha",
    "heb": "heb", "hebrew": "heb", "he": "heb",
    "gre": "gre", "greek": "gre", "el": "gre",
}


def scan_folder(folder: str, include_subtitles: bool = False) -> List[Path]:
    """Return all video files found recursively under folder.
    
    If include_subtitles=True, also returns subtitle files.
    """
    folder_path = Path(folder)
    result = []
    if not folder_path.exists() or not folder_path.is_dir():
        return result

    for root, _dirs, files in os.walk(folder):
        for name in sorted(files):
            p = Path(root) / name
            if p.suffix.lower() in VIDEO_EXTENSIONS:
                result.append(p)
            elif include_subtitles and p.suffix.lower() in SUBTITLE_EXTENSIONS:
                result.append(p)

    return result


def find_subtitle_siblings(video: Path) -> List[Path]:
    """Return subtitle files in the same directory that share the video stem,
    as well as those in a 'Subs' or 'Subtitles' sibling folder."""
    subs = []
    seen = set()
    parent = video.parent

    def _add(f):
        key = os.path.normcase(str(f))
        if key not in seen:
            seen.add(key)
            subs.append(f)

    _dbg(f"Finding subs for: {video}")

    # 1) Subtitles next to the video that share its stem (handles .en.srt etc.)
    if parent.exists() and parent.is_dir():
        for f in parent.iterdir():
            if f.is_file() and f.suffix.lower() in SUBTITLE_EXTENSIONS:
                if f.stem.startswith(video.stem) or f.stem == video.stem:
                    _dbg(f"Found sub in same dir: {f}")
                    _add(f)

    # 2) Subtitles inside a Subs / Subtitles folder next to the video
    seen_dirs = set()
    for sub_dir_name in SUBTITLE_FOLDER_NAMES:
        sub_dir = parent / sub_dir_name
        dir_key = os.path.normcase(str(sub_dir))
        if dir_key in seen_dirs or not sub_dir.is_dir():
            continue
        seen_dirs.add(dir_key)
        for f in sub_dir.iterdir():
            if f.is_file() and f.suffix.lower() in SUBTITLE_EXTENSIONS:
                if video.stem.lower() in f.stem.lower():
                    _dbg(f"Found sub in sub folder: {f}")
                    _add(f)

    _dbg(f"Total subs found: {len(subs)}")
    return subs


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTENSIONS


def is_subtitle(path: Path) -> bool:
    return path.suffix.lower() in SUBTITLE_EXTENSIONS


def detect_subtitle_language(sub_path: Path) -> Tuple[str, List[str]]:
    """Detect language code from subtitle filename.

    Looks for patterns like:
    - 4_eng, 4_en -> eng
    - English, English SDH -> eng
    - .eng. -> eng
    - forced, sdh/cc/hi flags detected

    Returns (language code, flags) where flags may contain "sdh" and/or "forced".
    """
    name = sub_path.stem
    flags = []
    lang = "eng"

    name_lower = name.lower()

    # Flags
    # (SDH / closed captions / hearing impaired, forced)
    if re.search(r'\b(sdh|cc|hi)\b', name_lower):
        flags.append("sdh")
    if re.search(r'\bforced\b', name_lower):
        flags.append("forced")

    # Language: check the last few dot-separated parts of the name
    valid_codes = {v for v in LANG_MAP.values()}

    # e.g. "Show.S01E01.eng" -> ["Show", "S01E01", "eng"]
    parts = name.split(".")

    # strip numeric prefixes like "4_" (RARBG style "4_English")
    def clean_part(p: str) -> str:
        return re.sub(r'^\d+_', '', p).strip().lower()

    # prefer explicit codes found near the end of the name
    for part in reversed(parts[-3:]):
        part_clean = clean_part(part)
        if part_clean in valid_codes and part_clean != "eng":
            lang = part_clean
            break
        if part_clean in LANG_MAP:
            lang = LANG_MAP[part_clean]
            break

    return lang, flags


def format_subtitle_filename(video_stem: str, lang: str, ext: str, flags: Optional[List[str]] = None) -> str:
    """Format subtitle filename following Plex conventions.
    
    Format: {videofilename}.{lang}.{flags}.{ext}
    Example: Parks.and.Recreation.S02E01.eng.srt
             Parks.and.Recreation.S02E01.eng.forced.srt
             Parks.and.Recreation.S02E01.eng.sdh.srt
    """
    if flags is None:
        flags = []

    parts = [video_stem, lang]
    parts.extend(flags)

    result = ".".join(parts) + ext
    return result
