# tests/test_system_status_strip.py
"""The admin system status strip counts MAILBOXES from the resolver's verdict
(docs/designs/mailbox-status-resolver.md, UX flow 4)."""

import re
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from mailfallback.models import Account, AuthType, JobStatus, SyncJob, SyncState, UserRole
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


def _sync_pill(html: str) -> str:
    m = re.search(
        r'<span class="status-badge ([a-z-]+)"\s+'
        r"onclick=\"toggleStatusDetail\('sync'\)\">(.*?)</span>",
        html,
        re.S,
    )
    assert m, html
    return m.group(1) + " | " + " ".join(re.sub(r"<[^>]+>", " ", m.group(2)).split())


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
    # A paused, otherwise-quiet system has nothing to report.
    assert client.get(URL).text == ""

    _box(db_session, default_store, name="Broken", sync_state=SyncState.error, last_error="boom")
    html = client.get(URL).text
    assert _sync_pill(html) == "status-error | 0 syncing · 1 error"
    assert "Broken</a> — error: boom" in html
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
    assert 'class="text-small text-warn-state"' in html
    assert "Outlook</a> — Sign-in needed" in html


def test_strip_ignores_stale(client, db_session, default_store):
    _admin(client, db_session, default_store)
    _box(
        db_session, default_store, name="Stale", last_sync_at=datetime.now(UTC) - timedelta(days=30)
    )
    assert client.get(URL).text == ""


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
    assert _sync_pill(html) == "status-error | 2 syncing · 2 errors · 2 need sign-in"
    assert "2 mailboxes syncing" in html


def test_strip_is_admin_only(client, db_session, default_store):
    create_user(db_session, "plain", "pass", UserRole.user, store_id=default_store.id)
    _box(db_session, default_store, name="Broken", sync_state=SyncState.error, last_error="x")
    client.post("/api/auth/login", json={"username": "plain", "password": "pass"})
    assert client.get(URL).text == ""
    client.post("/api/auth/logout")
    client.cookies.clear()
    assert client.get(URL).text == ""
