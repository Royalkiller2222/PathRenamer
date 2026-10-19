"""Subtitle language/flag detection and Plex subtitle file names.

A subtitle's language and flags (SDH, forced) are read from its "tags": the
part of the name after the video's name ("Movie.en.forced.srt" -> "en.forced"),
or, for subtitles not named after the video ("Subs/2_English.srt",
"Spanish (Latin America).srt"), the trailing words that are language, region
or flag words. Words inside a title are never read as tags, so "The.IT.Crowd"
is not Italian and "Hi.Score.Girl" is not hearing-impaired.
"""
import re
from pathlib import Path
from typing import List, Optional, Tuple

SUBTITLE_EXTENSIONS = {
    ".srt", ".sub", ".ass", ".ssa", ".vtt", ".idx", ".sup", ".smi",
}

# ISO 639-2/B code (what Plex expects) -> names and other codes for it.
_LANGUAGES = {
    "eng": ("en", "english"),
    "spa": ("es", "spanish", "espanol", "español", "castellano", "castilian", "latino"),
    "fre": ("fr", "fra", "french", "francais", "français"),
    "ger": ("de", "deu", "german", "deutsch"),
    "ita": ("it", "italian", "italiano"),
    "por": ("pt", "pob", "ptbr", "portuguese", "portugues", "português", "brazilian"),
    "dut": ("nl", "nld", "dutch", "nederlands", "flemish"),
    "rus": ("ru", "russian"),
    "jpn": ("ja", "jp", "japanese"),
    "kor": ("ko", "korean"),
    "chi": ("zh", "zho", "chs", "cht", "big5", "chinese", "mandarin", "cantonese"),
    "ara": ("ar", "arabic"),
    "hin": ("hindi",),          # "hi" means hearing-impaired in subtitle names
    "tur": ("tr", "turkish"),
    "pol": ("pl", "polish"),
    "swe": ("sv", "swedish"),
    "dan": ("da", "danish"),
    "nor": ("no", "nb", "nn", "nob", "nno", "norwegian"),
    "fin": ("fi", "finnish"),
    "hun": ("hu", "hungarian"),
    "cze": ("cs", "ces", "cz", "czech"),
    "rum": ("ro", "ron", "romanian"),
    "vie": ("vi", "vietnamese"),
    "tha": ("th", "thai"),
    "heb": ("he", "iw", "hebrew"),
    "gre": ("el", "ell", "gr", "greek"),
    "ukr": ("uk", "ukrainian"),
    "bul": ("bg", "bulgarian"),
    "hrv": ("hr", "croatian"),
    "srp": ("sr", "serbian"),
    "slv": ("sl", "slovenian", "slovene"),
    "slo": ("sk", "slk", "slovak"),
    "est": ("et", "estonian"),
    "lav": ("lv", "latvian"),
    "lit": ("lt", "lithuanian"),
    "cat": ("ca", "catalan"),
    "baq": ("eu", "eus", "basque"),
    "glg": ("gl", "galician"),
    "ice": ("is", "isl", "icelandic"),
    "per": ("fa", "fas", "persian", "farsi"),
    "urd": ("ur", "urdu"),
    "ben": ("bn", "bengali"),
    "tam": ("ta", "tamil"),
    "tel": ("te", "telugu"),
    "mal": ("ml", "malayalam"),
    "kan": ("kn", "kannada"),
    "mar": ("mr", "marathi"),
    "ind": ("id", "indonesian"),
    "may": ("ms", "msa", "malay"),
    "fil": ("tl", "tgl", "filipino", "tagalog"),
    "alb": ("sq", "sqi", "albanian"),
    "mac": ("mk", "mkd", "macedonian"),
    "bos": ("bs", "bosnian"),
    "geo": ("ka", "kat", "georgian"),
    "arm": ("hy", "hye", "armenian"),
    "kur": ("ku", "kurdish"),
    "swa": ("sw", "swahili"),
    "afr": ("af", "afrikaans"),
    "wel": ("cy", "cym", "welsh"),
    "gle": ("ga", "irish"),
}
LANGUAGE_WORDS = {code: code for code in _LANGUAGES}
for _code, _aliases in _LANGUAGES.items():
    for _alias in _aliases:
        LANGUAGE_WORDS[_alias] = _code

