"""Library scans after renaming, for every media server PathRenamer works
with: Plex (plex.py), Jellyfin, Emby and Kodi. Apps built on them, like
Plezy, see the new files as soon as their server does.

Every client has the same shape: `label`, `configured`, server_name(),
libraries() -> [{key, title, type, locations}], scan(paths, media_type)
(only the folders that changed when the server's libraries hold them, the
whole library otherwise) and scan_all(). Each returns the library names it
asked to scan and raises MediaServerError with words meant for the user.
"""
from pathlib import PurePosixPath, PureWindowsPath

import requests

SERVER_KINDS = (("plex", "Plex"), ("jellyfin", "Jellyfin"), ("emby", "Emby"), ("kodi", "Kodi"))
VIDEO_LIBRARY_TYPES = {"movie", "show", "movies", "tvshows", "video", "mixed", "", None}


class MediaServerError(Exception):
    """Something the user can act on, worded for them."""


# ── matching new files to the server's library folders ───────────────────────
def norm(path) -> str:
    """'D:\\Plex\\Movies\\', 'd:/plex/movies' compare equal, and so do Kodi's
    'smb://NAS/Movies/' and Windows' '\\\\NAS\\Movies'."""
    text = str(path).replace("\\", "/").rstrip("/").lower()
    return text[4:] if text.startswith("smb://") else text


def parent_of(path) -> str:
    text = str(path)
    if "\\" in text or (len(text) > 1 and text[1] == ":"):
        return str(PureWindowsPath(text).parent)
    return str(PurePosixPath(text).parent)


def map_folders(paths, libraries):
    """({library key: (library, folders as the server names them)}, whether a
    file lies outside every library) for the folders holding paths."""
    jobs, unmatched = {}, False
    for p in paths:
        folder = parent_of(p)
        n_folder = norm(folder)
        found = False
        for lib in libraries:
            for loc in lib["locations"]:
                n_loc = norm(loc)
                if not n_loc or (n_folder != n_loc and not n_folder.startswith(n_loc + "/")):
                    continue
                # norm() keeps the folder's length, so the part below the library cuts cleanly
                rest = folder.replace("\\", "/").rstrip("/")[len(n_loc):].strip("/")
                sep = "\\" if "\\" in loc or (len(loc) > 1 and loc[1] == ":") else "/"
                server_folder = loc.rstrip("\\/") + (sep + rest.replace("/", sep) if rest else "")
                jobs.setdefault(lib["key"], (lib, set()))[1].add(server_folder)
                found = True
        unmatched = unmatched or not found
    return jobs, unmatched


def _clean_url(url):
    url = (url or "").strip().rstrip("/")
    return ("http://" + url) if url and "://" not in url else url


# ── Jellyfin and Emby ────────────────────────────────────────────────────────
class JellyfinClient:
    """Jellyfin, or Emby (emby=True): the same API family. The API key comes
    from the server's Dashboard → API Keys."""

    def __init__(self, base_url, api_key, session=None, timeout=10.0, emby=False):
        self.base_url = _clean_url(base_url)
        self.api_key = (api_key or "").strip()
        self.emby = emby
        self.label = "Emby" if emby else "Jellyfin"
        self.s = session or requests.Session()
        self.timeout = timeout

    @property
    def configured(self):
        return bool(self.base_url and self.api_key)

    def _call(self, method, path, **kw):
        if not self.configured:
            raise MediaServerError(f"{self.label} isn't set up yet: add the server and an API key under "
                                   "Settings → Automation.")
        headers = {"Accept": "application/json"}
        if self.emby:
            headers["X-Emby-Token"] = self.api_key
        else:
            headers["Authorization"] = f'MediaBrowser Token="{self.api_key}"'
        url = self.base_url + ("/emby" if self.emby else "") + path
        try:
            r = self.s.request(method, url, headers=headers, timeout=self.timeout, **kw)
        except requests.RequestException as e:
            raise MediaServerError(f"Can't reach {self.label} at {self.base_url}. Is the server running? "
                                   f"({e.__class__.__name__})")
        if r.status_code in (401, 403):
            raise MediaServerError(f"{self.label} didn't accept the API key. Make a new one in its "
                                   "Dashboard → API Keys.")
        if r.status_code >= 400:
            raise MediaServerError(f"{self.label} answered with an error ({r.status_code}).")
        return r

    def _json(self, path):
        try:
            return self._call("GET", path).json()
        except ValueError:
            raise MediaServerError(f"{self.base_url} doesn't look like a {self.label} server.")

    def server_name(self):
        info = self._json("/System/Info")
        return (info.get("ServerName") if isinstance(info, dict) else None) or self.label

    def libraries(self):
        out = []
        for lib in self._json("/Library/VirtualFolders") or []:
            out.append({"key": lib.get("ItemId") or lib.get("Name"), "title": lib.get("Name") or "",
                        "type": lib.get("CollectionType") or "", "locations": lib.get("Locations") or []})
        return out

    def scan(self, paths, media_type=None):
        libraries = self.libraries()
        jobs, unmatched = map_folders(paths, libraries)
        if jobs:
            updates = [{"Path": folder, "UpdateType": "Created"}
                       for _, folders in jobs.values() for folder in sorted(folders)]
            self._call("POST", "/Library/Media/Updated", json={"Updates": updates})
        if unmatched:                      # the server sees the files by another path
            self._call("POST", "/Library/Refresh")
            return sorted(lib["title"] for lib in libraries if lib["type"] in VIDEO_LIBRARY_TYPES)
        return sorted(lib["title"] for lib, _ in jobs.values())

    def scan_all(self):
        libraries = self.libraries()
        self._call("POST", "/Library/Refresh")
        return sorted(lib["title"] for lib in libraries if lib["type"] in VIDEO_LIBRARY_TYPES)


