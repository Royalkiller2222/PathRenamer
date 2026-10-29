"""Plex: ask the server to scan the folders PathRenamer just filled, so new
movies and episodes show up right away instead of at the next scheduled scan.

Also signs in with a Plex account the way other Plex apps do (a code shown on
plex.tv), which finds the server address and token for the user.
"""
import xml.etree.ElementTree as ET
from pathlib import PureWindowsPath, PurePosixPath
from urllib.parse import urlencode

import requests

PRODUCT = "PathRenamer"
PLEX_TV = "https://plex.tv/api/v2"
SIGN_IN_PAGE = "https://app.plex.tv/auth#?"


class PlexError(Exception):
    """Something the user can act on, worded for them."""


def _norm(path) -> str:
    """'D:\\Plex\\Movies\\' and 'd:/plex/movies' compare equal."""
    return str(path).replace("\\", "/").rstrip("/").lower()


def _parent(path) -> str:
    text = str(path)
    if "\\" in text or (len(text) > 1 and text[1] == ":"):
        return str(PureWindowsPath(text).parent)
    return str(PurePosixPath(text).parent)


class PlexClient:
    def __init__(self, base_url, token, session=None, timeout=10.0):
        url = (base_url or "").strip().rstrip("/")
        if url and "://" not in url:
            url = "http://" + url
        self.base_url = url
        self.token = (token or "").strip()
        self.s = session or requests.Session()
        self.timeout = timeout

    def _get(self, path, params=None):
        if not self.base_url or not self.token:
            raise PlexError("Plex isn't set up yet: add your server and sign in under Settings → Automation.")
        try:
            r = self.s.get(self.base_url + path, params=params, timeout=self.timeout,
                           headers={"X-Plex-Token": self.token, "Accept": "application/xml",
                                    "X-Plex-Product": PRODUCT})
        except requests.RequestException as e:
            raise PlexError(f"Can't reach Plex at {self.base_url}. Is the server running? ({e.__class__.__name__})")
        if r.status_code == 401:
            raise PlexError("Plex didn't accept the sign-in. Sign in again under Settings → Automation.")
        if r.status_code >= 400:
            raise PlexError(f"Plex answered with an error ({r.status_code}).")
        return r

    def _xml(self, path):
        try:
            return ET.fromstring(self._get(path).content)
        except ET.ParseError:
            raise PlexError(f"{self.base_url} doesn't look like a Plex server.")

    def server_name(self) -> str:
        return self._xml("/").get("friendlyName") or "Plex"

    def sections(self):
        """The server's libraries: [{key, title, type, locations}]."""
        out = []
        for d in self._xml("/library/sections").findall("Directory"):
            out.append({"key": d.get("key"), "title": d.get("title") or "", "type": d.get("type") or "",
                        "locations": [loc.get("path") for loc in d.findall("Location") if loc.get("path")]})
        return out

    def scan(self, paths, media_type=None):
        """Scan the folders holding these files (new names). Files outside every
        library folder (e.g. Plex sees the drive by another path) get their
        whole library of that kind scanned instead. Returns the names of the
        libraries asked to scan."""
        sections = self.sections()
        jobs = {}                          # key -> [title, set of folders or None for all]
        unmatched = False
        for p in paths:
            folder = _parent(p)
            n_folder = _norm(folder)
            found = False
            for sec in sections:
                for loc in sec["locations"]:
                    n_loc = _norm(loc)
                    if n_folder != n_loc and not n_folder.startswith(n_loc + "/"):
                        continue
                    rest = folder.replace("\\", "/").rstrip("/")[len(n_loc):].strip("/")
                    sep = "\\" if "\\" in loc or (len(loc) > 1 and loc[1] == ":") else "/"
                    server_folder = loc.rstrip("\\/") + (sep + rest.replace("/", sep) if rest else "")
                    job = jobs.setdefault(sec["key"], [sec["title"], set()])
                    if job[1] is not None:
                        job[1].add(server_folder)
                    found = True
            unmatched = unmatched or not found
        if unmatched:
            kind = {"movie": "movie", "tv": "show", "anime": "show"}.get(media_type)
            for sec in sections:
                if sec["type"] == kind or (kind is None and sec["type"] in ("movie", "show")):
                    jobs[sec["key"]] = [sec["title"], None]
        for key, (title, folders) in jobs.items():
            if folders is None:
                self._get(f"/library/sections/{key}/refresh")
            else:
                for folder in sorted(folders):
                    self._get(f"/library/sections/{key}/refresh", {"path": folder})
        return sorted(title for title, _ in jobs.values())


    def scan_all(self):
        """Scan every movie and TV library. Returns their names."""
        titles = []
        for sec in self.sections():
            if sec["type"] in ("movie", "show"):
                self._get(f"/library/sections/{sec['key']}/refresh")
                titles.append(sec["title"])
        return titles


class PlexSignIn:
    """Sign in with a Plex account: start() gives a code and the page where
    the user approves it; token() returns the account token once they did."""

    def __init__(self, client_id, session=None, timeout=10.0):
        self.client_id = client_id
        self.s = session or requests.Session()
        self.timeout = timeout

    def _headers(self, token=None):
        h = {"Accept": "application/json", "X-Plex-Product": PRODUCT,
             "X-Plex-Client-Identifier": self.client_id}
        if token:
            h["X-Plex-Token"] = token
        return h

    def _call(self, method, url, **kw):
        try:
            r = self.s.request(method, url, headers=self._headers(kw.pop("token", None)),
                               timeout=self.timeout, **kw)
        except requests.RequestException as e:
            raise PlexError(f"Can't reach plex.tv ({e.__class__.__name__}). Check your internet connection.")
        if r.status_code >= 400:
            raise PlexError(f"plex.tv answered with an error ({r.status_code}).")
        try:
            return r.json()
        except ValueError:
            raise PlexError("plex.tv sent an answer PathRenamer doesn't understand.")

    def start(self):
        """(pin id, code, page to open in the browser)."""
        pin = self._call("POST", f"{PLEX_TV}/pins", data={"strong": "true"})
        page = SIGN_IN_PAGE + urlencode({"clientID": self.client_id, "code": pin["code"],
                                         "context[device][product]": PRODUCT})
        return pin["id"], pin["code"], page

    def token(self, pin_id, code):
        """The account token, or None while the user hasn't approved yet."""
        return self._call("GET", f"{PLEX_TV}/pins/{pin_id}", params={"code": code}).get("authToken") or None

    def servers(self, token):
        """[{name, url, token}] for the account's servers, best address first
        (on this network, not relayed)."""
        out = []
        resources = self._call("GET", f"{PLEX_TV}/resources", token=token,
                               params={"includeHttps": 1, "includeRelay": 1})
        for res in resources if isinstance(resources, list) else []:
            if "server" not in (res.get("provides") or ""):
                continue
            conns = sorted(res.get("connections") or [],
                           key=lambda c: (not c.get("local"), bool(c.get("relay"))))
            if conns and conns[0].get("uri"):
                out.append({"name": res.get("name") or "Plex", "url": conns[0]["uri"],
                            "token": res.get("accessToken") or token, "owned": bool(res.get("owned"))})
        out.sort(key=lambda s: not s["owned"])
        return out