# Region parts of names like "pt-BR", "es-419", "zh-Hans", "Spanish (Latin America)"
REGION_WORDS = {
    "us", "gb", "uk", "au", "ca", "ie", "nz", "in", "br", "pt", "es", "mx", "419",
    "latam", "la", "cn", "tw", "hk", "sg", "hans", "hant", "simplified",
    "traditional", "latin", "america", "american", "canada", "canadian", "brazil",
    "european", "mexico", "portugal", "spain", "france", "taiwan", "mainland",
}
SDH_WORDS = {"sdh", "cc", "hi", "hoh", "deaf", "hearing", "impaired"}
FORCED_WORDS = {"forced", "foreign"}
# Words that can sit among the tags without meaning anything for Plex
IGNORED_WORDS = {"default", "full", "normal", "subs", "sub", "subtitle", "subtitles"}

TAG_WORDS = set(LANGUAGE_WORDS) | REGION_WORDS | SDH_WORDS | FORCED_WORDS | IGNORED_WORDS

_SPLIT = re.compile(r"[\W_]+", re.UNICODE)


def _tokens(text: str) -> List[str]:
    return [t for t in _SPLIT.split(text.lower()) if t]


def subtitle_tags(stem: str, video_stem: Optional[str] = None) -> List[str]:
    """The words of a subtitle name that carry its language and flags."""
    if video_stem and stem.lower().startswith(video_stem.lower()):
        # Named after the video: everything after the video's name is tags
        return _tokens(stem[len(video_stem):])
    tokens = _tokens(stem)
    body = tokens[1:] if len(tokens) > 1 and tokens[0].isdigit() else tokens   # "2_English"
    if body and all(t in TAG_WORDS for t in body):
        return body                      # "English (SDH)", "Spanish (Latin America)"
    i = len(tokens)
    while i > 0 and tokens[i - 1] in TAG_WORDS:
        i -= 1
    return tokens[i:]                    # "Show.S01E01.720p-GRP.en.forced" -> en, forced


def is_language_only_name(stem: str) -> bool:
    """"English", "2_eng", "English (SDH)": a subtitle named only by its language."""
    tokens = _tokens(stem)
    body = tokens[1:] if len(tokens) > 1 and tokens[0].isdigit() else tokens
    return bool(body) and all(t in TAG_WORDS for t in body) and any(
        t in LANGUAGE_WORDS or t in SDH_WORDS or t in FORCED_WORDS for t in body)


def detect_subtitle_language(sub_path: Path, video_stem: Optional[str] = None,
                             default: str = "eng") -> Tuple[str, List[str]]:
    """(ISO 639-2/B language code, flags) for a subtitle file.

    flags holds "sdh" and/or "forced". video_stem is the name of the video the
    subtitle belongs to (its original name), when known. Without a language in
    the name the subtitle is taken to be in `default`.
    """
    tags = subtitle_tags(Path(sub_path).stem, video_stem)
    flags = []
    if any(t in SDH_WORDS for t in tags):
        flags.append("sdh")
    if any(t in FORCED_WORDS for t in tags):
        flags.append("forced")
    lang = None
    for i in range(len(tags) - 1, -1, -1):
        t = tags[i]
        if t in LANGUAGE_WORDS:
            if t in REGION_WORDS and i > 0 and tags[i - 1] in LANGUAGE_WORDS:
                continue                 # "fr-CA": CA is the region, not Catalan
            lang = LANGUAGE_WORDS[t]
            break
    return lang or default, flags


def format_subtitle_filename(video_stem: str, lang: str, ext: str,
                             flags: Optional[List[str]] = None,
                             index: Optional[int] = None) -> str:
    """Plex subtitle name: {video name}[.{index}].{lang}[.sdh][.forced]{ext}

    e.g. "Show S02E01 - Title.eng.srt", "Show S02E01 - Title.eng.forced.srt".
    index tells apart several subtitles with the same language and flags; it
    goes before the language because Plex reads the language from the end.
    """
    parts = [video_stem]
    if index:
        parts.append(str(index))
    parts.append(lang)
    parts.extend(flags or [])
    return ".".join(parts) + ext


def unique_subtitle_filename(video_stem: str, lang: str, ext: str, flags, taken: set) -> str:
    """format_subtitle_filename, numbered (.2., .3., ...) when the name is
    already in `taken` (lower-case names). Adds the result to `taken`."""
    index = None
    while True:
        name = format_subtitle_filename(video_stem, lang, ext, flags, index)
        if name.lower() not in taken:
            taken.add(name.lower())
            return name
        index = (index or 1) + 1
