"""The dashboard must surface accounts that need re-authorization, so a
revoked OAuth token (e.g. after a Gmail password change) is visible without
opening the account (2026-06-28)."""

import re
import uuid
from datetime import UTC, datetime, timedelta

from mailfallback.models import Account, AuthType, JobStatus, SyncJob, SyncState, UserRole
from mailfallback.services import sync_worker
from mailfallback.services.account_service import assign_owner, create_account
from mailfallback.services.user_service import create_user


def test_dashboard_flags_needs_reauth_account(client, db_session, default_store):
    user = create_user(db_session, "u", "secretpass123", UserRole.user, store_id=default_store.id)
    account = create_account(
        db_session, "Gmail", "imap.gmail.com", 993, "oauth2", store=default_store
    )
    account.provider = "google"
    account.credentials = "revoked-token"
    account.sync_state = SyncState.needs_reauth
    db_session.commit()
    assign_owner(db_session, account.id, user.id)

    client.post(
        "/login",
        data={"username": "u", "password": "secretpass123"},
        follow_redirects=False,
    )
    resp = client.get("/")

    assert resp.status_code == 200
    # surfaced in the Needs attention panel with a one-click reconnect link
    assert "Google sign-in expired. Reconnect to resume syncing." in resp.text
    assert f"/auth/google/start?account_id={account.id}" in resp.text


# --- mailbox status resolver (docs/designs/mailbox-status-resolver.md) -------


def _admin(client, db_session, default_store):
    user = create_user(db_session, "dashadmin", "pass", UserRole.admin, store_id=default_store.id)
    client.post("/api/auth/login", json={"username": "dashadmin", "password": "pass"})
    return user


def _box(db_session, default_store, **kw):
    kw.setdefault("initial_sync_completed_at", datetime.now(UTC) - timedelta(days=30))
    kw.setdefault("last_sync_at", datetime.now(UTC) - timedelta(minutes=5))
    account = Account(
        name=kw.pop("name", "Box"),
        provider=kw.pop("provider", "other"),
        imap_host="imap.example.com",
        maildir_path=f"/data/mailboxes/{uuid.uuid4()}",
        store_id=default_store.id,
        **kw,
    )
    db_session.add(account)
    db_session.commit()
    return account


def _attention_block(html: str) -> str:
    m = re.search(r"Needs attention.*?</details>", html, re.S)
    return m.group(0) if m else ""


def test_initial_sync_is_not_an_attention_item(client, db_session, default_store):
    _admin(client, db_session, default_store)
    a = _box(
        db_session,
        default_store,
        name="FreshGmail",
        initial_sync_completed_at=None,
        last_sync_at=None,
        initial_sync_total_messages=5000,
    )
    sync_worker._live_progress["dash-job"] = {"account_id": a.id, "pct": 40.0}
    try:
        resp = client.get("/")
    finally:
        sync_worker._live_progress.pop("dash-job", None)
    assert resp.status_code == 200
    assert "FreshGmail" not in _attention_block(resp.text)
    assert "initial sync" not in _attention_block(resp.text)


def test_reauth_badge_is_warning_not_error(client, db_session, default_store):
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
    block = _attention_block(client.get("/").text)
    assert "Microsoft sign-in expired. Reconnect to resume syncing." in block
    assert '<span class="badge badge-warning"><i data-lucide="log-in"' in block
    assert "badge-error" not in block


def test_reauth_turns_source_stage_to_attention(client, db_session, default_store):
    _admin(client, db_session, default_store)
    for i in range(4):
        _box(db_session, default_store, name=f"ok{i}")
    _box(
        db_session,
        default_store,
        name="Outlook",
        provider="microsoft",
        auth_type=AuthType.oauth2,
        credentials="x",
        sync_state=SyncState.needs_reauth,
    )
    text = client.get("/").text
    source = re.search(r"</i> Source.*?</div>\s*</a>", text, re.S).group(0)
    assert "4 of 5 connected" in source
    assert "stats-dot-warning" in source
    local = re.search(r"</i> Local backup.*?</div>\s*</a>", text, re.S).group(0)
    assert "1 of 5 need attention" in local
    assert "stats-dot-warning" in local


def test_credential_error_offers_update_password_link(client, db_session, default_store):
    _admin(client, db_session, default_store)
    a = _box(
        db_session,
        default_store,
        name="OldISP",
        auth_type=AuthType.app_password,
        sync_state=SyncState.error,
        last_error="AUTHENTICATIONFAILED Invalid credentials",
    )
    block = _attention_block(client.get("/").text)
    assert f'href="/accounts/{a.id}#admin-edit"' in block
    assert "Update password" in block
    assert f'hx-post="/api/sync/{a.id}"' not in block
    # The reason is the classified headline, never the raw IMAP response.
    assert "— The server rejected the password.</span>" in block
    assert "AUTHENTICATIONFAILED" not in block
    assert "Invalid credentials" not in block


