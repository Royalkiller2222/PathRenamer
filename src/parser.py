import datetime
import os
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Tuple
import guessit
import re

try:
    from rapidfuzz import fuzz as _fuzz
except ImportError:  # rapidfuzz ships with the app; only the agreement check degrades without it
    _fuzz = None


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


_GENERIC_FOLDER_NAMES = {
    'season', 'specials', 'extras', 'subs', 'subtitles', 'subtitle', 'sub', 'captions',
    'sample', 'samples', 'other', 'misc', 'new', 'new folder', 'folder', 'temp',
    'downloads', 'torrents', 'torrent', 'complete', 'completed', 'seeding',
    'staging', 'unsorted', 'media', 'library', 'video', 'videos',
    'movie', 'movies', 'film', 'films', 'tv', 'shows', 'tv shows', 'series',
    'anime', 'plex', 'jellyfin',
    'emby', 'kodi',
    # common download-client / staging folder names
    'download', 'incoming', 'finished', 'done', 'to sort', 'sort', 'sorted',
    'qbittorrent', 'utorrent', 'bittorrent', 'transmission', 'deluge', 'usenet',
    'sabnzbd', 'nzbget', 'tv series', 'tv-shows', 'desktop', 'documents', 'public',
}


def is_valid_show_name(name: str, allow_numeric: bool = False) -> bool:
    """Check if cleaned folder name looks like an actual show title, not a generic folder name.

    allow_numeric: accept all-digit names. Folder names like "2019" are not
    titles, but a title guessit found in a file name can be ("1883", "9")."""
    if not name or len(name) < (1 if allow_numeric else 2):
        return False
    if name.lower().strip() in _GENERIC_FOLDER_NAMES:
        return False
    if re.match(r'^\d+$', name) and not allow_numeric:
        return False
    if name in ('Season', 'Season 1', 'Season 01'):
        return False
    return True




# ═══════════════════════════════════════════════════════════════════
#  Release-name parsing
# ═══════════════════════════════════════════════════════════════════
# guessit does the heavy lifting. The code below corrects the cases where it
# goes wrong on real release names: hyphenated and numeric titles, title words
# mistaken for tags ("The.Last.of.Us" -> country "US"), anime fansub names,
# multi-episode files, anime/foreign season markers, dates, website prefixes
# and obfuscated file names. analyse_path() then combines what the file name
# and its folders say.

MEDIA_EXTENSIONS = {
    ".mkv", ".mp4", ".avi", ".mov", ".wmv", ".m4v", ".ts", ".m2ts", ".flv", ".webm", ".mpg", ".mpeg",
    ".srt", ".sub", ".ass", ".ssa", ".vtt", ".idx",
}

# Words that are never a real title when guessit returns them for a file name.
_JUNK_FILE_TITLES = {
    "sample", "samples", "extra", "extras", "sub", "subs", "subtitle", "subtitles",
    "featurette", "featurettes", "trailer", "season", "episode", "unknown",
}


@dataclass
class ReleaseInfo:
    """What a single file or folder name says about its media."""
    title: str = ""
    title_strong: bool = False   # title comes before the episode marker: a show name, not an episode title
    obfuscated: bool = False     # scene short name or hash such as "grp-abc.mkv"
    year: Optional[int] = None
    season: Optional[int] = None
    episodes: List[int] = field(default_factory=list)
    date: Optional[datetime.date] = None
    episode_title: str = ""
    explicit: bool = False       # the name has an SxxEyy / NxNN marker
    raw: dict = field(default_factory=dict)  # plain guessit result (codecs, resolution, ...)


def strip_media_extension(name: str) -> str:
    root, ext = os.path.splitext(name)
    return root if ext.lower() in MEDIA_EXTENSIONS else name


# "www.site.org - ", "[ www.site.cd ] -", "From [ WWW.SITE.COM ] - ", "[site.com].", "Site.com_"
_SITE_PREFIX = re.compile(
    r"^\s*(?:from\s*)?[\[(]?\s*www\.[^\s\[\]()]+\s*[\])]?\s*[-–—_:|~.]*\s*"
    r"|^\s*[\[(]\s*[^\[\]()]*?\.(?:com|org|net|cd|to|me|tv|cc|ws|info|in|io|eu|ru|se|li|is|nz|co|xyz"
    r"|club|site|online|top|vip|pro|link|lol|biz|mx|show)\s*[\])]\s*[-–—_:|~.]*\s*"
    r"|^\s*[A-Za-z0-9-]{3,}\.(?:com|org|net|to|cd)(?:_+|\s+-\s+)",
    re.IGNORECASE)

# Japanese TV rips start with the air date: "221201 Show Title ep05"
_YYMMDD_PREFIX = re.compile(r"^(?P<y>\d{2})(?P<m>0[1-9]|1[0-2])(?P<d>0[1-9]|[12]\d|3[01])\s+(?=\S)")

_LEADING_GROUP = re.compile(r"^\s*[\[【]([^\]】]*)[\]】][\s._-]*")

# "[Group] Title - 01v2 [1080p][ABCD1234]", "[Group] Title - 01-12 END (BD)"
_FANSUB_TAIL = (r"(?:[\s_]+(?:END|FINAL|Fin))?"
                r"(?:[\s_]+(?:VOSTFR|VOSTA|RAW|ENG|MULTI|UNCENSORED|UNCUT))*"
                r"(?P<tail>(?:[\s_.]*[\[(（【][^\])）】]*[\])）】])*)[\s_.]*$")
_FANSUB_EP = (r"(?P<ep>\d{1,4})(?:[vV]\d{1,2})?"
              r"(?:(?:-|[\s_]*[~&+][\s_]*)(?P<ep2>\d{1,4})(?:[vV]\d{1,2})?)?")
_FANSUB_GROUPS = r"^\s*[\[【](?P<group>[^\]】]+)[\]】][\s._]*(?:[\[【][^\]】]*[\]】][\s._]*)*"
_FANSUB = re.compile(
    _FANSUB_GROUPS + r"(?P<title>.+?)[\s_]+-[\s_]+" + _FANSUB_EP
    + r"(?:[\s_]+-[\s_]+(?P<eptitle>[^\[\]()]+?))?" + _FANSUB_TAIL,
    re.IGNORECASE)
# "[Group] Title 02 [720p]" (no dash before the number)
_FANSUB_NODASH = re.compile(_FANSUB_GROUPS + r"(?P<title>.+?)[\s_]+" + _FANSUB_EP + _FANSUB_TAIL, re.IGNORECASE)
# "Title Episode 13", "Title_Ep_2", "Title Episódio 1": the episode word marks where the title ends
_EPISODE_WORD = re.compile(
    r"(?<![A-Za-z0-9])(?:Episode|Episodio|Episódio|[EÉ]pisode|Episodul|Ep)\.?[\s._#-]*"
    r"(?P<e>\d{1,4})(?:[vV]\d)?(?:-(?P<e2>\d{1,4}))?(?![0-9])", re.IGNORECASE)
# "Mary Bell (DVD) - 01v2 [h-b]": the same without a leading [Group]
_TITLE_DASH_EPISODE = re.compile(
    r"^(?P<title>.+?)[\s_]+-[\s_]+(?P<ep>\d{1,4})(?:[vV]\d{1,2})?" + _FANSUB_TAIL, re.IGNORECASE)

