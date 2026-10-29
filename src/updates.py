"""Checking GitHub for a newer PathRenamer and downloading it.

The app asks GitHub's releases API for the latest release (once a day, and
whenever the user clicks Check for Updates), compares versions, and with
one click downloads the installer (or the portable PathRenamer.exe),
checks its size and SHA-256 against what GitHub reports, then runs it.
Downloads only ever come from this repository's own releases.
"""
import hashlib
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests

REPO = "Royalkiller2222/PathRenamer"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"
DOCKER_IMAGE = "ghcr.io/royalkiller2222/pathrenamer"


class UpdateError(Exception):
    """Something the user can act on, worded for them."""


def parse_version(text) -> tuple:
    """'V1.5.0' -> (1, 5, 0); unknown parts are 0."""
    nums = re.findall(r"\d+", str(text or ""))[:3]
    return tuple(int(n) for n in nums) + (0,) * (3 - len(nums))


def is_newer(latest, current) -> bool:
    return parse_version(latest) > parse_version(current)


@dataclass
class Asset:
    name: str
    url: str
    size: int = 0
    sha256: str = ""


@dataclass
class Release:
    version: str                 # "1.6.0"
    tag: str                     # "V1.6.0"
    notes: str = ""
    page_url: str = ""
    installer: Optional[Asset] = None
    portable: Optional[Asset] = None
    assets: list = field(default_factory=list)


def latest_release(session=None, timeout=10.0) -> Release:
    """The newest published release. Raises UpdateError when GitHub can't be reached."""
    s = session or requests.Session()
    try:
        r = s.get(RELEASES_API, timeout=timeout, headers={"Accept": "application/vnd.github+json",
                                                          "User-Agent": "PathRenamer"})
    except requests.RequestException as e:
        raise UpdateError(f"Can't reach GitHub to check for updates ({e.__class__.__name__}).")
    if r.status_code == 404:
        raise UpdateError("No release has been published yet.")
    if r.status_code == 403:
        raise UpdateError("GitHub is limiting update checks right now. Try again in an hour.")
    if r.status_code >= 400:
        raise UpdateError(f"GitHub answered with an error ({r.status_code}).")
    try:
        data = r.json()
    except ValueError:
        raise UpdateError("GitHub sent an answer PathRenamer doesn't understand.")
    tag = data.get("tag_name") or ""
    version = ".".join(str(n) for n in parse_version(tag))
    rel = Release(version=version, tag=tag, notes=data.get("body") or "",
                  page_url=data.get("html_url") or f"https://github.com/{REPO}/releases/latest")
    for a in data.get("assets") or []:
        url = a.get("browser_download_url") or ""
        if not url.startswith(DOWNLOAD_PREFIX):
            continue                              # only this repository's own downloads
        digest = a.get("digest") or ""
        asset = Asset(name=a.get("name") or "", url=url, size=int(a.get("size") or 0),
                      sha256=digest[7:].lower() if digest.startswith("sha256:") else "")
        rel.assets.append(asset)
        lower = asset.name.lower()
        if lower.startswith("pathrenamer-setup") and lower.endswith(".exe"):
            rel.installer = asset
        elif lower == "pathrenamer.exe":
            rel.portable = asset
    return rel


def check(current_version, session=None):
    """The newer Release, or None when current_version is the latest."""
    rel = latest_release(session)
    return rel if is_newer(rel.version, current_version) else None


def download(asset: Asset, folder=None, progress=None, should_cancel=None, session=None, timeout=30.0) -> Path:
    """Download asset into folder (a temp folder by default) and check it.
    progress(done_bytes, total_bytes) is called as it goes. Returns the file."""
    if not asset.url.startswith(DOWNLOAD_PREFIX):
        raise UpdateError("That download doesn't come from PathRenamer's releases.")
    folder = Path(folder or tempfile.mkdtemp(prefix="pathrenamer-update-"))
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / asset.name
    part = target.with_name(target.name + ".part")
    s = session or requests.Session()
    sha = hashlib.sha256()
    done = 0
    try:
        with s.get(asset.url, stream=True, timeout=timeout, headers={"User-Agent": "PathRenamer"}) as r:
            if r.status_code >= 400:
                raise UpdateError(f"The download failed ({r.status_code}).")
            total = asset.size or int(r.headers.get("Content-Length") or 0)
            with open(part, "wb") as f:
                for chunk in r.iter_content(chunk_size=256 * 1024):
                    if should_cancel and should_cancel():
                        raise UpdateError("The download was cancelled.")
                    if not chunk:
                        continue
                    f.write(chunk)
                    sha.update(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
    except requests.RequestException as e:
        part.unlink(missing_ok=True)
        raise UpdateError(f"The download stopped ({e.__class__.__name__}). Try again.")
    except UpdateError:
        part.unlink(missing_ok=True)
        raise
    if asset.size and done != asset.size:
        part.unlink(missing_ok=True)
        raise UpdateError("The download was incomplete. Try again.")
    if asset.sha256 and sha.hexdigest() != asset.sha256:
        part.unlink(missing_ok=True)
        raise UpdateError("The download didn't match what GitHub published, so it was deleted.")
    os.replace(part, target)
    return target


def remove_old_downloads(folder=None, older_than=3600.0):
    """Delete update downloads left in the temp folder by earlier updates."""
    import shutil
    import time
    base = Path(folder or tempfile.gettempdir())
    cutoff = time.time() - older_than
    try:
        leftovers = list(base.glob("pathrenamer-update-*"))
    except OSError:
        return
    for path in leftovers:
        try:
            if path.stat().st_mtime > cutoff:
                continue                          # (an update may still be running from it)
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink()
        except OSError:
            pass


# ── how this copy of PathRenamer updates itself ──────────────────────────────
def install_kind(frozen=None, exe=None) -> str:
    """'installer' (installed with the setup program), 'portable' (a lone
    PathRenamer.exe) or 'source' (run from the Python files)."""
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if not frozen:
        return "source"
    exe = Path(exe or sys.executable)
    return "installer" if any(exe.parent.glob("unins*.exe")) else "portable"


def installer_command(installer: Path):
    """Runs the setup quietly; it closes PathRenamer, upgrades it and starts it again."""
    return [str(installer), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/relaunch=1"]


def portable_swap_script(new_exe: Path, exe: Path, pid: int) -> str:
    """A cmd script that waits for this PathRenamer to close, puts the new
    exe in its place and starts it."""
    return "\r\n".join([
        "@echo off",
        ":wait",
        f'tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul && (timeout /t 1 /nobreak >nul & goto wait)',
        f'move /y "{new_exe}" "{exe}" >nul',
        f'start "" "{exe}"',
        'del "%~f0"',
    ]) + "\r\n"


def start_update(downloaded: Path, kind: str, exe=None):
    """Hand over to the new version (Windows). The caller quits right after."""
    if kind == "installer":
        command = installer_command(downloaded)
        try:
            subprocess.Popen(command, close_fds=True)
        except OSError as e:
            if getattr(e, "winerror", None) != 740:        # 740: Windows wants to ask for admin rights
                raise
            os.startfile(command[0], "open", " ".join(command[1:]))   # shows the admin prompt
    elif kind == "portable":
        exe = Path(exe or sys.executable)
        script = Path(tempfile.gettempdir()) / "pathrenamer-update.cmd"
        script.write_text(portable_swap_script(downloaded, exe, os.getpid()), encoding="utf-8")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        subprocess.Popen(["cmd", "/c", str(script)], close_fds=True, creationflags=flags)
    else:
        raise UpdateError("This copy runs from source: download the new version from GitHub.")