def test_recent_activity_budget_pause_is_not_failed(client, db_session, default_store):
    _admin(client, db_session, default_store)
    a = _box(
        db_session,
        default_store,
        name="Fastmail",
        pause_reason="budget",
        sync_paused_until=datetime.now(UTC) + timedelta(hours=5),
    )
    now = datetime.now(UTC)
    db_session.add(
        SyncJob(
            account_id=a.id,
            status=JobStatus.failed,
            failure_kind="budget_paused",
            requested_at=now - timedelta(minutes=3),
            started_at=now - timedelta(minutes=3),
            completed_at=now - timedelta(minutes=1),
        )
    )
    db_session.commit()
    text = client.get("/").text
    activity = re.search(r"Recent activity.*?</details>", text, re.S).group(0)
    assert "paused (daily limit)" in activity
    assert "badge-error" not in activity
    assert "badge-info" in activity


def test_suspended_error_is_not_attention(client, db_session, default_store):
    _admin(client, db_session, default_store)
    _box(
        db_session,
        default_store,
        name="OffOnPurpose",
        suspended=True,
        sync_state=SyncState.error,
        last_error="boom",
    )
    text = client.get("/").text
    assert "OffOnPurpose" not in _attention_block(text)
    assert "<strong>0</strong> failed" in text


def test_paused_and_stale_item_still_offers_sync_now(client, db_session, default_store):
    """The agent-facing action is None while paused, but the UI trigger
    overrides a pause, so the dashboard button follows the state."""
    _admin(client, db_session, default_store)
    a = _box(
        db_session,
        default_store,
        name="LongPaused",
        pause_reason="budget",
        sync_paused_until=datetime.now(UTC) + timedelta(hours=5),
        last_sync_at=datetime.now(UTC) - timedelta(days=10),
        sync_schedule="0 * * * *",
    )
    block = _attention_block(client.get("/").text)
    assert "LongPaused" in block
    assert "Last sync was 10 days ago." in block
    assert f'hx-post="/api/sync/{a.id}"' in block
    assert "Sync now" in block


def test_group_member_is_told_who_can_reconnect(client, db_session, default_store):
    """A group member sees a shared mailbox but cannot reconnect it: the
    dashboard says who can instead of offering a Reconnect it can't use."""
    from mailfallback.services.group_service import add_member, create_group, set_group_accounts

    owner = create_user(db_session, "owner", "pass", UserRole.user, store_id=default_store.id)
    member = create_user(db_session, "member", "pass", UserRole.user, store_id=default_store.id)
    a = _box(
        db_session,
        default_store,
        name="FamilyGmail",
        provider="google",
        auth_type=AuthType.oauth2,
        credentials="x",
        sync_state=SyncState.needs_reauth,
    )
    assign_owner(db_session, a.id, owner.id)
    group = create_group(db_session, "family", owner.id)
    add_member(db_session, group.id, member.id)
    set_group_accounts(db_session, group.id, [a.id])

    client.post("/api/auth/login", json={"username": "member", "password": "pass"})
    block = _attention_block(client.get("/").text)
    assert "FamilyGmail" in block
    assert "Ask the mailbox owner or an admin to reconnect it." in block
    assert "Reconnect to resume syncing" not in block
    assert "/auth/google/start" not in block
    assert "Reconnect</a>" not in block


def test_dashboard_counts_read_as_mailboxes_with_separators(client, db_session, default_store):
    _admin(client, db_session, default_store)
    _box(db_session, default_store, name="Big", total_messages=279986)
    text = client.get("/").text
    assert '<div class="stat-value">279,986</div>' in text
    assert "279986" not in text
    assert "</i> Mailbox</div>" in text  # one mailbox: singular
    assert 'class="icon-nav"></i>Mailboxes</a>' in text


def test_chain_hint_is_shown_to_every_role(client, db_session, default_store):
    user = create_user(db_session, "hintuser", "pass", UserRole.user, store_id=default_store.id)
    a = _box(db_session, default_store, name="Mine")
    assign_owner(db_session, a.id, user.id)
    client.post("/api/auth/login", json={"username": "hintuser", "password": "pass"})
    text = client.get("/").text
    assert "Click a stage's name below to open it." in text
    assert "to manage it" not in text