# Leading number that is the title: "1923.S02E01", "1917 (2019)", "20-1.2014.S02E01"
_NUMERIC_TITLE = re.compile(
    r"(\d{1,4}(?:-\d{1,3})?)(?=[\s._-]+(?:S\d{1,3}[\s._-]?E\d|\d{1,2}x\d{2}"
    r"|\(?(?:19|20)\d{2}\)?(?=[\s._)\]-]|$)))",
    re.IGNORECASE)

# SxxEyy with optional extra episodes: S01E01E02, S01E01-E03, S01E01-03, S01E01 E02, S01E01&E02
_SXXEYY = re.compile(
    r"(?<![0-9])S(?P<s>\d{1,4})[\s._x-]?E(?P<e>\d{1,5})(?![0-9])"
    r"(?P<more>(?:(?:[\s._-]?E|-E?|&E?|\+E?)\d{1,4}(?![0-9A-DF-Za-df-z]))*)",
    re.IGNORECASE)
_SXXEYY_MORE = re.compile(r"([\s._-]?E|-E?|&E?|\+E?)(\d{1,4})", re.IGNORECASE)
# a further "S07E23" right after the first marker: "S07E22 - S07E23", "S14E00.and.S14E01"
_SXXEYY_REPEAT = r"(?:[\s._]*(?:-|&|\+|and)?[\s._]*)S0*{s}[\s._x-]?E(\d{{1,4}})(?![0-9])"
_NXNN = re.compile(r"(?<![0-9])\d{1,2}[x×]\d{2,3}(?![0-9])", re.IGNORECASE)

# Anime / non-scene season+episode forms guessit misreads. Each gives
# (season, episode) and is specific enough to override guessit.
_SEASON_EPISODE_OVERRIDES = [
    # "Title 87 (S4-24)", "Title - 07 [S2-07]"
    re.compile(r"(?:(?<![0-9])(?P<abs>\d{1,4})[\s._]*)?[\[(]\s*S(?P<s>\d{1,2})\s*-\s*(?P<e>\d{1,4})\s*[\])]", re.I),
    # "Title S2 - 03", "Title S2 (2019) - 11"
    re.compile(r"(?<![A-Za-z0-9])S(?P<s>\d{1,2})(?:[\s_]*\((?:19|20)\d{2}\))?[\s_]+-[\s_]+(?P<e>\d{1,4})(?:v\d)?(?![0-9])", re.I),
    # "Title S2 [05]"
    re.compile(r"(?<![A-Za-z0-9])S(?P<s>\d{1,2})[\s_]*\[(?P<e>\d{1,4})(?:v\d)?\]", re.I),
    # "Title 2nd Season 24", "Title 2nd Season - 24"
    re.compile(r"(?<![0-9])(?P<s>\d{1,2})(?:st|nd|rd|th)[\s._-]+Season(?:[\s._]+-)?[\s._-]+(?P<e>\d{1,4})(?:v\d)?(?![0-9])", re.I),
    # Turkish "The.White.Lotus.2.Sezon.7.Bölüm"
    re.compile(r"(?<![0-9])(?P<s>\d{1,2})\.?[\s._-]*Sezon[\s._-]*(?P<e>\d{1,3})\.?[\s._-]*B[öo]l[üu]m", re.I),
    # "Title Season 2 - 05", "Title_Season_2_Ep07", "Title Season 01 Episode 05-06"
    re.compile(r"(?<![A-Za-z0-9])Season[\s._-]*(?P<s>\d{1,2})[\s._]*(?:-[\s._]*|(?:Ep(?:isode)?|E)[\s._-]*)"
               r"(?P<e>\d{1,4})(?:-(?P<e2>\d{1,4}))?(?![0-9])", re.I),
    # Russian "Сезон 3, Эпизод 21", "Сезон №9. Серия №180", transliterated "8.sez.21.ser"
    re.compile(r"сезон[\s._№-]*(?P<s>\d{1,2})[\s.,_-]*(?:эпизод|серия)[\s._№-]*(?P<e>\d{1,4})", re.I),
    re.compile(r"(?<![0-9])(?P<s>\d{1,2})[\s._]*sez(?:on)?[\s._]*(?P<e>\d{1,3})[\s._]*ser", re.I),
    # Spanish "Cap.201" = S02E01, "Cap.111_120" = S01E11-E20
    re.compile(r"(?<![A-Za-z0-9])Cap[\s._]*(?P<s>\d{1,2})(?P<e>\d{2})(?:_(?P=s)(?P<e2>\d{2}))?(?![0-9])", re.I),
    # "1x01-x03", "[02x01-x02]", "[06x01-08 of 16]"
    re.compile(r"(?<![0-9])(?P<s>\d{1,2})x(?P<e>\d{2,3})(?:-x?\d{2,3})*-x?(?P<e2>\d{2,3})(?![0-9])", re.I),
    # Seasons numbered by year: "Looney Tunes 1940x01", "Show - 2010x15 - 2010x16"
    re.compile(r"(?<![0-9])(?P<s>(?:19|20)\d{2})x(?P<e>\d{1,3})(?:[\s._-]+(?:-[\s._]*)?(?P=s)x(?P<e2>\d{1,3}))?(?![0-9])", re.I),
    # Russian ordinals: "5-го сезон 9 серия", "5-й сезон 09-я серия"
    re.compile(r"(?<![0-9])(?P<s>\d{1,2})(?:-?[а-яё]{1,2})?[\s._-]*сезон[\s.,_-]*(?P<e>\d{1,4})(?:-?[а-яё]{1,2})?[\s._-]*сери", re.I),
    # Specials: "[FFF] Title - SP01", "Title - OVA 2" -> Season 00
    re.compile(r"[\s_]+-[\s_]+(?P<sp>SP|OVA|OAD|Special)[\s_]*(?P<e>\d{1,3})(?![0-9])", re.I),
    # Spanish/Portuguese "T01XE08", "T02E22"
    re.compile(r"(?<![A-Za-z0-9])T(?P<s>\d{1,2})X?E(?P<e>\d{1,3})(?![0-9])", re.I),
]
# "Aflevering 5 van 12", "Folge 5 von 12", "Episode 3 of 10", "5 серия из 12": the first number
_EPISODE_OF = re.compile(
    r"(?<![A-Za-z0-9])(?:Episode|Episodio|Episodul|[EÉ]pisode|Ep|Aflevering|Afl|Folge|Avsnitt|Odcinek|Jakso|Part"
    r"|Cap[ií]tulo)"
    r"\.?[\s._-]*(?P<e>\d{1,3})[\s._-]+(?:of|van|von|av|z|ze|de|di|din|sur|из)[\s._-]+\d{1,3}(?![0-9])"
    r"|(?<![0-9])(?P<e2>\d{1,3})[\s._-]*(?:серия|серии)[\s._-]+из[\s._-]+\d{1,3}",
    re.IGNORECASE)

_SEASON_WORDS = (r"Season|Saison|Temporada|Staffel|Stagione|Seizoen|Sezona?|S[äa]song|S[æa]son|Sesong"
                 r"|Kausi")
_EPISODE_WORDS = (r"Episode|[EÉ]pisode|Episodio|Episódio|Folge|Avsnitt|Aflevering|Afl|Odcinek|Jakso"
                  r"|Epizoda|Epizod|B[öo]l[üu]m|Cap[ií]tulo|Ep")