# ── Kodi ─────────────────────────────────────────────────────────────────────
class KodiClient:
    """Kodi's JSON-RPC over HTTP. Kodi needs Settings → Services → Control →
    Allow remote control via HTTP."""
    label = "Kodi"

    def __init__(self, base_url, username="", password="", session=None, timeout=10.0):
        self.base_url = _clean_url(base_url)
        self.username, self.password = (username or "").strip(), password or ""
        self.s = session or requests.Session()
        self.timeout = timeout

    @property
    def configured(self):
        return bool(self.base_url)

    def _rpc(self, method, params=None):
        if not self.configured:
            raise MediaServerError("Kodi isn't set up yet: add its address (like http://192.168.1.20:8080) "
                                   "under Settings → Automation.")
        body = {"jsonrpc": "2.0", "method": method, "params": params or {}, "id": 1}
        auth = (self.username, self.password) if self.username else None
        try:
            r = self.s.request("POST", self.base_url + "/jsonrpc", json=body, auth=auth, timeout=self.timeout)
        except requests.RequestException as e:
            raise MediaServerError(f"Can't reach Kodi at {self.base_url}. In Kodi, turn on Settings → "
                                   f"Services → Control → Allow remote control via HTTP. ({e.__class__.__name__})")
        if r.status_code == 401:
            raise MediaServerError("Kodi didn't accept the username or password.")
        if r.status_code >= 400:
            raise MediaServerError(f"Kodi answered with an error ({r.status_code}).")
        try:
            data = r.json()
        except ValueError:
            raise MediaServerError(f"{self.base_url} doesn't look like Kodi.")
        if data.get("error"):
            raise MediaServerError(f"Kodi said: {data['error'].get('message', 'error')}.")
        return data.get("result")

    def server_name(self):
        props = self._rpc("Application.GetProperties", {"properties": ["name", "version"]}) or {}
        major = (props.get("version") or {}).get("major")
        return f"{props.get('name') or 'Kodi'} {major}".strip() if major else (props.get("name") or "Kodi")

    def libraries(self):
        sources = (self._rpc("Files.GetSources", {"media": "video"}) or {}).get("sources") or []
        return [{"key": s.get("file"), "title": s.get("label") or s.get("file") or "", "type": "video",
                 "locations": [s.get("file")] if s.get("file") else []} for s in sources]

    def scan(self, paths, media_type=None):
        libraries = self.libraries()
        jobs, unmatched = map_folders(paths, libraries)
        if unmatched or not jobs:
            self._rpc("VideoLibrary.Scan", {})
            return sorted(lib["title"] for lib in libraries) or ["Video library"]
        for _, folders in jobs.values():
            for folder in sorted(folders):
                self._rpc("VideoLibrary.Scan", {"directory": folder.rstrip("/\\") + "/"})
        return sorted(lib["title"] for lib, _ in jobs.values())

    def scan_all(self):
        libraries = self.libraries()
        self._rpc("VideoLibrary.Scan", {})
        return sorted(lib["title"] for lib in libraries) or ["Video library"]


def server_from_config(cfg, unprotect=lambda s: s or ""):
    """The media server client the settings choose (Plex unless set)."""
    kind = cfg.get("media_server") or "plex"
    if kind in ("jellyfin", "emby"):
        return JellyfinClient(cfg.get(f"{kind}_url", ""), unprotect(cfg.get(f"{kind}_key", "")), emby=kind == "emby")
    if kind == "kodi":
        return KodiClient(cfg.get("kodi_url", ""), cfg.get("kodi_user", ""), unprotect(cfg.get("kodi_password", "")))
    from plex import PlexClient
    return PlexClient(cfg.get("plex_url", ""), unprotect(cfg.get("plex_token", "")))
