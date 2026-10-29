"""OpenSubtitles.com: find and download subtitles for a video.

Subtitles made for the exact release are found by the file's OpenSubtitles
hash; others by title (and season/episode). The app needs an API key (built
into releases, or the user's own in Settings). Signing in with a free
OpenSubtitles account raises the daily download limit.
"""
import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import requests

from scanner import LANGUAGE_WORDS, unique_subtitle_filename

API_URL = "https://api.opensubtitles.com/api/v1"
_CHUNK = 64 * 1024


class OpenSubtitlesError(Exception):
    """Something the user can act on, worded for them."""


def opensubtitles_hash(path) -> Optional[str]:
    """The OpenSubtitles movie hash: file size plus the 64-bit little-endian
    words of the first and last 64 KB. None for files under 128 KB."""
    size = os.path.getsize(path)
    if size < 2 * _CHUNK:
        return None
    total = size
    with open(path, "rb") as f:
        for offset in (0, size - _CHUNK):
            f.seek(offset)
            total += sum(struct.unpack(f"<{_CHUNK // 8}Q", f.read(_CHUNK)))
    return f"{total & 0xFFFFFFFFFFFFFFFF:016x}"


# ── languages: Plex names use ISO 639-2/B ("eng"), OpenSubtitles mostly 639-1 ──
_TO_OS = {"por": ("pt-PT", "pt-BR"), "chi": ("zh-CN", "zh-TW"), "hin": ("hi",)}
_FROM_OS = {"hi": "hin", "pb": "por", "ze": "chi", "ea": "spa"}


def to_opensubtitles(code: str) -> List[str]:
    """'eng' -> ['en'], 'por' -> ['pt-PT', 'pt-BR']."""
    if code in _TO_OS:
        return list(_TO_OS[code])
    two = [alias for alias, c in LANGUAGE_WORDS.items() if c == code and len(alias) == 2 and alias.isalpha()]
    return two[:1]


def from_opensubtitles(code: str) -> str:
    """'en' -> 'eng', 'pt-BR' -> 'por'; '' when unknown."""
    code = (code or "").lower()
    if code in _FROM_OS:
        return _FROM_OS[code]
    base = code.split("-")[0]
    return LANGUAGE_WORDS.get(base, "") if base != "hi" else "hin"


def parse_languages(text: str) -> List[str]:
    """'English, es, fre' -> ['eng', 'spa', 'fre'] (unknown words are skipped)."""
    out = []
    for word in str(text or "").replace(";", ",").split(","):
        word = word.strip().lower()
        code = "hin" if word == "hi" else LANGUAGE_WORDS.get(word)
        if code and code not in out:
            out.append(code)
    return out


@dataclass
class SubtitleResult:
    file_id: int
    language: str                  # ISO 639-2/B, e.g. "eng"
    release: str = ""
    file_name: str = ""
    hearing_impaired: bool = False
    forced: bool = False           # only the foreign-language parts
    hash_match: bool = False       # made for this exact file
    trusted: bool = False
    machine: bool = False          # machine or AI translated
    downloads: int = 0


