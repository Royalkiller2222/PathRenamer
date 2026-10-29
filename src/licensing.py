"""PathRenamer Free and Pro.

Renaming by hand is free, forever. Pro adds what PathRenamer does on its own:
renaming new downloads from a watch folder, downloading missing subtitles
after a rename, and asking the media server to scan. Everything that was free
stays free.

Pro license keys are sold through Polar (polar.sh), which takes the payment
and handles sales tax and VAT. A key is checked with Polar's public
license-key API (made for desktop apps: no secret is built into the app).
After that the app works offline: it re-checks the key once a week and keeps
Pro for 30 days without a connection.

Keeping Pro honest without closing the source:
- The license and trial state is sealed: encrypted for the Windows user
  (DPAPI) or signed with a random key kept next to the settings (Docker,
  Linux). Typing values into config.json doesn't unlock anything, and a
  copied config.json doesn't carry a license or a trial to another computer.
- Each activation is tied to this computer: Polar only confirms it when the
  computer's anonymous ID matches the one it was activated with.
- On Windows the trial's start is also kept in the registry, so deleting the
  settings doesn't start a new trial, and setting the clock back doesn't
  stretch the trial or the 30 offline days.
Anyone can still build their own copy from the source without these checks
(the GPL allows that); the official builds are what Pro pays for.

Until POLAR_ORGANIZATION_ID is set (in build_info.py, written by the release
build, or below), licensing is off and every feature is unlocked.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import platform
import secrets
import sys
import time
from pathlib import Path

import requests

import secret

try:                                     # written by the release build (not in git)
    import build_info
except ImportError:                      # running from source
    build_info = None

log = logging.getLogger("PathRenamer")

POLAR_ORGANIZATION_ID = getattr(build_info, "POLAR_ORGANIZATION_ID", "") or ""
POLAR_API = "https://api.polar.sh/v1/customer-portal/license-keys"
BUY_URL = "https://royalkiller2222.github.io/PathRenamer/pricing/"
MANAGE_URL = getattr(build_info, "POLAR_PORTAL_URL", "") or BUY_URL

TRIAL_DAYS = 14
RECHECK_DAYS = 7
OFFLINE_DAYS = 30
DAY = 86400

# Pro features: config switch -> what it does
PRO_FEATURES = {
    "watch_auto_rename": "Rename new downloads in a watch folder automatically",
    "subs_auto": "Download missing subtitles after every rename",
    "plex_auto_scan": "Ask your media server (Plex, Jellyfin, Emby or Kodi) to scan as soon as files are renamed",
}

# The sealed state lives in the config under STATE_KEY. Plain values under the
# old names (never used by a release with licensing on) are ignored and removed.
STATE_KEY = "license_state"
LEGACY_KEYS = ("license_key", "license_activation_id", "license_status", "license_display_key",
               "license_expires_at", "license_checked_at", "trial_started")
STATE_KEYS = (STATE_KEY,) + LEGACY_KEYS          # every config key License may change

# The folder for the signing key (the settings folder; core.py sets it)
DATA_DIR = None


# ── sealing ──────────────────────────────────────────────────────────────────
_ENTROPY = b"PathRenamer license state v1"
_KEYS = {}


def _signing_key() -> bytes:
    """A random key made once and kept in DATA_DIR (only for this install).
    Without a folder it lasts while the app runs."""
    folder = Path(DATA_DIR) if DATA_DIR else None
    if folder in _KEYS:
        return _KEYS[folder]
    key = None
    if folder is not None:
        path = folder / "seal.key"
        try:
            key = bytes.fromhex(path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            key = None
        if not key or len(key) < 32:
            key = secrets.token_bytes(32)
            try:
                folder.mkdir(parents=True, exist_ok=True)
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w", encoding="ascii") as f:
                    f.write(key.hex())
            except OSError as e:
                log.warning(f"Couldn't save the license signing key ({e}); the license is kept until "
                            "PathRenamer closes.")
    _KEYS[folder] = key or secrets.token_bytes(32)
    return _KEYS[folder]


def _mac(payload: str) -> str:
    return hmac.new(_signing_key(), ("hmac1:" + payload).encode("ascii"), hashlib.sha256).hexdigest()


def seal(data: dict, dpapi=None) -> str:
    """The state as text that can't be edited or moved to another computer:
    encrypted for this Windows user, or signed with this install's key."""
    raw = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if sys.platform == "win32" if dpapi is None else dpapi:
        try:
            return "dpapi1:" + base64.b64encode(secret.seal(raw, _ENTROPY)).decode("ascii")
        except OSError as e:
            log.warning(f"Windows couldn't encrypt the license state ({e}); signing it instead.")
    payload = base64.urlsafe_b64encode(raw).decode("ascii")
    return f"hmac1:{payload}.{_mac(payload)}"