# Used only when guessit found nothing.
_SEASON_FALLBACKS = [
    re.compile(r"(?<![A-Za-z0-9])(?:" + _SEASON_WORDS + r")\.?[\s._-]*(?P<n>\d{1,2})(?![0-9])", re.I),
    re.compile(r"(?<![A-Za-z0-9])Se[\s._-]+(?P<n>\d{1,2})(?![0-9])", re.I),               # Dutch "Se.3"
    re.compile(r"(?<![0-9])(?P<n>\d{1,2})\.?[\s._-]*(?:évad|evad|сезон|sezon)", re.I),
    re.compile(r"(?<![0-9])(?P<n>\d{1,2})\.[\s._-]*(?:Staffel|Saison|Temporada|Stagione|Sezon)", re.I),
    re.compile(r"сезон[\s._№-]*(?P<n>\d{1,2})", re.I),
    re.compile(r"シーズン\s*(?P<n>[0-9０-９]+)"),
    re.compile(r"第\s*(?P<n>[0-9０-９]+|[〇零一二两三四五六七八九十百]+)\s*[季期部]"),
]
_EPISODE_FALLBACKS = [
    re.compile(r"(?:эпизод|серия)[\s._№-]*(?P<n>\d{1,4})", re.I),
    re.compile(r"(?<![A-Za-z0-9])(?:" + _EPISODE_WORDS + r")\.?[\s._#-]*(?P<n>\d{1,4})(?![0-9])", re.I),
    re.compile(r"(?<![0-9])(?P<n>\d{1,4})\.?[\s._-]*(?:rész|resz|серия|серии|b[öo]l[üu]m|blm)(?![a-z])", re.I),
    re.compile(r"第\s*(?P<n>[0-9０-９]+|[〇零一二两三四五六七八九十百]+)\s*[集话話回]"),
]
_CJK_SEASON = _SEASON_FALLBACKS[-1]
_SEASON_EPISODE_FALLBACKS = [
    # "The.Witcher.S01.07", "Breaking Bad S02 03"
    re.compile(r"(?<![A-Za-z0-9])S(?P<s>\d{1,2})[\s._](?P<e>\d{1,3})(?![0-9pPiIkKbB])", re.I),
    # "2x09-2x10", "La casa del dragón 2×7", "Castle1x01"
    re.compile(r"(?<![0-9])(?P<s>\d{1,2})[x×](?P<e>\d{1,3})(?:[-&+](?:(?P=s)[x×])?(?P<e2>\d{1,3}))?(?![0-9])", re.I),
    # "office_03_19", "SupNat-11_06"
    re.compile(r"(?:^|[\s._-])(?P<s>\d{1,2})_(?P<e>\d{2,3})(?=$|[\s._\[(-])"),
]
# Random file names ("gNWDXow11s7E0X7GTDrZ") say nothing, even where they happen to contain "s7E0"
_RANDOM_NAME = re.compile(r"^(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[a-z])(?=[A-Za-z0-9]*[A-Z])[A-Za-z0-9]{12,}$")
_SCENE_MULTI = re.compile(r"(?<![0-9])(?P<s>\d{1,2})(?P<e1>\d{2})[\s._](?P=s)(?P<e2>\d{2})(?![0-9])")  # "101.102"
_YMD_DATE = re.compile(r"(?<![0-9])((?:19|20)\d{2})([\s._-])(\d{1,2})\2(\d{1,2})(?![0-9])")
_EXPLICIT_EP = re.compile(r"(?:^|[\s._-])(?:E|EP|Episode)[\s._-]?(\d{1,5})(?![0-9])", re.IGNORECASE)

# Season markers at the end of an (anime) title
_TITLE_SEASON_SUFFIX = [
    re.compile(r"[\s._-]*\(\s*Season[\s._-]*(?P<n>\d{1,2})\s*\)$", re.I),           # "Title (Season 2)"
    re.compile(r"[\s._-]+S(?P<n>\d{1,2})$", re.I),                                   # "Title S2"
    re.compile(r"[\s._-]+Season[\s._-]*(?P<n>\d{1,2})$", re.I),                      # "Title Season 2"
    re.compile(r"[\s._-]+(?P<n>\d{1,2})(?:st|nd|rd|th)[\s._-]+Season$", re.I),       # "Title 2nd Season"
    re.compile(r"[\s._]+-[\s._]+S(?P<n>\d{1,2})(?:[\s._]+-[\s._]+Part[\s._]*\d+)?$", re.I),   # "Title - S03 - Part 1"
    re.compile(r"\s*第\s*(?P<n>[0-9０-９]+|[〇零一二两三四五六七八九十百]+)\s*[季期部]$"),   # "庆余年第二季"
    re.compile(r"\s*シーズン\s*(?P<n>[0-9０-９]+)$"),
]

# Scene short names ("grp-abbrev") and hash-like names carry no usable title.
_OBFUSCATED = re.compile(
    r"^(?:[a-z0-9]{1,10}(?:-[a-z0-9]{2,20}){1,3}|[0-9a-f]{12,}|[A-Za-z0-9]{6,}(?:-[A-Za-z0-9]{4,}){2,}"
    r"|(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[a-z])(?=[A-Za-z0-9]*[A-Z])[A-Za-z0-9]{12,})$")

# guessit tags that are sometimes really title words ("The.Collector.2009",
# "It.Ends.With.Us.2024", "Winter.Classic.S07E01", "Au bout c'est la mer").
_TITLE_WORD_KEYS = {"country", "language", "subtitle_language", "other", "edition", "audio_codec"}
# ...unless the word is one of these release tags
_TAG_WORDS = {
    "proper", "repack", "rerip", "real", "internal", "limited", "complete", "remux", "dubbed",
    "subbed", "multi", "dual", "fix", "dirfix", "nfofix", "uncut", "unrated", "extended",
    "remastered", "theatrical", "imax", "criterion", "hdr", "sdr", "3d", "ws", "rip", "hd", "hq",
    "readnfo", "festival", "retail", "converted", "colorized", "edition", "cut", "version",
    "directors", "director's", "special", "ultimate", "anniversary", "collector's", "final",
}

