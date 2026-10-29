"""Work PathRenamer does on its own after renaming: download missing
subtitles and ask Plex to scan. Plain functions without Qt, run in a
background thread by the window."""
import os
from pathlib import Path
from typing import List

from opensubs import OpenSubtitlesError, subtitle_name
from scanner import SUBTITLE_EXTENSIONS, detect_subtitle_language


def languages_present(video: Path) -> set:
    """Languages of the full (not forced-only) subtitles named after video."""
    video = Path(video)
    stem = video.stem.lower()
    found = set()
    try:
        names = os.listdir(video.parent)
    except OSError:
        return found
    for name in names:
        p = Path(name)
        if p.suffix.lower() in SUBTITLE_EXTENSIONS and name.lower().startswith(stem + "."):
            lang, flags = detect_subtitle_language(p, video.stem)      # no language: English
            if lang and "forced" not in flags:
                found.add(lang)
    return found


def fetch_subtitles(client, video, languages, title="", year=None, season=None, episodes=None,
                    media_type="movie", hearing_impaired=False) -> List[Path]:
    """Download the best subtitle in each wanted language the video doesn't
    have yet, saved next to it with Plex names ("Movie (2010).eng.srt").
    Returns the new files. Raises OpenSubtitlesError (quota, sign-in, ...)."""
    video = Path(video)
    missing = [lang for lang in languages if lang not in languages_present(video)]
    if not missing:
        return []
    episode_search = media_type in ("tv", "anime")
    results = client.search(path=video, languages=missing, query=title,
                            year=None if episode_search else year,
                            season=season if episode_search else None,
                            episode=(episodes[0] if episodes else None) if episode_search else None,
                            is_episode=episode_search)
    best = client.best_per_language(results, missing, hearing_impaired)
    try:
        taken = {n.lower() for n in os.listdir(video.parent)}
    except OSError:
        taken = set()
    saved = []
    for lang in missing:
        result = best.get(lang)
        if result is None:
            continue
        data = client.download(result)
        dst = video.parent / subtitle_name(video, result, taken)
        with open(dst, "xb") as f:             # never overwrites a file
            f.write(data)
        saved.append(dst)
    return saved


def after_rename(videos, paths, media_type, subtitles=None, plex=None):
    """The after-rename job. videos: [(candidate, new video path)] for
    subtitle downloads; paths: every placed file, for the Plex scan.
    subtitles: dict(client, languages, hearing_impaired) or None;
    plex: a PlexClient or None. Returns (downloaded files, scanned library
    names, problems worded for the user)."""
    downloaded, scanned, problems = [], [], []
    if subtitles:
        client = subtitles["client"]
        try:
            client.login()
            for cand, video in videos:
                downloaded += fetch_subtitles(
                    client, video, subtitles["languages"], title=cand.title, year=cand.year or None,
                    season=cand.season, episodes=cand.episodes, media_type=cand.media_type,
                    hearing_impaired=subtitles.get("hearing_impaired", False))
        except OpenSubtitlesError as e:          # quota, sign-in: the rest would fail too
            problems.append(f"Subtitles: {e}")
        except OSError as e:
            problems.append(f"Subtitles: couldn't save a subtitle ({e.strerror or e}).")
    if plex is not None:
        try:
            scanned = plex.scan([str(p) for p in list(paths) + downloaded], media_type)
        except Exception as e:                   # PlexError, or a server answering nonsense
            problems.append(f"Plex: {e}")
    return downloaded, scanned, problems