def unseal(text):
    """The dict seal() was given, or None when the text was changed or comes
    from another computer or user."""
    if not isinstance(text, str):
        return None
    try:
        if text.startswith("dpapi1:"):
            raw = secret.unseal(base64.b64decode(text[7:], validate=True), _ENTROPY)
        elif text.startswith("hmac1:"):
            payload, _, mac = text[6:].rpartition(".")
            if not payload or not hmac.compare_digest(mac, _mac(payload)):
                return None
            raw = base64.urlsafe_b64decode(payload)
        else:
            return None
        data = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, TypeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def machine_id() -> str:
    """An anonymous ID for this computer (a hash: nothing about the computer
    can be read from it). Activations are tied to it."""
    raw = b""
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                                winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                raw = str(winreg.QueryValueEx(k, "MachineGuid")[0]).encode("utf-8")
        except (OSError, ImportError):
            raw = b""
    raw = raw or _signing_key()                     # Docker and Linux: this install
    return hashlib.sha256(b"PathRenamer machine " + raw).hexdigest()[:32]


# ── a second copy of the trial's start (Windows registry) ────────────────────
class RegistryBackup:
    """HKEY_CURRENT_USER\\Software\\PathRenamer, value State (sealed)."""
    KEY, NAME = r"Software\PathRenamer", "State"

    def load(self) -> str:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.KEY) as k:
                return str(winreg.QueryValueEx(k, self.NAME)[0])
        except (OSError, ImportError):
            return ""

    def save(self, text: str):
        try:
            import winreg
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, self.KEY) as k:
                winreg.SetValueEx(k, self.NAME, 0, winreg.REG_SZ, text)
        except (OSError, ImportError):
            pass


class MemoryBackup:
    """No second copy (Docker, Linux and the tests)."""

    def __init__(self, text=""):
        self.text = text

    def load(self) -> str:
        return self.text

    def save(self, text: str):
        self.text = text


def trial_backup():
    return RegistryBackup() if sys.platform == "win32" else MemoryBackup()


