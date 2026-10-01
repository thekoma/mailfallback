# tests/test_system_status_strip.py
"""The admin system status strip counts MAILBOXES from the resolver's verdict
(docs/designs/mailbox-status-resolver.md, UX flow 4)."""

import re
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from mailfallback.models import Account, AuthType, JobStatus, SyncJob, SyncState, UserRole
from mailfallback.routers.ui import SYSTEM_CALM_HTML
from mailfallback.services.user_service import create_user

URL = "/partials/system-status"


@pytest.fixture(autouse=True)
def _dovecot_up():
    # A down/unknown Dovecot would count as activity on its own; keep it quiet.
    with patch(
        "mailfallback.services.dovecot_manager.get_cached_health", return_value={"ok": True}
    ):
        yield


def _admin(client, db_session, default_store):
    create_user(db_session, "stripadmin", "pass", UserRole.admin, store_id=default_store.id)
    client.post("/api/auth/login", json={"username": "stripadmin", "password": "pass"})


def _box(db_session, default_store, **kw):
    kw.setdefault("initial_sync_completed_at", datetime.now(UTC) - timedelta(days=30))
    kw.setdefault("last_sync_at", datetime.now(UTC) - timedelta(minutes=5))
    account = Account(
        name=kw.pop("name", "Box"),
        imap_host="imap.example.com",
        maildir_path=f"/data/mailboxes/{uuid.uuid4()}",
        store_id=default_store.id,
        **kw,
    )
    db_session.add(account)
    db_session.commit()
    return account


# The panel's line tone → the old strip's chip tone, so the expectations
# below still read as before.
_TONE = {"error": "status-error", "attention": "status-warning", "active": "status-active"}


def _sync_pill(html: str) -> str:
    m = re.search(
        r'<li class="health-line health-([a-z]+)" data-health="sync">.*?'
        r'<span class="health-line-value num">(.*?)</span>',
        html,
        re.S,
    )
    assert m, html
    tone = _TONE.get(m.group(1), "status-neutral")
    return tone + " | " + " ".join(re.sub(r"<[^>]+>", " ", m.group(2)).split())


def test_strip_counts_accounts_not_jobs(client, db_session, default_store):
    _admin(client, db_session, default_store)
    a = _box(db_session, default_store, name="Syncing", sync_state=SyncState.syncing)
    # Two running jobs for the same mailbox used to read "2 syncing".
    for _ in range(2):
        db_session.add(SyncJob(account_id=a.id, status=JobStatus.running))
    db_session.commit()
    html = client.get(URL).text
    assert _sync_pill(html) == "status-active | 1 syncing"
    assert "1 mailbox syncing" in html
    # One indicator: a real button that owns the panel it opens.
    assert '<button type="button" id="health-toggle"' in html
    assert 'aria-expanded="false" aria-controls="health-panel"' in html
    assert '<div id="health-panel" class="health-panel" hidden>' in html


def test_strip_excludes_paused_from_errors(client, db_session, default_store):
    _admin(client, db_session, default_store)
    _box(
        db_session,
        default_store,
        name="Paused",
        sync_state=SyncState.error,
        last_error="OVERQUOTA",
        pause_reason="throttle",
        sync_paused_until=datetime.now(UTC) + timedelta(hours=1),
    )
    # A paused, otherwise-quiet system has nothing to report: the explicit
    # calm markup, never an empty body.
    assert client.get(URL).text == SYSTEM_CALM_HTML

    _box(db_session, default_store, name="Broken", sync_state=SyncState.error, last_error="boom")
    html = client.get(URL).text
    assert _sync_pill(html) == "status-error | 0 syncing · 1 failed"
    # The classified headline; the raw last_error is not the strip's message.
    # Unclassified: the label alone, not "Sync failed: The last sync failed."
    assert "Broken</a> — Sync failed\n" in html
    assert "The last sync failed." not in html
    assert "boom" not in html
    assert "Paused</a>" not in html


def test_strip_shows_reauth_as_needs_sign_in_warning(client, db_session, default_store):
    _admin(client, db_session, default_store)
    _box(
        db_session,
        default_store,
        name="Outlook",
        provider="microsoft",
        auth_type=AuthType.oauth2,
        credentials="x",
        sync_state=SyncState.needs_reauth,
    )
    html = client.get(URL).text
    assert _sync_pill(html) == "status-warning | 0 syncing · 1 needs sign-in"
    assert 'class="health-account health-account-attention"' in html
    assert "Outlook</a> — Sign-in needed" in html


def test_strip_ignores_stale(client, db_session, default_store):
    _admin(client, db_session, default_store)
    _box(
        db_session, default_store, name="Stale", last_sync_at=datetime.now(UTC) - timedelta(days=30)
    )
    assert client.get(URL).text == SYSTEM_CALM_HTML


def test_strip_pluralises_counts(client, db_session, default_store):
    _admin(client, db_session, default_store)
    for i in range(2):
        _box(db_session, default_store, name=f"err{i}", sync_state=SyncState.error, last_error="x")
        _box(
            db_session,
            default_store,
            name=f"reauth{i}",
            auth_type=AuthType.oauth2,
            credentials="x",
            sync_state=SyncState.needs_reauth,
        )
        _box(db_session, default_store, name=f"sync{i}", sync_state=SyncState.syncing)
    html = client.get(URL).text
    assert _sync_pill(html) == "status-error | 2 syncing · 2 failed · 2 need sign-in"
    assert "2 mailboxes syncing" in html


def test_strip_is_admin_only(client, db_session, default_store):
    create_user(db_session, "plain", "pass", UserRole.user, store_id=default_store.id)
    _box(db_session, default_store, name="Broken", sync_state=SyncState.error, last_error="x")
    client.post("/api/auth/login", json={"username": "plain", "password": "pass"})
    resp = client.get(URL)
    # Non-2xx with no content: an empty 200 would swap an empty bar in; a 401
    # makes the page show "Status unavailable" instead.
    assert resp.status_code == 401 and resp.text == ""
    client.post("/api/auth/logout")
    client.cookies.clear()
    resp = client.get(URL)
    assert resp.status_code == 401 and resp.text == ""


def test_calm_system_answers_with_explicit_calm_markup(client, db_session, default_store):
    """Nothing happening is an answer, not an empty response: an empty bar
    would read as calm before the first poll and after a failed one."""
    _admin(client, db_session, default_store)
    _box(db_session, default_store, name="Quiet")
    body = client.get(URL).text
    assert body == SYSTEM_CALM_HTML
    assert 'data-health="calm"' in body
    assert "System normal" in body
