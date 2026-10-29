"""PathRenamer Free and Pro.

Renaming by hand is free, forever. Pro adds what PathRenamer does on its own:
renaming new downloads from a watch folder, downloading missing subtitles
after a rename, and asking Plex to scan. Everything that was free stays free.

Pro license keys are sold through Polar (polar.sh), which takes the payment
and handles sales tax and VAT. A key is checked with Polar's public
license-key API (made for desktop apps: no secret is built into the app).
After that the app works offline: it re-checks the key once a week and keeps
Pro for 30 days without a connection.

Until POLAR_ORGANIZATION_ID is set (in build_info.py, written by the release
build, or below), licensing is off and every feature is unlocked.
"""
import platform
import time

import requests

try:                                     # written by the release build (not in git)
    import build_info
except ImportError:                      # running from source
    build_info = None

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
    "plex_auto_scan": "Ask Plex to scan as soon as files are renamed",
}


# What License keeps in the config
STATE_KEYS = ("license_key", "license_activation_id", "license_status", "license_display_key",
              "license_expires_at", "license_checked_at", "trial_started")


class LicenseError(Exception):
    """Something the user can act on, worded for them."""


class License:
    """License state kept in the app's config dict (keys starting with
    "license_" and "trial_started")."""

    def __init__(self, cfg, organization_id=None, session=None, clock=time.time):
        self.cfg = cfg
        self.organization_id = POLAR_ORGANIZATION_ID if organization_id is None else organization_id
        self.s = session or requests.Session()
        self.clock = clock

    # ── state ────────────────────────────────────────────────────────
    @property
    def enabled(self):
        """False while licensing isn't set up: everything is unlocked."""
        return bool(self.organization_id)

    def is_pro(self):
        if not self.enabled:
            return True
        if not self.cfg.get("license_key") or self.cfg.get("license_status") != "granted":
            return False
        now = self.clock()
        expires = self.cfg.get("license_expires_at")
        if expires and now > expires:
            return False
        return now - float(self.cfg.get("license_checked_at") or 0) <= OFFLINE_DAYS * DAY

    def trial_days_left(self):
        started = self.cfg.get("trial_started")
        if not started:
            return TRIAL_DAYS
        left = TRIAL_DAYS - (self.clock() - float(started)) / DAY
        return max(0, int(left + 0.999))

    def trial_active(self):
        return bool(self.cfg.get("trial_started")) and self.trial_days_left() > 0

    def allows(self, feature=None):
        """May a Pro feature run? (Starts no trial; see start_trial.)"""
        return self.is_pro() or self.trial_active()

    def can_try(self):
        """True if turning on a Pro feature would start (or is within) the trial."""
        return self.is_pro() or not self.cfg.get("trial_started") or self.trial_active()

    def start_trial(self):
        """The 14-day trial starts the first time a Pro feature is turned on."""
        if self.enabled and not self.is_pro() and not self.cfg.get("trial_started"):
            self.cfg["trial_started"] = self.clock()
            return True
        return False

    def summary(self):
        """One line for Settings and the About box."""
        if not self.enabled:
            return "Early access: every feature is unlocked for now."
        if self.is_pro():
            shown = self.cfg.get("license_display_key") or "your key"
            return f"PathRenamer Pro is active ({shown}). Thank you for supporting PathRenamer!"
        if self.cfg.get("license_key") and self.cfg.get("license_status") not in (None, "granted"):
            return "This license key is no longer valid. Renaming by hand still works."
        if self.cfg.get("license_key"):
            return ("PathRenamer couldn't check your license for 30 days. Connect to the "
                    "internet and click Check Now.")
        if self.trial_active():
            days = self.trial_days_left()
            return f"Pro trial: {days} day{'s' if days != 1 else ''} left."
        if self.cfg.get("trial_started"):
            return "Your Pro trial has ended. Renaming by hand is free, forever."
        return "Free. Turn on any Pro feature to try Pro free for 14 days."

    def needs_check(self):
        return (self.enabled and bool(self.cfg.get("license_key"))
                and self.clock() - float(self.cfg.get("license_checked_at") or 0) > RECHECK_DAYS * DAY)

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
            raise LicenseError("That license key wasn't found. Check it and try again.")
        if r.status_code in (403, 422):
            detail = data.get("detail") if isinstance(data, dict) else None
            if isinstance(detail, list):
                detail = "; ".join(str(d.get("msg", d)) if isinstance(d, dict) else str(d) for d in detail)
            if r.status_code == 403 and not detail:
                detail = "This key is already in use on the most computers it allows."
            raise LicenseError(str(detail or "The license server didn't accept this key."))
        if r.status_code >= 400:
            raise ConnectionError(f"The license server had a problem ({r.status_code}). Try again later.")
        return data

    def _store(self, info, key=None):
        if key is not None:
            self.cfg["license_key"] = key
        self.cfg["license_status"] = info.get("status") or "granted"
        self.cfg["license_display_key"] = info.get("display_key") or ""
        self.cfg["license_expires_at"] = _timestamp(info.get("expires_at"))
        self.cfg["license_checked_at"] = self.clock()

    def activate(self, key, label=None):
        """Check a key and register this computer with it."""
        key = (key or "").strip()
        if not key:
            raise LicenseError("Paste your license key first.")
        if not self.enabled:
            raise LicenseError("Licensing isn't switched on in this version: everything is already unlocked.")
        activation = self._post("activate", {
            "key": key, "organization_id": self.organization_id,
            "label": label or platform.node() or "PathRenamer",
            "meta": {"app": "PathRenamer"},
        })
        self.cfg["license_activation_id"] = activation.get("id") or ""
        info = activation.get("license_key") or {}
        info = self._post("validate", self._body(key)) if not info.get("status") else info
        self._store(info, key)
        if info.get("status") not in (None, "granted"):
            raise LicenseError("This license key has been revoked or disabled.")
        return info

    def check(self):
        """Re-check the stored key. Network trouble keeps the current state
        (Pro keeps working offline for 30 days); returns False then."""
        key = self.cfg.get("license_key")
        if not self.enabled or not key:
            return False
        try:
            info = self._post("validate", self._body(key))
        except ConnectionError:
            return False
        except LicenseError:
            self.cfg["license_status"] = "invalid"
            self.cfg["license_checked_at"] = self.clock()
            return True
        self._store(info)
        return True

    def deactivate(self):
        """Free this computer's seat (to move the license to another one)."""
        key, activation = self.cfg.get("license_key"), self.cfg.get("license_activation_id")
        if self.enabled and key and activation:
            try:
                self._post("deactivate", {"key": key, "organization_id": self.organization_id,
                                          "activation_id": activation})
            except LicenseError:
                pass                            # already gone on the server
        for k in STATE_KEYS:
            if k != "trial_started":
                self.cfg.pop(k, None)

    def _body(self, key):
        body = {"key": key, "organization_id": self.organization_id}
        if self.cfg.get("license_activation_id"):
            body["activation_id"] = self.cfg["license_activation_id"]
        return body


def _timestamp(value):
    """ISO 8601 date from the API -> seconds since 1970 (None for no expiry)."""
    if not value:
        return None
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
