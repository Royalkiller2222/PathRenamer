"""Free and Pro: the trial, license keys checked with Polar, working offline,
and the state sealed so editing config.json doesn't unlock Pro."""
import base64
import json
import os
import sys

import pytest
import requests

import licensing
from conftest import FakeResponse, FakeSession
from licensing import DAY, POLAR_API, License, LicenseError, MemoryBackup, machine_id, seal, unseal

ORG = "org-123"


class Clock:
    def __init__(self, now=1_700_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def _license(cfg=None, routes=None, error=None, org=ORG, backup=None, clock=None):
    clock = clock or Clock()
    session = FakeSession(routes, error=error)
    lic = License({} if cfg is None else cfg, organization_id=org, session=session, clock=clock, backup=backup)
    return lic, clock, session


GRANTED = {"status": "granted", "display_key": "****-E304DA", "expires_at": None}


def test_everything_is_unlocked_until_licensing_is_set_up():
    lic, _, _ = _license(org="")
    assert not lic.enabled and lic.is_pro() and lic.allows("watch_auto_rename")
    assert not lic.start_trial()
    assert "Early access" in lic.summary()


def test_trial_starts_when_a_pro_feature_is_turned_on():
    lic, clock, _ = _license()
    assert not lic.allows() and lic.can_try() and "try Pro free for 14 days" in lic.summary()
    assert lic.start_trial() and not lic.start_trial()      # only once
    assert lic.allows() and lic.trial_days_left() == 14
    clock.now += 10.5 * DAY
    assert lic.trial_days_left() == 4 and "4 days left" in lic.summary()
    clock.now += 4 * DAY
    assert not lic.allows() and not lic.can_try() and "trial has ended" in lic.summary()


def test_activate_a_key():
    activation = {"id": "act-1", "license_key": GRANTED}
    lic, clock, session = _license(routes={("POST", f"{POLAR_API}/activate"): FakeResponse(json=activation)})
    lic.activate("  KEY-1  ", label="Den PC")
    body = session.calls[0][2]["json"]
    assert body["key"] == "KEY-1" and body["organization_id"] == ORG and body["label"] == "Den PC"
    assert body["conditions"] == {"machine": machine_id()}                # tied to this computer
    assert lic.is_pro() and lic.state["activation_id"] == "act-1" and lic.key == "KEY-1"
    assert "Pro is active (****-E304DA)" in lic.summary()


def test_activation_without_key_details_validates():
    lic, _, session = _license(routes={
        ("POST", f"{POLAR_API}/activate"): FakeResponse(json={"id": "act-9"}),
        ("POST", f"{POLAR_API}/validate"): FakeResponse(json=GRANTED)})
    lic.activate("KEY-2")
    assert lic.is_pro()
    assert session.calls[1][2]["json"] == {"key": "KEY-2", "organization_id": ORG, "activation_id": "act-9",
                                           "conditions": {"machine": machine_id()}}


@pytest.mark.parametrize("response, message", [
    (FakeResponse(404, json={"detail": "Not found"}), "wasn't found"),
    (FakeResponse(403, json={"detail": "License key activation limit reached"}), "limit reached"),
    (FakeResponse(403, json={}), "most computers"),
    (FakeResponse(422, json={"detail": [{"msg": "field required"}]}), "field required"),
])
def test_bad_keys_say_why(response, message):
    lic, _, _ = _license(routes={("POST", f"{POLAR_API}/activate"): response})
    with pytest.raises(LicenseError, match=message):
        lic.activate("KEY")
    assert not lic.is_pro() and not lic.key


def test_revoked_keys_stop_working_at_the_next_check():
    lic, clock, session = _license(routes={
        ("POST", f"{POLAR_API}/activate"): FakeResponse(json={"id": "a", "license_key": GRANTED})})
    lic.activate("KEY")
    clock.now += 8 * DAY
    assert lic.needs_check()
    session.routes[("POST", f"{POLAR_API}/validate")] = FakeResponse(json=dict(GRANTED, status="revoked"))
    assert lic.check() and not lic.is_pro() and "no longer valid" in lic.summary()


def test_pro_works_offline_for_30_days():
    lic, clock, session = _license(routes={
        ("POST", f"{POLAR_API}/activate"): FakeResponse(json={"id": "a", "license_key": GRANTED})})
    lic.activate("KEY")
    session.error = requests.ConnectionError()
    clock.now += 29 * DAY
    assert not lic.check() and lic.is_pro()                  # no connection: unchanged
    clock.now += 2 * DAY
    assert not lic.is_pro() and "couldn't check your license" in lic.summary()
    session.error = None
    session.routes[("POST", f"{POLAR_API}/validate")] = FakeResponse(json=GRANTED)
    assert lic.check() and lic.is_pro()


def test_expired_keys():
    lic, clock, _ = _license(routes={("POST", f"{POLAR_API}/activate"): FakeResponse(
        json={"id": "a", "license_key": dict(GRANTED, expires_at="2023-11-20T00:00:00Z")})})
    lic.activate("KEY")
    assert lic.is_pro()
    clock.now += 30 * DAY
    assert not lic.is_pro()


def test_deactivate_frees_this_computer():
    lic, _, session = _license(routes={
        ("POST", f"{POLAR_API}/activate"): FakeResponse(json={"id": "a", "license_key": GRANTED}),
        ("POST", f"{POLAR_API}/deactivate"): FakeResponse(204)})
    lic.activate("KEY")
    lic.deactivate()
    assert session.calls[-1][2]["json"] == {"key": "KEY", "organization_id": ORG, "activation_id": "a"}
    assert not lic.is_pro() and not lic.key and "activation_id" not in lic.state


# ── keeping Pro honest ───────────────────────────────────────────────────────
def _activated(**kw):
    lic, clock, session = _license(routes={
        ("POST", f"{POLAR_API}/activate"): FakeResponse(json={"id": "act-1", "license_key": GRANTED}),
        ("POST", f"{POLAR_API}/validate"): FakeResponse(json=GRANTED)}, **kw)
    lic.activate("KEY")
    return lic, clock, session


def test_checks_are_tied_to_this_computer():
    lic, clock, session = _activated()
    clock.now += 8 * DAY
    assert lic.check()
    assert session.calls[-1][2]["json"] == {"key": "KEY", "organization_id": ORG, "activation_id": "act-1",
                                            "conditions": {"machine": machine_id()}}
    assert machine_id() == machine_id() and len(machine_id()) == 32


def test_typing_values_into_the_config_unlocks_nothing():
    now = Clock().now
    cfg = {"license_key": "FAKE", "license_status": "granted", "license_checked_at": now,
           "license_activation_id": "x", "trial_started": now, "theme": "dark"}
    lic, _, _ = _license(cfg)
    assert not lic.is_pro() and not lic.key and not lic.trial_active()
    assert cfg == {"theme": "dark"}                                       # the plain values are removed


def _edited(text, **changes):
    """The sealed text with its contents changed but the old signature kept."""
    payload, _, mac = text[len("hmac1:"):].rpartition(".")
    data = json.loads(base64.urlsafe_b64decode(payload))
    data.update(changes)
    return "hmac1:" + base64.urlsafe_b64encode(json.dumps(data).encode()).decode() + "." + mac


def test_an_edited_state_is_reset_and_does_not_restart_the_trial(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")                        # the signed form (Docker, Linux)
    lic, clock, _ = _license()
    lic.start_trial()
    text = lic.cfg[licensing.STATE_KEY]
    assert text.startswith("hmac1:") and unseal(text)["trial_started"] == clock.now
    for bad in (_edited(text, trial_started=clock.now + 300 * DAY),
                _edited(text, key="K", status="granted", checked_at=clock.now),
                text[:-1] + ("0" if text[-1] != "0" else "1"), "hmac1:", "plain", 42):
        again, _, _ = _license({licensing.STATE_KEY: bad}, clock=clock)
        assert not again.is_pro() and not again.trial_active() and not again.can_try()
        assert "trial has ended" in again.summary()


def test_a_state_from_another_install_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(licensing, "DATA_DIR", tmp_path / "one")
    text = seal({"key": "K", "status": "granted"})
    assert unseal(text) == {"key": "K", "status": "granted"}
    assert (tmp_path / "one" / "seal.key").exists()
    if os.name != "nt":
        assert (tmp_path / "one" / "seal.key").stat().st_mode & 0o077 == 0  # only the owner can read it
    licensing._KEYS.clear()                                               # a restart: the key is read back
    assert unseal(text) == {"key": "K", "status": "granted"}
    monkeypatch.setattr(licensing, "DATA_DIR", tmp_path / "two")          # another computer's settings
    assert unseal(text) is None


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows only")
def test_windows_encrypts_the_state_for_this_user():
    import secret
    text = seal({"key": "K"})
    assert text.startswith("dpapi1:") and "K" not in text and unseal(text) == {"key": "K"}
    with pytest.raises(OSError):
        secret.unseal(secret.seal(b"data", b"one"), b"two")
    blob = bytearray(base64.b64decode(text[len("dpapi1:"):]))
    blob[-1] ^= 1
    assert unseal("dpapi1:" + base64.b64encode(bytes(blob)).decode()) is None


def test_deleting_the_settings_does_not_restart_the_trial():
    backup = MemoryBackup()                                                # the registry on Windows
    lic, clock, _ = _license(backup=backup)
    lic.start_trial()
    clock.now += 15 * DAY
    fresh, _, _ = _license({}, backup=backup, clock=clock)
    assert not fresh.trial_active() and not fresh.can_try()
    assert fresh.cfg[licensing.STATE_KEY]                                  # copied back into the settings
    # and the other way round: the settings remember it when the registry is cleared
    again, _, _ = _license(dict(fresh.cfg), backup=MemoryBackup(), clock=clock)
    assert not again.can_try()


def test_setting_the_clock_back_does_not_stretch_the_trial():
    lic, clock, _ = _license()
    lic.start_trial()
    clock.now += 10 * DAY
    assert lic.touch() and not lic.touch()                                 # at most hourly
    clock.now -= 10 * DAY
    assert lic.trial_days_left() == 4


def test_pro_survives_a_clock_that_was_wrong():
    lic, clock, _ = _activated()
    clock.now += 400 * DAY                                                 # the clock jumps a year ahead
    lic.touch()
    clock.now -= 400 * DAY                                                 # and is fixed
    assert not lic.is_pro()
    assert lic.check() and lic.is_pro()                                    # the license server confirms


def test_stores_that_do_not_count_computers():
    """If activations aren't switched on for the product, the key alone is checked."""
    lic, clock, session = _license(routes={
        ("POST", f"{POLAR_API}/activate"): FakeResponse(403, json={"detail": "This license key does not "
                                                                           "support activations."}),
        ("POST", f"{POLAR_API}/validate"): FakeResponse(json=GRANTED)})
    lic.activate("KEY")
    assert lic.is_pro() and session.calls[-1][2]["json"] == {"key": "KEY", "organization_id": ORG}
    full, _, _ = _license(routes={("POST", f"{POLAR_API}/activate"): FakeResponse(
        403, json={"detail": "License key activation limit already reached"})})
    with pytest.raises(LicenseError, match="limit"):
        full.activate("KEY")
