from pathlib import Path
from typing import Optional
import guessit
import re


class ParsedFile:
    def __init__(self, path: Path, raw: dict):
        self.path = path
        self.raw = raw

        self.media_type: str = raw.get("type", "episode")  # episode | movie
        self.title: Optional[str] = raw.get("title")
        self.year: Optional[int] = raw.get("year")
        self.season: Optional[int] = raw.get("season")
        self.episode: Optional[int] = self._first_int(raw.get("episode"))
        self.episode_title: Optional[str] = raw.get("episode_title")
        self.release_group: Optional[str] = raw.get("release_group")

    @staticmethod
    def _first_int(val) -> Optional[int]:
        if val is None:
            return None
        if isinstance(val, list):
            return int(val[0]) if val else None
        return int(val)

    @property
    def is_episode(self) -> bool:
        return self.media_type == "episode"

    @property
    def is_movie(self) -> bool:
        return self.media_type == "movie"

    def __repr__(self):
        if self.is_episode:
            return f"<ParsedFile TV '{self.title}' S{self.season}E{self.episode}>"
        return f"<ParsedFile Movie '{self.title}' ({self.year})>"


def parse_filename(path: Path) -> ParsedFile:
    """Use guessit to extract metadata from a media filename."""
    result = guessit.guessit(path.name)
    return ParsedFile(path, result)


def clean_title(title: Optional[str]) -> str:
    """Normalize a title for API querying."""
    if not title:
        return ""
    return title.strip()


def clean_show_folder(folder: str) -> str:
    """Clean up show folder name for better matching."""
    if not folder:
        return ""
    name = folder.strip()
    name = re.sub(r'S\d{1,2}E\d{1,4}', '', name, flags=re.IGNORECASE)
    name = re.sub(r'S\d{1,2}\b', '', name, flags=re.IGNORECASE)
    name = re.sub(r'Season\s*\d{1,2}\b', '', name, flags=re.IGNORECASE)
    name = re.sub(r'[._]+', ' ', name)
    name = re.sub(r'\(\d{4}\)', '', name)
    name = re.sub(r'\b\d{4}\b', '', name)
    name = re.sub(r'\b1080[ip]\b', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\b720[ip]\b', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\b2160[ip]\b', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\b4K\b', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\bBlu-?Ray\b', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\bBD-?Rip\b', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\bWEB-?DL\b', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\bHDTV\b', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\bDVD-?Rip\b', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\bx264\b', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\bx265\b', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\bHEVC\b', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s*-+[A-Za-z0-9]+\s*$', '', name)
    name = re.sub(r'\s+', ' ', name)
    name = re.sub(r'\d+bit', '', name, flags=re.IGNORECASE)
    name = re.sub(r'AAC.*', '', name, flags=re.IGNORECASE)
    name = re.sub(r'Dolby.*', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\bAMZN\b', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\bEAC3\b', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\b5\.?1\b', '', name)
    name = re.sub(r'\bSilence\b', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\(.*?\)', '', name)
    name = re.sub(r'\[.*?\]', '', name)
    name = name.strip()
    return name


def is_valid_show_name(name: str) -> bool:
    """Check if cleaned folder name looks like an actual show title, not a generic folder name."""
    if not name or len(name) < 2:
        return False
    invalid_names = {
        'season', 'specials', 'extras', 'subs', 'subtitles', 'subtitle', 'sub', 'captions',
        'sample', 'samples', 'other', 'misc', 'new', 'new folder', 'folder', 'temp',
        'downloads', 'torrents', 'torrent', 'complete', 'completed', 'seeding',
        'staging', 'unsorted', 'media', 'library', 'video', 'videos',
        'movie', 'movies', 'film', 'films', 'tv', 'shows', 'tv shows', 'series',
        'anime', 'plex', 'jellyfin',
        'emby', 'kodi',
    }
    if name.lower().strip() in invalid_names:
        return False
    if re.match(r'^\d+$', name):
        return False
    if name in ('Season', 'Season 1', 'Season 01'):
        return False
    return True