class LicenseError(Exception):
    """Something the user can act on, worded for them."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class License:
    """The license and trial, kept sealed in the app's config dict under
    "license_state". Reading it may tidy the config (old plain values are
    removed); the caller saves the config as usual."""

    def __init__(self, cfg, organization_id=None, session=None, clock=time.time, backup=None):
        self.cfg = cfg
        self.organization_id = POLAR_ORGANIZATION_ID if organization_id is None else organization_id
        self.s = session or requests.Session()
        self.clock = clock
        self.backup = backup if backup is not None else trial_backup()
        self._state, self._text, self._source = {}, None, None

    # ── the sealed state ─────────────────────────────────────────────
    @property
    def state(self) -> dict:
        """Read again whenever the config (or the sealed text in it) changed."""
        if self._source is not self.cfg or self.cfg.get(STATE_KEY) != self._text:
            self._load()
        return self._state

    def _load(self):
        text = self.cfg.get(STATE_KEY)
        state = unseal(text) if text else {}
        changed = False
        if state is None:                   # edited by hand, or from another computer or user
            log.warning("The saved license state isn't valid on this computer, so it was reset.")
            state, changed = {"trial_started": 1.0}, True      # (that doesn't start a new trial)
        for k in LEGACY_KEYS:               # plain values in config.json don't count
            if k in self.cfg:
                self.cfg.pop(k)
                changed = True
        if self.enabled:
            backed_up = (unseal(self.backup.load()) or {}).get("trial_started")
            starts = [float(t) for t in (state.get("trial_started"), backed_up)
                      if isinstance(t, (int, float)) and not isinstance(t, bool) and t > 0]
            if starts:
                first = min(starts)                    # the earliest start counts
                if state.get("trial_started") != first:
                    state["trial_started"], changed = first, True
                if backed_up != first:
                    self.backup.save(seal({"trial_started": first}))
        self._state, self._source, self._text = state, self.cfg, text
        if changed:
            self._save()

    def _save(self):
        if self._state:
            self._state["seen"] = max(float(self._state.get("seen") or 0), self.clock())
            self.cfg[STATE_KEY] = seal(self._state)
        else:
            self.cfg.pop(STATE_KEY, None)
        self._source, self._text = self.cfg, self.cfg.get(STATE_KEY)

    def _now(self):
        """The time, never earlier than PathRenamer has already seen (setting
        the clock back doesn't stretch the trial or the offline days)."""
        return max(self.clock(), float(self.state.get("seen") or 0))

    def touch(self):
        """Note that PathRenamer is running now (at most hourly). True when the
        config changed and should be saved."""
        if not self.enabled or not self.state or self.clock() < float(self.state.get("seen") or 0) + 3600:
            return False
        self._save()
        return True

    @property
    def key(self):
        """The license key typed in on this computer ('' for none)."""
        return self.state.get("key") or ""

    # ── what is allowed ──────────────────────────────────────────────
    @property
    def enabled(self):
        """False while licensing isn't set up: everything is unlocked."""
        return bool(self.organization_id)

    def is_pro(self):
        if not self.enabled:
            return True
        st = self.state
        if not st.get("key") or st.get("status") != "granted":
            return False
        now = self._now()
        expires = st.get("expires_at")
        if expires and now > expires:
            return False
        return now - float(st.get("checked_at") or 0) <= OFFLINE_DAYS * DAY

    def trial_days_left(self):
        started = self.state.get("trial_started")
        if not started:
            return TRIAL_DAYS
        left = TRIAL_DAYS - (self._now() - float(started)) / DAY
        return max(0, min(TRIAL_DAYS, int(left + 0.999)))

    def trial_active(self):
        return bool(self.state.get("trial_started")) and self.trial_days_left() > 0

    def allows(self, feature=None):
        """May a Pro feature run? (Starts no trial; see start_trial.)"""
        return self.is_pro() or self.trial_active()

    def can_try(self):
        """True if turning on a Pro feature would start (or is within) the trial."""
        return self.is_pro() or not self.state.get("trial_started") or self.trial_active()

    def start_trial(self):
        """The 14-day trial starts the first time a Pro feature is turned on."""
        if self.enabled and not self.is_pro() and not self.state.get("trial_started"):
            now = self.clock()
            self.state["trial_started"] = now
            self.backup.save(seal({"trial_started": now}))
            self._save()
            return True
        return False

    def summary(self):
        """One line for Settings and the About box."""
        if not self.enabled:
            return "Early access: every feature is unlocked for now."
        st = self.state
        if self.is_pro():
            shown = st.get("display_key") or "your key"
            return f"PathRenamer Pro is active ({shown}). Thank you for supporting PathRenamer!"
        if st.get("key") and st.get("status") not in (None, "granted"):
            return ("This license key is no longer valid on this computer. Renaming by hand still works. "
                    "If you moved PathRenamer to a new computer, click Activate again.")
        if st.get("key"):
            return ("PathRenamer couldn't check your license for 30 days. Connect to the "
                    "internet and click Check Now.")
        if self.trial_active():
            days = self.trial_days_left()
            return f"Pro trial: {days} day{'s' if days != 1 else ''} left."
        if st.get("trial_started"):
            return "Your Pro trial has ended. Renaming by hand is free, forever."
        return "Free. Turn on any Pro feature to try Pro free for 14 days."

    def needs_check(self):
        return (self.enabled and bool(self.state.get("key"))
                and self._now() - float(self.state.get("checked_at") or 0) > RECHECK_DAYS * DAY)

    # ── Polar ────────────────────────────────────────────────────────
    def _post(self, action, body):
        try:
            r = self.s.post(f"{POLAR_API}/{action}", json=body, timeout=15,
                            headers={"Accept": "application/json"})
        except requests.RequestException as e:
            raise ConnectionError(f"Can't reach the license server ({e.__class__.__name__}).")
        if r.status_code == 204:
            return {}
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code == 404:
            raise LicenseError("That license key wasn't found. Check it and try again.", 404)
        if r.status_code in (403, 422):
            detail = data.get("detail") if isinstance(data, dict) else None
            if isinstance(detail, list):
                detail = "; ".join(str(d.get("msg", d)) if isinstance(d, dict) else str(d) for d in detail)
            if r.status_code == 403 and not detail:
                detail = "This key is already in use on the most computers it allows."
            raise LicenseError(str(detail or "The license server didn't accept this key."), r.status_code)
        if r.status_code >= 400:
            raise ConnectionError(f"The license server had a problem ({r.status_code}). Try again later.")
        return data

    @staticmethod
    def _conditions():
        """Polar confirms an activation only on the computer it was made on."""
        return {"machine": machine_id()}

    def _body(self, key, activation_id):
        body = {"key": key, "organization_id": self.organization_id}
        if activation_id:
            body["activation_id"] = activation_id
            body["conditions"] = self._conditions()
        return body

    def _store(self, info):
        st = self.state
        st["status"] = info.get("status") or "granted"
        st["display_key"] = info.get("display_key") or ""
        st["expires_at"] = _timestamp(info.get("expires_at"))
        st["checked_at"] = st["seen"] = self.clock()        # the server confirmed: trust the clock again
        self._save()

    def activate(self, key, label=None):
        """Check a key and register this computer with it."""
        key = (key or "").strip()
        if not key:
            raise LicenseError("Paste your license key first.")
        if not self.enabled:
            raise LicenseError("Licensing isn't switched on in this version: everything is already unlocked.")
        try:
            activation = self._post("activate", {
                "key": key, "organization_id": self.organization_id,
                "label": label or platform.node() or "PathRenamer",
                "conditions": self._conditions(), "meta": {"app": "PathRenamer"},
            })
        except LicenseError as e:
            message = str(e).lower()
            if e.status != 403 or "activation" not in message or "limit" in message:
                raise
            activation = {}                 # this store doesn't count computers: the key alone is checked
        activation_id = activation.get("id") or ""
        info = activation.get("license_key") or {}
        if not info.get("status"):
            info = self._post("validate", self._body(key, activation_id))
        st = self.state
        st.update(key=key, activation_id=activation_id)
        self._store(info)
        if info.get("status") not in (None, "granted"):
            raise LicenseError("This license key has been revoked or disabled.")
        return info

    def check(self):
        """Re-check the stored key. Network trouble keeps the current state
        (Pro keeps working offline for 30 days); returns False then."""
        st = self.state
        key = st.get("key")
        if not self.enabled or not key:
            return False
        try:
            info = self._post("validate", self._body(key, st.get("activation_id")))
        except ConnectionError:
            return False
        except LicenseError:
            st["status"] = "invalid"
            st["checked_at"] = self.clock()
            self._save()
            return True
        self._store(info)
        return True

    def deactivate(self):
        """Free this computer's seat (to move the license to another one)."""
        st = self.state
        key, activation = st.get("key"), st.get("activation_id")
        if self.enabled and key and activation:
            try:
                self._post("deactivate", {"key": key, "organization_id": self.organization_id,
                                          "activation_id": activation})
            except LicenseError:
                pass                            # already gone on the server
        for k in ("key", "activation_id", "status", "display_key", "expires_at", "checked_at"):
            st.pop(k, None)
        self._save()


def _timestamp(value):
    """ISO 8601 date from the API -> seconds since 1970 (None for no expiry)."""
    if not value:
        return None
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
