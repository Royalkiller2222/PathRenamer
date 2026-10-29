"""Free and Pro: the trial, license keys checked with Polar, working offline."""
import pytest
import requests

from conftest import FakeResponse, FakeSession
from licensing import DAY, License, LicenseError, POLAR_API

ORG = "org-123"


class Clock:
    def __init__(self, now=1_700_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def _license(cfg=None, routes=None, error=None, org=ORG):
    clock = Clock()
    session = FakeSession(routes, error=error)
    return License({} if cfg is None else cfg, organization_id=org, session=session, clock=clock), clock, session


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
    assert lic.is_pro() and lic.cfg["license_activation_id"] == "act-1"
    assert "Pro is active (****-E304DA)" in lic.summary()


def test_activation_without_key_details_validates():
    lic, _, session = _license(routes={
        ("POST", f"{POLAR_API}/activate"): FakeResponse(json={"id": "act-9"}),
        ("POST", f"{POLAR_API}/validate"): FakeResponse(json=GRANTED)})
    lic.activate("KEY-2")
    assert lic.is_pro()
    assert session.calls[1][2]["json"] == {"key": "KEY-2", "organization_id": ORG, "activation_id": "act-9"}


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
    assert not lic.is_pro() and not lic.cfg.get("license_key")


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
    assert not lic.is_pro() and not any(k.startswith("license_") for k in lic.cfg)
