"""After a rename: missing subtitles are downloaded with Plex names and the media server
is asked to scan."""
from pathlib import Path
from types import SimpleNamespace

from automation import after_rename, fetch_subtitles, languages_present
from opensubs import OpenSubtitlesClient, OpenSubtitlesError, SubtitleResult


class FakeSubs:
    """Answers like OpenSubtitlesClient without the network."""

    def __init__(self, results, fail_on_download=None):
        self.results = results
        self.searches, self.downloads, self.logins = [], [], 0
        self.fail_on_download = fail_on_download

    def login(self):
        self.logins += 1

    def search(self, **kw):
        self.searches.append(kw)
        return [r for r in self.results if r.language in kw["languages"]]

    best_per_language = staticmethod(OpenSubtitlesClient.best_per_language)

    def download(self, result):
        if self.fail_on_download:
            raise self.fail_on_download
        self.downloads.append(result.file_id)
        return f"subtitle {result.file_id}".encode()


def _video(folder, name, *subs):
    folder.mkdir(parents=True, exist_ok=True)
    v = folder / name
    v.write_bytes(b"x")
    for s in subs:
        (folder / s).write_text("1")
    return v


def test_languages_present(tmp_path):
    v = _video(tmp_path, "Movie (2010).mkv", "Movie (2010).eng.srt", "Movie (2010).spa.forced.srt",
               "Movie (2010).2.fre.sdh.srt", "Other (2011).ger.srt", "Movie (2010) Extra.ita.srt")
    assert languages_present(v) == {"eng", "fre"}


def test_downloads_only_missing_languages(tmp_path):
    v = _video(tmp_path, "Show S02E05 - Title.mkv", "Show S02E05 - Title.eng.srt")
    client = FakeSubs([SubtitleResult(1, "eng"), SubtitleResult(2, "spa", hearing_impaired=True),
                       SubtitleResult(3, "spa", downloads=5)])
    saved = fetch_subtitles(client, v, ["eng", "spa", "ger"], title="Show", season=2, episodes=[5, 6],
                            media_type="tv")
    assert [p.name for p in saved] == ["Show S02E05 - Title.spa.srt"]
    assert saved[0].read_bytes() == b"subtitle 3"
    assert client.searches[0] == {"path": v, "languages": ["spa", "ger"], "query": "Show", "year": None,
                                  "season": 2, "episode": 5, "is_episode": True}
    # nothing missing: no search at all
    assert fetch_subtitles(client, v, ["eng", "spa"]) == [] and len(client.searches) == 1


def test_never_overwrites(tmp_path):
    v = _video(tmp_path, "Movie (2010).mkv", "Movie (2010).spa.srt")
    client = FakeSubs([SubtitleResult(1, "spa", hearing_impaired=True)])
    # the existing .spa.srt counts, so only a hearing-impaired one could be added
    assert fetch_subtitles(client, v, ["spa"]) == []
    (tmp_path / "Movie (2010).spa.srt").unlink()
    (tmp_path / "Movie (2010).spa.sdh.srt").write_text("mine")          # counts as Spanish too
    assert fetch_subtitles(client, v, ["spa"]) == []
    assert (tmp_path / "Movie (2010).spa.sdh.srt").read_text() == "mine"


class FakePlex:
    label = "Plex"

    def __init__(self, error=None):
        self.scans, self.error = [], error

    def scan(self, paths, media_type):
        if self.error:
            raise self.error
        self.scans.append((sorted(Path(p).name for p in paths), media_type))
        return ["Movies"]


def test_after_rename_downloads_then_scans(tmp_path):
    v = _video(tmp_path / "Movies" / "Heat (1995)", "Heat (1995).mkv")
    cand = SimpleNamespace(title="Heat", year="1995", season=None, episodes=[], media_type="movie")
    client = FakeSubs([SubtitleResult(9, "eng")])
    plex = FakePlex()
    downloaded, scanned, problems = after_rename(
        [(cand, v)], [v], "movie", subtitles={"client": client, "languages": ["eng"]}, server=plex)
    assert [p.name for p in downloaded] == ["Heat (1995).eng.srt"]
    assert client.logins == 1 and client.searches[0]["year"] == "1995"
    assert scanned == ["Movies"] and plex.scans == [(["Heat (1995).eng.srt", "Heat (1995).mkv"], "movie")]
    assert problems == []


def test_after_rename_reports_problems_and_still_scans(tmp_path):
    v = _video(tmp_path, "Heat (1995).mkv")
    cand = SimpleNamespace(title="Heat", year="", season=None, episodes=[], media_type="movie")
    client = FakeSubs([SubtitleResult(9, "eng")], fail_on_download=OpenSubtitlesError("limit reached"))
    plex = FakePlex()
    downloaded, scanned, problems = after_rename(
        [(cand, v)], [v], "movie", subtitles={"client": client, "languages": ["eng"]}, server=plex)
    assert downloaded == [] and scanned == ["Movies"] and problems == ["Subtitles: limit reached"]
    _, scanned, problems = after_rename([], [v], "movie", server=FakePlex(error=RuntimeError("Plex is off")))
    assert scanned == [] and problems == ["Plex: Plex is off"]