_CN_DIGITS = {"〇": 0, "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _cjk_number(s: str) -> Optional[int]:
    if s.isdigit():
        return int(s)            # also handles full-width digits
    total, cur = 0, 0
    for ch in s:
        if ch == "百":
            total += (cur or 1) * 100; cur = 0
        elif ch == "十":
            total += (cur or 1) * 10; cur = 0
        elif ch in _CN_DIGITS:
            cur = _CN_DIGITS[ch]
        else:
            return None
    return total + cur


def _spaced(text: str) -> str:
    """Release-name separators to single spaces."""
    return re.sub(r"\s+", " ", re.sub(r"[._]+", " ", text)).strip(" -")


@lru_cache(maxsize=4096)
def _run_guessit(name: str, kind: str):
    """guessit with match positions. Returns (plain result, {key: ((value, start, end), ...)})."""
    opts = {"advanced": True}
    if kind:
        opts["type"] = kind
    try:
        result = guessit.guessit(name, opts)
    except Exception:
        return {}, {}
    raw, spans = {}, {}
    for key, val in result.items():
        items = val if isinstance(val, list) else [val]
        values = [getattr(m, "value", m) for m in items]
        raw[key] = values if isinstance(val, list) else values[0]
        spans[key] = tuple((getattr(m, "value", m), m.start, m.end) for m in items if hasattr(m, "start"))
    return raw, spans


def _ints(values) -> List[int]:
    out = []
    for v in values:
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            pass
    return out


def _parse_sxxeyy(text: str):
    """Explicit SxxEyy marker -> (season, [episodes], start) or None."""
    m = _SXXEYY.search(text)
    if not m:
        return None
    season, first = int(m.group("s")), int(m.group("e"))
    eps = [first]
    for sep, num in _SXXEYY_MORE.findall(m.group("more") or ""):
        n = int(num)
        if n <= eps[-1] or n > eps[-1] + 100:
            break                                   # "S12E13-3_Acts_of_God": not an episode
        if sep.startswith("-"):
            eps.extend(range(eps[-1] + 1, n + 1))   # S01E01-E03 is a range
        else:
            eps.append(n)
    pos = m.end()
    repeat = re.compile(_SXXEYY_REPEAT.format(s=season), re.IGNORECASE)
    while True:
        r = repeat.match(text, pos)
        if not r or int(r.group(1)) <= eps[-1]:
            break
        eps.append(int(r.group(1)))
        pos = r.end()
    return season, eps, m.start()


def _fix_guessit_numbers(clean, spans, season, episodes, media_type):
    """Undo two guessit habits on bare numbers.

    "Title - 107 - Name": guessit splits 107 into season 1 episode 7. For anime
    (absolute numbering) and whenever the split gives episode 0 ("100" -> S1E00)
    the number stays whole.
    "Show 03-20", "Lgds-02_17", "Ozk.02.09": guessit makes an episode range out
    of what is season 3 episode 20 (TV mode). Long or gappy lists are never right
    for one file: an explicit "E02" wins, otherwise the first number."""
    s_spans, e_spans = spans.get("season", ()), spans.get("episode", ())
    if len(episodes) == 1 and s_spans and e_spans:
        ss, es = s_spans[0][1], e_spans[0][1]
        token = re.match(r"\d{3,4}(?![0-9])", clean[ss:])
        before = clean[ss - 1:ss] if ss else ""
        if token and ss < es < ss + token.end() and before.lower() not in ("s", "x", "e") \
                and (media_type == "anime" or episodes[0] == 0):
            return None, [int(token.group())]
    if len(episodes) == 1 and season is None and media_type != "anime":
        # "Iron-Fist-2017-01_13": guessit keeps 13 and drops the season in front of it
        for pair in re.finditer(r"(?<![0-9xXeE])(\d{1,2})[._](\d{2})(?![0-9])", clean):
            if int(pair.group(2)) == episodes[0] and 1 <= int(pair.group(1)) <= 50:
                return int(pair.group(1)), episodes
    if len(episodes) >= 2:
        first, last = episodes[0], episodes[-1]
        if media_type != "anime" and season is None:
            # "6.01", "[5.01]", "11_06", "Ozk.02.09" are season.episode; "03-20" too when
            # read as a range it would be implausibly long
            for pair in re.finditer(r"(?<![0-9xXeE])(\d{1,2})([._-])(\d{2})(?![0-9])", clean):
                a, sep, b = int(pair.group(1)), pair.group(2), int(pair.group(3))
                if {a, b} != {first, last} or not 1 <= a <= 50:
                    continue
                bracketed = pair.start() > 0 and clean[pair.start() - 1] in "(["
                if sep in "._" or (b > a and b - a + 1 > 4 and not bracketed):
                    return a, [b]
        written_as_range = re.search(
            r"(?<![0-9])0*%d(?:\s*[-~&+xX]\s*\d{1,4})*?\s*[-~&+xX]\s*(?:EP?\s*)?0*%d(?![0-9])" % (first, last),
            clean, re.IGNORECASE)
        contiguous = all(b == a + 1 for a, b in zip(episodes, episodes[1:]))
        if (len(episodes) > 3 or not contiguous) and not written_as_range:
            # "Chernobyl E02 1 23 45", "Title - 39 (137)", "Mob.Psycho.100.II.E10":
            # numbers far apart are stray numbers, not a multi-episode file
            m = _EXPLICIT_EP.search(clean)
            return season, [int(m.group(1))] if m else [first]
    return season, episodes


def _split_title_season(title: str):
    """'Title S2' / 'Title 2nd Season' / 'Title (2014)' / '庆余年第二季' -> (title, season, year)."""
    season = year = None
    m = re.search(r"[\s._]*\((?P<y>(?:19|20)\d{2})\)$", title)
    if m and m.start() > 0:
        year, title = int(m.group("y")), title[:m.start()]
    for rx in _TITLE_SEASON_SUFFIX:
        m = rx.search(title)
        if m and m.start() > 0:
            n = _cjk_number(m.group("n"))
            if n is not None:
                season, title = n, title[:m.start()]
                break
    return title.strip(" ._-"), season, year


def _title_words_only(text: str) -> bool:
    """True if text holds only words that read as part of a title, not release tags."""
    for w in re.split(r"[\s._()\[\]-]+", text):
        if not w:
            continue
        if (w.isupper() and len(w) > 1) or w.lower() in _TAG_WORDS or any(c.isdigit() for c in w):
            return False
    return True


# Names made only of [bracket] groups, common for Chinese releases:
# "[GM-Team][国漫][诛仙][Jade Dynasty][2022][11][HEVC][GB][4K]"
_ALL_BRACKETS = re.compile(r"^(?:\s*[\[【][^\]】]*[\]】])+\s*$")
_BRACKET_TAGS = {"国漫", "日漫", "动画", "番剧", "新番", "繁体", "简体", "简繁", "繁體", "簡體", "中字", "双语",
                 "gb", "big5", "chs", "cht", "jpn", "raw", "batch", "bd", "tv", "web", "end", "fin"}
_TAG_KEYS = {"screen_size", "source", "video_codec", "audio_codec", "crc32", "container", "language",
             "subtitle_language", "other", "video_profile", "audio_channels", "streaming_service"}


def _parse_bracket_name(clean: str, media_type: str) -> Optional["ReleaseInfo"]:
    groups = [g.strip() for g in re.findall(r"[\[【]([^\]】]*)[\]】]", clean) if g.strip()]
    if media_type == "movie":
        if not groups:
            return None
        return parse_release_name(" ".join(groups), media_type, folder=True)
    if len(groups) < 3:
        return None
    info = ReleaseInfo(raw=dict(_run_guessit(" ".join(groups), "episode")[0]))
    latin = cjk = None
    for g in groups[1:]:                 # the first group is the release group
        num = re.fullmatch(r"(\d{1,4})(?:[vV]\d)?", g)
        if num:
            n = int(num.group(1))
            if len(num.group(1)) == 4 and 1900 <= n <= 2099:
                info.year = info.year or n
            elif not info.episodes:
                info.episodes = [n]
            continue
        if g.lower() in _BRACKET_TAGS or re.fullmatch(r"[A-Za-z]{2,4}(?:[_&+][A-Za-z]{2,4})+", g):
            continue
        if set(_run_guessit(g, "")[0]) & _TAG_KEYS or (g.isupper() and len(g) <= 5):
            continue
        if re.search(r"[A-Za-z]{3}", g):
            latin = latin or _spaced(g)
        elif re.search(r"\w", g):
            cjk = cjk or _spaced(g)
    title = latin or cjk
    if not title:
        return None
    info.title = title
    info.title_strong = True
    return info


def parse_release_name(name: str, media_type: str = "tv", folder: bool = False) -> ReleaseInfo:
    """Parse one file or folder name (no path) into a ReleaseInfo.

    folder: the name is a folder. guessit's "episode" hint is not used for
    folders: it turns titles like "Babylon 5" or "Adam-12" into episode numbers."""
    info = ReleaseInfo()
    if not name or not name.strip():
        return info
    stem = name if folder else strip_media_extension(name)
    clean = _SITE_PREFIX.sub("", stem, count=1).strip() or stem
    clean = _YYMMDD_PREFIX.sub("", clean, count=1) or clean
    if _ALL_BRACKETS.match(clean):
        bracketed = _parse_bracket_name(clean, media_type)
        if bracketed is not None:
            return bracketed
    is_movie = media_type == "movie"
    kind = "movie" if is_movie else ("" if folder else "episode")
    raw, spans = _run_guessit(clean, kind)
    info.raw = dict(raw)
    if _RANDOM_NAME.match(clean):
        info.obfuscated = True
        return info

    def first(key):
        s = spans.get(key) or ()
        return s[0] if s else None

    def covered(start, end, keys, allow_untagged=False):
        """Every non-separator character in clean[start:end] is tagged with one of keys
        (or untagged, if allowed). guessit's season/episode spans leave out their
        "S"/"E"/"Season" prefix, so untagged text is only safe before a year."""
        if any(ch in "[]()" for ch in clean[start:end]):
            return False
        ok = [(s, e) for k in keys for _, s, e in spans.get(k, ())]
        other = [(s, e) for k, v in spans.items() if k not in keys and k != "title" for _, s, e in v]
        for i in range(start, end):
            if clean[i] in " ._-" or any(s <= i < e for s, e in ok):
                continue
            if allow_untagged and not any(s <= i < e for s, e in other):
                continue
            return False
        return True

    lead = _LEADING_GROUP.match(clean)
    base = lead.end() if lead else 0
    markers = []          # where season/episode/date information starts
    inner_marks = []      # markers found by our own patterns (may sit inside guessit's title)

    # ── title ──────────────────────────────────────────────────────
    t = first("title")
    title, tstart, tend = (str(t[0]), t[1], t[2]) if t else ("", None, None)
    if title and tstart is not None and tstart > base:
        gap = clean[base:tstart]
        trailing_group = re.search(r"-[A-Za-z0-9]{2,}(?:\[[^\]]*\])?$", clean)
        if re.fullmatch(r"[A-Z][a-z']*-", gap) or (re.fullmatch(r"[a-z][a-z']+-", gap) and trailing_group):
            # guessit reads "Spider-Man.No.Way.Home" as group "Spider" + title "Man No Way Home"
            title, tstart = gap + title, base
        elif gap.strip(" ._-") and covered(base, tstart, _TITLE_WORD_KEYS) and _title_words_only(gap):
            # "Au bout c'est la mer": guessit took "Au" for a country code
            title, tstart = _spaced(clean[base:tend]), base

    has_explicit = bool(_SXXEYY.search(clean) or _NXNN.search(clean))
    info.explicit = has_explicit
    word_ep = None if (has_explicit or is_movie) else _EPISODE_WORD.search(clean, base)
    fansub = None
    if not has_explicit and not word_ep:
        for rx in (_FANSUB, _FANSUB_NODASH):
            fansub = rx.match(clean)
            if fansub and not fansub.group("ep2") and 1900 <= int(fansub.group("ep")) <= 2099:
                fansub = None          # "[Group] Movie Title - 2019 [1080p]": a year, not an episode
            if fansub:
                break
    fansub_season = None
    if fansub:
        title = re.sub(r"\s+", " ", fansub.group("title").replace("_", " ")).strip(" .-")
        tstart, tend = fansub.start("title"), fansub.end("title")
        title, fansub_season, fs_year = _split_title_season(title)
        if fs_year:
            info.year = fs_year
        e1 = int(fansub.group("ep"))
        e2 = int(fansub.group("ep2")) if fansub.group("ep2") else None
        info.episodes = list(range(e1, e2 + 1)) if e2 and e1 < e2 <= e1 + 100 else [e1]
        markers.append(fansub.start("ep"))
    elif word_ep:
        # Keep guessit's title unless it is missing or runs into the "Episode" word
        # ("[Group] Anime Super Episode 161" -> guessit says "Anime Super Episode")
        gap = clean[tend:word_ep.start()] if (title and tend is not None and tend <= word_ep.start()) else ""
        gap_plain = re.sub(r"\[[^\]]*\]|\(\s*(?:19|20)\d{2}\s*\)", " ", gap)
        if gap and gap_plain.strip(" ._-") and not re.search(r"[\[\]()]|\s-\s", gap_plain) \
                and not any(k not in ("title", "episode", "year") and s < word_ep.start() and e > tend
                            for k, v in spans.items() for _, s, e in v):
            ym = re.search(r"\(((?:19|20)\d{2})\)", gap)
            if ym:
                info.year = int(ym.group(1))
            title, tend = _spaced(clean[tstart:tend] + gap_plain), word_ep.start()
        if not title or tstart is None or tend is None or tstart >= word_ep.start() \
                or tend > word_ep.start() + 1:
            pre = clean[base:word_ep.start()]
            ym = re.search(r"\(((?:19|20)\d{2})\)", pre)
            if ym:
                info.year = int(ym.group(1))
            pre = re.sub(r"\[[^\]]*\]|\(\s*(?:19|20)\d{2}\s*\)", " ", pre)
            if _spaced(pre):
                title, tstart, tend = _spaced(pre), base, word_ep.start()
    else:
        num = _NUMERIC_TITLE.match(clean, base)
        if num and (not title or tstart is None or tstart > num.start(1)):
            title, tstart, tend = num.group(1), num.start(1), num.end(1)

    # ── year ───────────────────────────────────────────────────────
    years = [y for y in spans.get("year", ()) if not (tstart is not None and y[1] == tstart)]
    year = info.year
    ystart = None
    if year is None and years:
        year, ystart = int(years[0][0]), years[0][1]
    # (guessit's season/episode spans leave out the "S"/"E" in front, so also use our own marker positions)
    first_mark = min([v[0][1] for k, v in spans.items() if k in ("season", "episode", "date", "year") and v]
                     + [m.start() for m in (_SXXEYY.search(clean), _NXNN.search(clean), _EXPLICIT_EP.search(clean))
                        if m], default=None)
    if not is_movie and not title and first_mark is not None and first_mark > base \
            and not re.search(r"[\[\]()]", clean[base:first_mark]) and _spaced(clean[base:first_mark]):
        title, tstart, tend = _spaced(clean[base:first_mark]), base, first_mark
    if is_movie and not title and ystart is not None and ystart > base \
            and not re.search(r"[\[\]()]", clean[base:ystart]) and _spaced(clean[base:ystart]):
        # "Us.2019", "Opus.2025", "F1.2024": guessit took the whole title for a tag
        title, tstart, tend = _spaced(clean[base:ystart]), base, ystart
    if is_movie and title and ystart is not None and tstart is not None and ystart < tstart \
            and clean[base:ystart].strip(" ._-()[]"):
        # "xXx.2002.15th.Anniversary.Edition": the title is what comes before the year
        title, tstart, tend = _spaced(clean[base:ystart]), base, ystart

    # ── season / episodes ──────────────────────────────────────────
    g_seasons = _ints(v for v, _, _ in spans.get("season", ()))
    g_eps = _ints(v for v, _, _ in spans.get("episode", ()))
    for k in ("season", "episode"):
        markers.extend(s for _, s, _ in spans.get(k, ()))
    season = g_seasons[0] if g_seasons else None
    if season is not None and season >= 1900:     # guessit sometimes files the year as the season
        year = year or season
        season = None
    episodes = info.episodes if fansub else list(dict.fromkeys(g_eps))
    if not fansub:
        season, episodes = _fix_guessit_numbers(clean, spans, season, episodes, media_type)
    explicit = None if fansub else _parse_sxxeyy(clean)
    override_start = None
    if explicit:
        season, episodes, pos = explicit
        markers.append(pos)
        inner_marks.append(pos)
        override_start = pos
    elif not fansub:
        matched = False
        for rx in _SEASON_EPISODE_OVERRIDES:
            m = rx.search(clean)
            if m:
                e1 = int(m.group("e"))
                e2 = int(m.group("e2")) if "e2" in rx.groupindex and m.group("e2") else None
                season = 0 if "sp" in rx.groupindex else int(m.group("s"))
                episodes = list(range(e1, e2 + 1)) if e2 and e1 < e2 <= e1 + 100 else [e1]
                cut = m.start("abs") if "abs" in rx.groupindex and m.group("abs") else m.start()
                markers.append(cut)
                inner_marks.append(cut)
                override_start = cut
                matched = True
                break
        if not matched and word_ep:
            e1 = int(word_ep.group("e"))
            e2 = int(word_ep.group("e2")) if word_ep.group("e2") else None
            episodes = list(range(e1, e2 + 1)) if e2 and e1 < e2 <= e1 + 100 else [e1]
            dotted = re.match(r"\.(\d{2})(?![0-9])", clean[word_ep.end():])
            if dotted and not e2:
                season, episodes = e1, [int(dotted.group(1))]     # "Episode 1.22" = S01E22
            markers.append(word_ep.start())
            override_start = word_ep.start()
        elif not matched:
            m = _SCENE_MULTI.search(clean)
            if m and int(m.group("s") + m.group("e1")) < 1900 and int(m.group("e1")) >= 1 \
                    and 1 <= int(m.group("e2")) - int(m.group("e1")) <= 3:
                season = int(m.group("s"))
                episodes = list(range(int(m.group("e1")), int(m.group("e2")) + 1))
                markers.append(m.start())
                inner_marks.append(m.start())
                override_start = m.start()
    if fansub:
        season = fansub_season if fansub_season is not None else season
        if season is not None and season >= 1900:
            season = None
    if season is not None and season >= 1900 and year == season:
        year = None
    m = _EPISODE_OF.search(clean)
    if m and not explicit and not fansub:
        episodes = [int(m.group("e") or m.group("e2"))]
        markers.append(m.start())
        inner_marks.append(m.start())

    if override_start is not None and title and tstart is not None and tend is not None \
            and override_start > tend:
        between = clean[tend:override_start]
        in_year = any(tend <= y[1] < override_start for y in years)
        if not in_year and re.fullmatch(r"[\s._]*\d{1,3}[\s._]*", between):
            # "Mobile_Suit_Gundam_00_Season_2_Ep07", "Babylon.5.S01E02": the number is part of the title
            title, tend = _spaced(clean[tstart:override_start]), override_start

    # Fallbacks for names guessit found nothing in
    if season is None:
        for rx in _SEASON_FALLBACKS:
            m = rx.search(clean)
            if m:
                n = _cjk_number(m.group("n"))
                if n is not None and n < 1900:
                    season = n
                    markers.append(m.start())
                    inner_marks.append(m.start())
                    break
    if not episodes:
        for rx in _EPISODE_FALLBACKS:
            m = rx.search(clean)
            if m:
                n = _cjk_number(m.group("n"))
                if n is not None:
                    episodes = [n]
                    markers.append(m.start())
                    inner_marks.append(m.start())
                    break
    if not episodes:
        for rx in _SEASON_EPISODE_FALLBACKS:
            m = rx.search(clean)
            if m:
                e1 = int(m.group("e"))
                e2 = int(m.group("e2")) if "e2" in rx.groupindex and m.group("e2") else None
                season = int(m.group("s"))
                episodes = list(range(e1, e2 + 1)) if e2 and e1 < e2 <= e1 + 100 else [e1]
                markers.append(m.start())
                inner_marks.append(m.start())
                break
    if not episodes and not is_movie and not folder:
        m = _TITLE_DASH_EPISODE.match(clean, base)
        if m and not (1900 <= int(m.group("ep")) <= 2099):
            episodes = [int(m.group("ep"))]
            markers.append(m.start("ep"))
            if not title:
                title, tstart, tend = _spaced(m.group("title")), m.start("title"), m.end("title")

    if not episodes and not is_movie and not folder:
        prt = first("part")
        if prt and _ints([prt[0]]):
            episodes = [_ints([prt[0]])[0]]
            word = re.search(r"part[\s._-]*$", clean[:prt[1]], re.I)
            cut = word.start() if word else prt[1]
            markers.append(cut)
            inner_marks.append(cut)

    if not episodes and not is_movie and not folder:
        dashes = [m for m in re.finditer(r"[\s_]+-[\s_]+(?P<ep>\d{1,4})(?:[vV]\d)?(?=[\s_.\[(]|$)", clean)
                  if not 1900 <= int(m.group("ep")) <= 2099]
        if len(dashes) == 1:
            episodes = [int(dashes[0].group("ep"))]
            markers.append(dashes[0].start("ep"))
        elif title and tend is not None and season is None:
            m = re.match(r"[\s._]?(?P<s>[1-9])(?P<e>\d{2})(?=[\s._-])", clean[tend - 1:] if tend else "")
            if m and int(m.group("e")) >= 1:          # scene "101" = S01E01, right after the title
                season, episodes = int(m.group("s")), [int(m.group("e"))]
                markers.append(tend)

    # guessit occasionally returns stray numbers as extra episodes ([0, 7], [100, 1])
    if len(episodes) > 1 and any(b <= a for a, b in zip(episodes, episodes[1:])):
        m = _EXPLICIT_EP.search(clean)
        episodes = [int(m.group(1))] if m else [episodes[-1]]

    # ── air date (daily shows) ─────────────────────────────────────
    date, date_span = None, (0, 0)
    d = first("date")
    if d and isinstance(d[0], datetime.date):
        date, date_span = d[0], (d[1], d[2])
    else:
        m = _YMD_DATE.search(clean)
        if m:
            y, mo, dd = int(m.group(1)), int(m.group(3)), int(m.group(4))
            if mo > 12 >= dd:
                mo, dd = dd, mo                         # "2012.16.02": year.day.month
            try:
                date = datetime.date(y, mo, dd)
                date_span = m.span()
            except ValueError:
                date = None
    if date:
        markers.append(date_span[0])
    if date and not is_movie and not explicit and override_start is None and not fansub:
        ep_spans = [s for k in ("season", "episode") for _, s, _ in spans.get(k, ())]
        # An episode number written before the date ("Duckman - 101 (01) - 20021107",
        # "Show 69. Bölüm (29.10.2023)") is real; stray numbers after it ("- Friday July 3",
        # "The.170.Million...") are part of the episode title, and the date wins.
        marked = bool(_EXPLICIT_EP.search(clean)) or bool(_NXNN.search(clean)) \
            or any(s < date_span[0] for s in ep_spans)
        if not episodes or not marked or all(date_span[0] <= s < date_span[1] for s in ep_spans):
            # the date's own digits are not a season/episode ("Show_2018_06_22")
            season, episodes = None, []
            if year == date.year:
                year = None
    if is_movie and year is None and date:
        year = date.year

    # ── title clean-up ─────────────────────────────────────────────
    marker_start = min(markers) if markers else None
    if title and tstart is not None and tend is not None and inner_marks:
        cut = min(inner_marks)
        if tstart < cut < tend:
            # "Die.Simpsons.Staffel.2.Folge.5", "庆余年第二季": the marker was inside guessit's title
            title, tend = _spaced(clean[tstart:cut]), cut

    if title and tend is not None and not fansub:
        limit = ystart if is_movie else marker_start
        if is_movie:
            # "Dune.Part.Two", "Kill.Bill.Vol.1": guessit splits these off the title
            for key in ("part", "volume"):
                p = first(key)
                if p and p[1] >= tend - 1 and re.fullmatch(r"[\s._-]*(?:part|vol(?:ume)?)?[\s._-]*",
                                                           clean[tend:p[1]], re.I):
                    title, tend = _spaced(clean[tstart:p[2]]), p[2]
        if limit is not None and limit > tend and clean[tend:limit].strip(" ._-") \
                and covered(tend, limit, _TITLE_WORD_KEYS, allow_untagged=is_movie and limit == ystart) \
                and _title_words_only(clean[tend:limit]):
            # "The.Collector.2009", "It.Ends.With.Us.2024", "Road.to.the.NHL.Winter.Classic.S07E01"
            title, tend = _spaced(clean[tstart:limit]), limit
        if not is_movie:
            # "The.Office.US.S01E01" -> "The Office (US)"
            c = first("country")
            if c and c[1] >= tend - 1 and not clean[tend:c[1]].strip(" ._-()"):
                code = clean[c[1]:c[2]].strip(" ._-()").upper()
                if 2 <= len(code) <= 3 and code.isalpha() and clean[c[1]:c[2]].strip(" ._-()").isupper():
                    title = f"{title} ({code})"
    if is_movie and year is None and title:
        m = re.search(r"\s((?:18[89]|19[01])\d)$", title)    # guessit 3.x rejects years before 1920
        if m and m.start() > 0:
            year, title = int(m.group(1)), title[:m.start()]
        else:
            m = re.search(r"\((18[89]\d|19[01]\d)\)", clean)
            if m:
                year = int(m.group(1))
    if not is_movie and title:
        title, t_season, t_year = _split_title_season(title)
        if season is None and t_season is not None:
            season = t_season
        year = year or t_year
    title = re.sub(r"\s+", " ", title).strip(" ._-")
    mixed = re.fullmatch(r"[\u3000-\u30ff\u3400-\u9fff\uac00-\ud7af\uff00-\uffef·・\s]+\s+([A-Za-z].*)", title)
    if mixed:
        title = mixed.group(1)

    # TV: a year after the episode marker is an air date, not part of the show's name
    if not is_movie and year and ystart is not None and marker_start is not None and ystart > marker_start:
        year = None

    first_token = re.split(r"[\s.]", clean.strip())[0] if clean.strip() else ""
    info.obfuscated = bool(_OBFUSCATED.match(first_token)) and " " not in title
    junk = title.lower() in _JUNK_FILE_TITLES and not (has_explicit or year)
    info.title = "" if junk else title
    info.title_strong = bool(info.title) and not info.obfuscated and (
        marker_start is None or tstart is None or tstart < marker_start) and not (
        info.title.isdigit() and not has_explicit and not is_movie)
    info.year = year
    info.season = season
    info.episodes = episodes
    info.date = date if (not is_movie and not episodes) else None
    et = first("episode_title")
    info.episode_title = _spaced(str(et[0])) if et else ""
    return info


# ── folders ──────────────────────────────────────────────────────────
_SEASON_WORDS_FOLDER = (r"season|saison|temporada|staffel|stagione|seizoen|sezona?|s[äa]song|s[æa]son"
                        r"|sesong|kausi|s")
_SEASON_FOLDER = re.compile(r"^(?:" + _SEASON_WORDS_FOLDER + r")[\s._-]*0*(?P<n>\d{1,3})(?![0-9])", re.I)
# Folders that hold a show's files but are not the show folder: season, extras, subtitle folders...
_SEASONISH_FOLDER_X = re.compile(
    r"^(?:(?:" + _SEASON_WORDS_FOLDER + r")[\s._-]*\d{1,3}(?:[\s._-].*)?"
    r"|s\d{1,3}e\d{1,4}"
    r"|specials?|extras?|minisodes?|featurettes?|bonus(?:es)?|subs?|subtitles?|samples?"
    r"|ovas?|oads?|webisodes?|shorts|behind the scenes|deleted scenes|interviews|trailers"
    r"|第\s*\S+\s*季)$",
    re.IGNORECASE)
# Folder names that are a release name rather than a library-style show folder
_RELEASE_TAG_KEYS = {"screen_size", "source", "video_codec", "audio_codec", "release_group",
                     "streaming_service", "video_profile", "audio_channels", "other", "edition",
                     "website", "crc32", "subtitle_language", "episode_details"}
_EXPLICIT_SEASON_TEXT = re.compile(
    r"(?<![A-Za-z0-9])(?:S\d{1,3}(?:E\d{1,4})?|(?:" + _SEASON_WORDS + r")[\s._-]*\d{1,3}|\d{1,2}x\d{2})(?![0-9])"
    r"|第\s*\S+?\s*[季期部]|シーズン",
    re.IGNORECASE)


def season_from_folder(name: str) -> Optional[int]:
    """'Season 3', 'S03', 'Saison 3', 'Staffel 3', '第3季' -> 3; 'Specials' -> 0."""
    if not name:
        return None
    n = name.strip()
    if re.fullmatch(r"specials?", n, re.I):
        return 0
    m = _SEASON_FOLDER.match(n) or _CJK_SEASON.search(n)
    if m:
        v = _cjk_number(m.group("n"))
        return v if v is not None and v < 1900 else None
    return None


def is_seasonish_folder(name: str) -> bool:
    return bool(name and _SEASONISH_FOLDER_X.match(name.strip()))


def _norm_title(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold().replace("&", " and ")
    s = re.sub(r"\(\s*(?:19|20)\d{2}\s*\)", " ", s)
    s = re.sub(r"[\W_]+", " ", s).strip()
    return re.sub(r"^the\s+", "", s)


def titles_agree(a: str, b: str) -> bool:
    """Loose 'same show' test: equal, one a prefix of the other, or high token overlap."""
    na, nb = _norm_title(a), _norm_title(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    ca, cb = na.replace(" ", ""), nb.replace(" ", "")
    if (ca.startswith(cb) or cb.startswith(ca)) and min(len(ca), len(cb)) >= 3:
        return True
    return bool(_fuzz) and _fuzz.token_set_ratio(na, nb) >= 90


def is_acronym(short: str, long: str) -> bool:
    """'tbbt' / 'The Big Bang Theory'."""
    s = re.sub(r"[\W_]+", "", short or "").lower()
    words = re.sub(r"[\W_]+", " ", (long or "").lower()).split()
    if len(s) < 2 or len(words) < 2:
        return False
    initials = "".join(w[0] for w in words)
    return s == initials or (words[0] == "the" and s == initials[1:])


@dataclass
class MediaGuess:
    """Final answer for one file: what to name it."""
    title: str = ""
    year: Optional[int] = None
    season: Optional[int] = None
    episodes: List[int] = field(default_factory=list)
    date: Optional[datetime.date] = None
    episode_title: str = ""
    title_source: str = ""       # "file", "folder" or ""
    raw: dict = field(default_factory=dict)


def _folder_title(name: str, media_type: str) -> Tuple[str, Optional[int], ReleaseInfo]:
    """Title (and year) a folder name stands for, or '' for generic folders."""
    if not name:
        return "", None, ReleaseInfo()
    info = parse_release_name(name, media_type, folder=True)
    year = info.year
    if not (set(info.raw) & _RELEASE_TAG_KEYS) and not _EXPLICIT_SEASON_TEXT.search(name) \
            and not _LEADING_GROUP.match(name):
        # Library-style folder ("Babylon 5", "The 4400", "Doctor Who (2005)"): the
        # name is the title; guessit would read the numbers as episodes.
        title = _spaced(name)
        m = re.search(r"[\s._]*\(?((?:19|20)\d{2})\)?$", title)
        if m and m.start() > 0:
            year, title = int(m.group(1)), title[:m.start()].strip(" -")
        lang = re.search(r"\s*\(([^()]+)\)$", title)
        if lang and lang.start() > 0 and _run_guessit(lang.group(1), "")[0].get("language"):
            title = title[:lang.start()]          # "Dr Slump (Catalan)"
    else:
        title = info.title
    # "9 (2009)", "1917 (2019)", "24.S01E02...": a number next to a year or SxxEyy is a title
    numeric_ok = bool(year) or bool(re.match(r"^\d{1,4}(?:-\d{1,3})?[\s._-]+(?:S\d|\d{1,2}x\d)", name, re.I))
    if not (title and is_valid_show_name(title, allow_numeric=numeric_ok)):
        cleaned = clean_show_folder(name)
        title = cleaned if cleaned and is_valid_show_name(cleaned) else ""
    return title, year if title else None, info


def _pick_title(file_title: str, folder_title: str) -> str:
    """Both name the same show: take the more complete one ("Adam-12" over
    "Adam", "Star Wars Rebels" over "Star Wars"); the folder on a tie."""
    return file_title if len(_norm_title(file_title)) > len(_norm_title(folder_title)) else folder_title


def _clean_episode_title(text: str) -> str:
    """guessit's episode title can end in a release tag ("Johnny Knoxville iTouch-MW")."""
    words = text.split()
    if len(words) > 1 and "-" in words[-1]:
        words = words[:-1]
    return " ".join(words)


def analyse_path(path, media_type: str = "tv") -> MediaGuess:
    """Combine the file name and its folders into one answer.

    The file name decides when it carries the title itself. The folder decides
    when the file name has no title (or only an episode title or a scene short
    name), and gives the cleaner spelling when both agree."""
    path = Path(path)
    parts = path.parts
    f = parse_release_name(path.name, media_type)
    g = MediaGuess(raw=f.raw, episode_title=_clean_episode_title(f.episode_title))

    parent = parts[-2] if len(parts) >= 2 else ""
    folder_season = season_from_folder(parent)
    idx = len(parts) - 2
    if parent and is_seasonish_folder(parent) and idx >= 1:
        idx -= 1
    # Obfuscated folder ("Movie (2013) .../XD607ebb-BRc59935-.../file.avi"): look one level higher
    for _ in range(2):
        if idx >= 1 and parse_release_name(parts[idx], media_type, folder=True).obfuscated:
            idx -= 1
    show_dir = parts[idx] if idx >= 0 and len(parts) >= 2 else ""
    folder_title, folder_year, folder_info = _folder_title(show_dir, media_type)

    # Generic-folder words are only filtered for folders: a file can be called "Series.S01E01".
    file_title = f.title

    if media_type == "movie":
        raw_folder = _norm_title(show_dir)
        if file_title and not f.obfuscated:
            if f.year:
                g.title, g.year, g.title_source = file_title, f.year, "file"
            elif folder_title and folder_year and (
                    titles_agree(file_title, folder_title)
                    or (_norm_title(file_title) and _norm_title(file_title) in raw_folder)):
                g.title, g.year, g.title_source = folder_title, folder_year, "folder"
            else:
                g.title, g.title_source = file_title, "file"
        elif folder_title:
            g.title, g.year, g.title_source = folder_title, folder_year or f.year, "folder"
        elif file_title:
            g.title, g.year, g.title_source = file_title, f.year, "file"
        return g

    # TV / anime. A file name with no episode information at all ("abc123.mkv"
    # inside "Show.S01E05.720p-GRP/") is not an episode name: the folder decides.
    agree = bool(file_title and folder_title and titles_agree(file_title, folder_title))
    file_has_episode = bool(f.episodes or f.date or f.explicit)
    if file_title and f.title_strong and (file_has_episode or not folder_title):
        if agree:
            g.title = _pick_title(file_title, folder_title)
            g.title_source = "folder" if g.title == folder_title else "file"
        elif folder_title and is_acronym(file_title, folder_title):
            g.title, g.title_source = folder_title, "folder"
        else:
            g.title, g.title_source = file_title, "file"
    elif folder_title:
        g.title, g.title_source = folder_title, "folder"
    elif file_title:
        g.title, g.title_source = file_title, "file"

    if g.title_source == "folder":
        g.year = folder_year or (f.year if agree else None)
    else:
        g.year = f.year or (folder_year if agree else None)

    g.season, g.episodes, g.date = f.season, list(f.episodes), f.date
    need_folder_eps = not g.episodes and not g.date
    weak_file = not f.explicit and (f.obfuscated or not f.title or f.title.isdigit())
    if need_folder_eps or weak_file:
        # Obfuscated file inside an episode folder: "Show.S01E05.720p-GRP/abc123.mkv",
        # "Zoo.S02E05.1080p.WEB-DL/160725_02.mkv"
        for lvl in (2, 3):
            if len(parts) > lvl:
                info = parse_release_name(parts[-lvl], media_type, folder=True)
                if info.episodes and (need_folder_eps or (info.explicit and len(info.episodes) <= 3)):
                    g.episodes = list(info.episodes)
                    if g.season is None or weak_file:
                        g.season = info.season if info.season is not None else g.season
                    g.date = None
                    break
                if need_folder_eps and info.date:
                    g.date = info.date
                    break
    if g.season is None and g.episodes:
        # "Show/Season 3/07 - Title.mkv" or "Friends S03/Friends - 07.mkv"
        if folder_season is not None:
            g.season = folder_season
        elif folder_info.season is not None:
            g.season = folder_info.season
        elif parent and parent != show_dir:
            g.season = parse_release_name(parent, media_type, folder=True).season
    return g
