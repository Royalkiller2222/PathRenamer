"""Passwords and tokens are stored encrypted for the Windows user."""
import sys

import pytest

from secret import PREFIX, protect, unprotect


@pytest.mark.skipif(sys.platform != "win32", reason="Windows encryption (DPAPI)")
def test_round_trip_on_windows():
    stored = protect("plex-token-ÄÖ")
    assert stored.startswith(PREFIX) and "plex-token" not in stored
    assert unprotect(stored) == "plex-token-ÄÖ"
    assert unprotect(PREFIX + "bm90IGVuY3J5cHRlZA==") == ""     # not ours: empty, no crash


def test_plain_values_and_other_systems():
    assert protect("") == "" and unprotect("") == "" and unprotect(None) == ""
    assert unprotect("typed-by-hand") == "typed-by-hand"
    if sys.platform != "win32":
        assert protect("x") == "x" and unprotect(PREFIX + "abc") == ""
