# tests/test_sync_failures.py
"""Failure classifier — throttle/transient signatures vs real errors.

The anchor case is the verbatim production line (Gmail OVERQUOTA BYE,
2026-06-12) that motivated the sync-budget cycle: it must classify as
``throttled`` — a self-recovering pause, never the red error path.
"""

import pytest

from mailfallback.services.sync_failures import classify_failure

# Verbatim from the production sync log ("Main gMail", 2026-06-12).
PRODUCTION_OVERQUOTA = (
    "IMAP error: unexpected BYE response: [OVERQUOTA] Account exceeded command or bandwidth limits."
)


def test_production_overquota_line_is_throttled_for_google():
    assert classify_failure(PRODUCTION_OVERQUOTA, "google") == "throttled"


def test_production_overquota_line_is_throttled_for_any_provider():
    """[OVERQUOTA] is a generic bracketed response code — provider-agnostic."""
    assert classify_failure(PRODUCTION_OVERQUOTA, "other") == "throttled"


def test_unknown_provider_key_still_gets_generic_and_transient():
    """Providers outside the signature table must not KeyError — the generic
    bracketed codes and the transient list still apply."""
    assert classify_failure("BYE [THROTTLED] slow down", "yahoo") == "throttled"
    assert classify_failure("read: Connection reset by peer", "yahoo") == "transient"


@pytest.mark.parametrize(
    "tail",
    [
        "Socket error: timeout",
        "unexpected EOF on IMAP connection",
        "write: Broken pipe",
        "Connection timed out while fetching UID 4242",
    ],
)
def test_network_blips_are_transient(tail):
    assert classify_failure(tail, "google") == "transient"


def test_microsoft_prose_throttle():
    assert classify_failure("Request is throttled. Suggested backoff: 300s", "microsoft") == (
        "throttled"
    )


def test_prose_signatures_match_case_insensitively():
    assert classify_failure("TOO MANY SIMULTANEOUS CONNECTIONS", "google") == "throttled"
    assert classify_failure("SOCKET TIMED OUT", "other") == "transient"


def test_bracketed_codes_match_case_sensitively():
    """[overquota] lowercase is NOT the protocol token — no throttle match
    (and nothing transient in the line either)."""
    assert classify_failure("unexpected BYE response: [overquota] limits", "google") is None


def test_throttle_wins_over_transient_in_mixed_logs():
    """A throttled session often dies with a network-looking tail — the
    throttle classification must win (precedence)."""
    mixed = (
        "IMAP error: unexpected BYE response: [OVERQUOTA] limits.\nsocket: Connection reset by peer"
    )
    assert classify_failure(mixed, "google") == "throttled"


def test_unknown_junk_is_none():
    assert classify_failure("AUTHENTICATIONFAILED Invalid credentials (Failure)", "google") is None


def test_empty_tail_is_none():
    assert classify_failure("", "google") is None