class OpenSubtitlesClient:
    def __init__(self, api_key, username="", password="", app_version="1.0", session=None,
                 base_url=API_URL, timeout=20.0):
        self.api_key = (api_key or "").strip()
        self.username, self.password = (username or "").strip(), password or ""
        self.user_agent = f"PathRenamer v{app_version}"
        self.s = session or requests.Session()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.token = None
        self.remaining = None          # downloads left today, once known

    def _headers(self):
        h = {"Api-Key": self.api_key, "User-Agent": self.user_agent, "Accept": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _call(self, method, path, **kw):
        if not self.api_key:
            raise OpenSubtitlesError("Subtitle downloads need an OpenSubtitles API key: add one under "
                                     "Settings → Subtitles.")
        try:
            r = self.s.request(method, self.base_url + path, headers=self._headers(),
                               timeout=self.timeout, **kw)
        except requests.RequestException as e:
            raise OpenSubtitlesError(f"Can't reach OpenSubtitles ({e.__class__.__name__}). "
                                     "Check your internet connection.")
        try:
            data = r.json()
        except ValueError:
            data = {}
        message = (data.get("message") or data.get("errors") or "") if isinstance(data, dict) else ""
        if isinstance(message, list):
            message = " ".join(str(m) for m in message)
        if r.status_code == 401:
            raise OpenSubtitlesError("OpenSubtitles didn't accept your username or password.")
        if r.status_code == 403:
            raise OpenSubtitlesError("OpenSubtitles didn't accept the API key.")
        if r.status_code == 406:
            raise OpenSubtitlesError(message or "You've reached today's OpenSubtitles download limit.")
        if r.status_code == 429:
            raise OpenSubtitlesError("OpenSubtitles is busy right now. Try again in a minute.")
        if r.status_code >= 400:
            raise OpenSubtitlesError(f"OpenSubtitles answered with an error ({r.status_code}). {message}".strip())
        return data

    def login(self):
        """Sign in (only when a username is set). Returns the account info."""
        if not self.username:
            return {}
        data = self._call("POST", "/login", json={"username": self.username, "password": self.password})
        self.token = data.get("token")
        host = data.get("base_url")
        if host and self.base_url == API_URL:          # VIP accounts get their own server
            self.base_url = f"https://{host}/api/v1"
        user = data.get("user") or {}
        if "allowed_downloads" in user:
            self.remaining = user.get("allowed_downloads")
        return user

    def search(self, path=None, languages=("eng",), query="", year=None, season=None,
               episode=None, imdb_id=None, is_episode=None) -> List[SubtitleResult]:
        params = {"languages": ",".join(sorted({c.lower() for lang in languages
                                                for c in to_opensubtitles(lang)}))}
        moviehash = opensubtitles_hash(path) if path and Path(path).exists() else None
        if moviehash:
            params["moviehash"] = moviehash
        if query:
            params["query"] = str(query).lower()
        if year and not is_episode:
            params["year"] = int(year)
        if season is not None:
            params["season_number"] = int(season)
        if episode is not None:
            params["episode_number"] = int(episode)
        if imdb_id:
            params["imdb_id"] = int(str(imdb_id).lower().lstrip("t") or 0)
        if is_episode is not None:
            params["type"] = "episode" if is_episode else "movie"
        # The API asks for parameters in alphabetical order (fewer redirects)
        data = self._call("GET", "/subtitles", params=sorted(params.items()))
        out = []
        for item in data.get("data") or []:
            a = item.get("attributes") or {}
            files = a.get("files") or []
            language = from_opensubtitles(a.get("language", ""))
            if len(files) != 1 or not language:        # multi-CD subtitles don't fit one video
                continue
            out.append(SubtitleResult(
                file_id=int(files[0].get("file_id")), language=language,
                release=a.get("release") or "", file_name=files[0].get("file_name") or "",
                hearing_impaired=bool(a.get("hearing_impaired")),
                forced=bool(a.get("foreign_parts_only")),
                hash_match=bool(a.get("moviehash_match")), trusted=bool(a.get("from_trusted")),
                machine=bool(a.get("machine_translated") or a.get("ai_translated")),
                downloads=int(a.get("download_count") or 0)))
        return out

    @staticmethod
    def best_per_language(results: Iterable[SubtitleResult], languages,
                          hearing_impaired=False) -> Dict[str, SubtitleResult]:
        """The best subtitle for each wanted language: made for this file,
        written by people, complete (not forced-only), with or without
        hearing-impaired notes as preferred, from a trusted uploader, most
        downloaded."""
        best = {}
        for r in results:
            if r.language not in languages:
                continue
            key = (r.hash_match, not r.machine, not r.forced, r.hearing_impaired == hearing_impaired,
                   r.trusted, r.downloads)
            if r.language not in best or key > best[r.language][0]:
                best[r.language] = (key, r)
        return {lang: r for lang, (_, r) in best.items()}

    def download(self, result: SubtitleResult) -> bytes:
        data = self._call("POST", "/download", json={"file_id": result.file_id})
        if "remaining" in data:
            self.remaining = data.get("remaining")
        link = data.get("link")
        if not link:
            raise OpenSubtitlesError(data.get("message") or "OpenSubtitles didn't send the subtitle.")
        try:
            r = self.s.get(link, timeout=self.timeout, headers={"User-Agent": self.user_agent})
        except requests.RequestException as e:
            raise OpenSubtitlesError(f"The subtitle download failed ({e.__class__.__name__}).")
        if r.status_code >= 400 or not r.content:
            raise OpenSubtitlesError(f"The subtitle download failed ({r.status_code}).")
        return r.content


def subtitle_name(video: Path, result: SubtitleResult, taken: set) -> str:
    """Plex name for a downloaded subtitle next to video: "Movie (2010).eng.srt"
    ("Movie (2010).eng.sdh.srt" with hearing-impaired notes). taken: lower-case
    names already in the folder; the new name is added to it."""
    ext = Path(result.file_name).suffix.lower() or ".srt"
    if ext not in (".srt", ".ass", ".ssa", ".vtt", ".sub", ".smi"):
        ext = ".srt"
    flags = (["sdh"] if result.hearing_impaired else []) + (["forced"] if result.forced else [])
    return unique_subtitle_filename(Path(video).stem, result.language, ext, flags, taken)
