"""Plex: ask the server to scan the folders PathRenamer just filled, so new
movies and episodes show up right away instead of at the next scheduled scan.

Also signs in with a Plex account the way other Plex apps do (a code shown on
plex.tv), which finds the server address and token for the user.
"""
import xml.etree.ElementTree as ET
from urllib.parse import urlencode

import requests

from mediaservers import MediaServerError, map_folders

PRODUCT = "PathRenamer"
PLEX_TV = "https://plex.tv/api/v2"
SIGN_IN_PAGE = "https://app.plex.tv/auth#?"


class PlexError(MediaServerError):
    """Something the user can act on, worded for them."""


class PlexClient:
    label = "Plex"

    def __init__(self, base_url, token, session=None, timeout=10.0):
        url = (base_url or "").strip().rstrip("/")
        if url and "://" not in url:
            url = "http://" + url
        self.base_url = url
        self.token = (token or "").strip()
        self.s = session or requests.Session()
        self.timeout = timeout

    @property
    def configured(self):
        return bool(self.base_url and self.token)

    def _get(self, path, params=None):
        if not self.configured:
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

    libraries = sections

    def scan(self, paths, media_type=None):
        """Scan the folders holding these files (new names). Files outside every
        library folder (e.g. Plex sees the drive by another path) get their
        whole library of that kind scanned instead. Returns the names of the
        libraries asked to scan."""
        sections = self.sections()
        jobs, unmatched = map_folders(paths, sections)
        whole = []
        if unmatched:
            kind = {"movie": "movie", "tv": "show", "anime": "show"}.get(media_type)
            whole = [sec for sec in sections
                     if sec["type"] == kind or (kind is None and sec["type"] in ("movie", "show"))]
        for sec in whole:
            self._get(f"/library/sections/{sec['key']}/refresh")
        for key, (sec, folders) in jobs.items():
            if any(w["key"] == key for w in whole):
                continue
            for folder in sorted(folders):
                self._get(f"/library/sections/{key}/refresh", {"path": folder})
        return sorted({sec["title"] for sec in whole} | {sec["title"] for sec, _ in jobs.values()})

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
