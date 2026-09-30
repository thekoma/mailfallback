# tests/test_status_consistency.py
"""Every surface agrees on a mailbox's health — the point of the unified
resolver (docs/designs/mailbox-status-resolver.md, "Cross-surface
consistency"). One account per state; each surface is checked against the
resolver's verdict for that account."""

import re
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from mailfallback.models import Account, AuthType, JobStatus, SyncJob, SyncState, UserRole
from mailfallback.services import app_credential_service as svc
from mailfallback.services import group_service
from mailfallback.services.account_service import assign_owner
from mailfallback.services.mailbox_status import (
    TONE_DOT,
    MailboxState,
    Tone,
    latest_finished_jobs_by_account,
    resolve_job_outcome,
    resolve_many,
)
from mailfallback.services.user_service import create_user

_HERO = {
    "migrating": "migrating",
    "suspended": "paused",
    "sign_in_needed": "sign-in-needed",
    "first_sync": "first-sync",
    "syncing": "syncing",
    "paused": "sync-paused",
    "stopped": "stopped",
    "error": "error",
    "initial_stalled": "out-of-date",
    "stale": "out-of-date",
    "current": "idle",
}

_DAY = timedelta(days=1)
_HOST_LOG = "Sync blocked: host resolves to an internal address"


def _now():
    return datetime.now(UTC)


def _done(**kw):
    kw.setdefault("initial_sync_completed_at", _now() - 30 * _DAY)
    kw.setdefault("last_sync_at", _now() - timedelta(minutes=5))
    return kw


# case id → (expected state, account fields, job fields or None, group-shared?)
CASES = {
    "current": ("current", _done, None, False),
    "sign_in_needed_reauth": (
        "sign_in_needed",
        lambda: _done(
            provider="google",
            auth_type=AuthType.oauth2,
            credentials="x",
            sync_state=SyncState.needs_reauth,
        ),
        None,
        False,
    ),
    "sign_in_needed_no_credentials": (
        "sign_in_needed",
        lambda: {"provider": "google", "auth_type": AuthType.oauth2, "credentials": None},
        None,
        False,
    ),
    "error_app_password": (
        "error",
        lambda: _done(
            auth_type=AuthType.app_password,
            sync_state=SyncState.error,
            last_error="AUTHENTICATIONFAILED Invalid credentials",
        ),
        {"status": JobStatus.failed, "failure_kind": "error"},
        False,
    ),
    "paused_budget": (
        "paused",
        lambda: _done(pause_reason="budget", sync_paused_until=_now() + 5 * timedelta(hours=1)),
        {"status": JobStatus.failed, "failure_kind": "budget_paused"},
        False,
    ),
    "stopped": (
        "stopped",
        lambda: _done(sync_state=SyncState.error, last_error="stopped by user"),
        {"status": JobStatus.failed, "signal": "SIGTERM", "log": "stopped by user"},
        False,
    ),
    "stale": ("stale", lambda: _done(last_sync_at=_now() - 10 * _DAY), None, False),
    "initial_stalled": (
        "initial_stalled",
        lambda: {"created_at": _now() - 10 * _DAY, "initial_sync_total_messages": 500},
        None,
        False,
    ),
    "first_sync": ("first_sync", lambda: {"sync_state": SyncState.syncing}, None, False),
    "syncing": ("syncing", lambda: _done(sync_state=SyncState.syncing), None, False),
    "initial_sync": (
        "initial_sync",
        lambda: {"initial_sync_total_messages": 500},
        {"status": JobStatus.completed},
        False,
    ),
    "suspended": ("suspended", lambda: _done(suspended=True), None, False),
    "hidden_current": ("current", lambda: _done(enabled=False), None, False),
    "paused_and_stale": (
        "stale",
        lambda: _done(
            pause_reason="budget",
            sync_paused_until=_now() + 5 * timedelta(hours=1),
            last_sync_at=_now() - 10 * _DAY,
        ),
        None,
        False,
    ),
    # A user stop, then the host re-validation guard fails a job before it
    # starts (no started_at) and sets the account to error: a real error.
    "error_after_stop_then_host_guard": (
        "error",
        lambda: _done(sync_state=SyncState.error, last_error=_HOST_LOG),
        {"status": JobStatus.failed, "signal": "SIGTERM", "log": "stopped by user"},
        False,
    ),
    "owner_migrating": ("migrating", _done, None, False),
    "group_shared_error": (
        "error",
        lambda: _done(
            auth_type=AuthType.app_password,
            sync_state=SyncState.error,
            last_error="AUTHENTICATIONFAILED Invalid credentials",
        ),
        {"status": JobStatus.failed, "failure_kind": "error"},
        True,
    ),
}


@pytest.fixture(autouse=True)
def _dovecot_up():
    with patch(
        "mailfallback.services.dovecot_manager.get_cached_health", return_value={"ok": True}
    ):
        yield


def _login(client, username):
    client.post("/api/auth/logout")
    client.cookies.clear()
    client.post("/api/auth/login", json={"username": username, "password": "pass"})


def _text(fragment: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", fragment).split())


def _block(html: str, heading: str) -> str:
    m = re.search(heading + r".*?</details>", html, re.S)
    return m.group(0) if m else ""


def _expected_local_text(tone: Tone, state: MailboxState) -> str:
    if tone == Tone.error:
        return "1 of 1 failing"
    if tone == Tone.attention:
        return "1 of 1 need attention"
    if tone in (Tone.ok, Tone.active):
        return "1 of 1 healthy"
    suffix = {
        MailboxState.suspended: "suspended",
        MailboxState.stopped: "stopped",
        MailboxState.waiting: "not started",
    }[state]
    return f"0 of 1 healthy · 1 {suffix}"


@pytest.mark.parametrize("case", list(CASES))
def test_every_surface_agrees(client, db_session, default_store, case):
    expected_state, fields, job_fields, shared = CASES[case]
    admin = create_user(db_session, "cadmin", "pass", UserRole.admin, store_id=default_store.id)
    kw = fields()
    kw.setdefault("sync_schedule", "0 * * * *")
    account = Account(
        name=f"Subject-{case}",
        imap_host="imap.example.com",
        maildir_path=f"/data/mailboxes/{uuid.uuid4()}",
        store_id=default_store.id,
        **{"provider": "other", **kw},
    )
    db_session.add(account)
    db_session.commit()
    assign_owner(db_session, account.id, admin.id)
    if case == "owner_migrating":
        # A second owner mid-migration blocks every sync of the account.
        mover = create_user(db_session, "cmover", "pass", UserRole.user, store_id=default_store.id)
        assign_owner(db_session, account.id, mover.id)
        mover.migrating = True
        db_session.commit()
    if job_fields:
        now = _now()
        db_session.add(
            SyncJob(
                account_id=account.id,
                requested_at=now - timedelta(minutes=6),
                started_at=now - timedelta(minutes=6),
                completed_at=now - timedelta(minutes=4),
                **job_fields,
            )
        )
        db_session.commit()
    if case == "error_after_stop_then_host_guard":
        db_session.add(
            SyncJob(
                account_id=account.id,
                status=JobStatus.failed,
                requested_at=_now() - timedelta(minutes=1),
                completed_at=_now() - timedelta(minutes=1),
                log=_HOST_LOG,
            )
        )
        db_session.commit()

    viewer = admin
    if shared:
        viewer = create_user(
            db_session, "cmember", "pass", UserRole.user, store_id=default_store.id
        )
        group = group_service.create_group(db_session, "family", admin.id)
        group_service.add_member(db_session, group.id, viewer.id)
        group_service.set_group_accounts(db_session, group.id, [account.id])

    status = resolve_many(db_session, [account])[account.id]
    assert status.state.value == expected_state, case
    last_job = latest_finished_jobs_by_account(db_session, [account.id]).get(account.id)

    _, token = svc.create_credential(
        db_session, viewer, name="consistency", scopes=[svc.SCOPE_MAIL_READ]
    )
    _login(client, viewer.username)

    # Accounts table: the resolver's badge and label.
    table = client.get("/partials/accounts-table").text
    assert f'<span class="badge {status.badge}"' in table
    assert status.label in table

    # Dashboard: in Needs Attention iff needs_attention; chain Local backup text.
    dash = client.get("/").text
    attention = _block(dash, "Needs Attention")
    assert (account.name in attention) == status.needs_attention
    local = re.search(r"</i> Local backup.*?</div>\s*</a>", dash, re.S).group(0)
    assert _text(local).endswith(_expected_local_text(status.tone, status.state))
    local_tone = status.tone if status.tone in (Tone.error, Tone.attention) else Tone.ok
    assert f'<span class="stats-dot {TONE_DOT[local_tone.value]}"></span>' in local

    # Detail page: Local sync dot tone + hero container class.
    detail = client.get(f"/accounts/{account.id}").text
    dot = re.search(
        r'<span class="stats-dot ([^"]*)"></span>\s*<span class="health-label">Local sync',
        detail,
    ).group(1)
    assert dot.strip() == TONE_DOT[status.tone.value]
    hero = re.search(r'id="hero-panel"\s+class="hero-panel hero-([a-z-]+)"', detail).group(1)
    expected_hero = _HERO.get(status.state.value) or (
        "empty" if account.last_sync_at is None else "idle"
    )
    assert hero == expected_hero

    # Agent API: same state and tone.
    agent = client.get(
        "/api/v1/agent/mailboxes", headers={"Authorization": f"Bearer {token}"}
    ).json()
    mine = next(m for m in agent if m["account_id"] == account.id)
    assert mine["status"]["state"] == status.state.value
    assert mine["status"]["tone"] == status.tone.value

    # The account's last finished job carries the same outcome badge on the
    # dashboard's Recent Activity and on the detail Sync History.
    if last_job is not None:
        outcome = resolve_job_outcome(last_job)
        activity = _block(dash, "Recent Activity")
        assert f'<span class="badge {outcome.badge}">' in activity
        history = detail[detail.index("Sync History") :]
        assert f'<span class="badge {outcome.badge}">{outcome.label}</span>' in history
        if not (status.tone == Tone.error or last_job.failure_kind == "error"):
            assert "badge-error" not in activity
            assert "badge-error" not in history

    if shared:
        # A group member can see the mailbox but cannot edit or reconnect it.
        assert "Update password" not in dash
        assert f"/accounts/{account.id}#admin-edit" not in dash
        assert '<a class="icon-btn" href="#admin-edit">' not in detail
        assert "/auth/" not in attention
        assert "Reconnect with" not in detail
        _login(client, admin.username)

    # System strip (admin-only): errors iff tone error, sign-in iff sign_in_needed.
    strip = client.get("/partials/system-status").text
    error_rows = re.findall(r'text-error-state">\s*&#10007; <a href="/accounts/([^"]+)"', strip)
    signin_rows = re.findall(r'text-warn-state">\s*&#9888; <a href="/accounts/([^"]+)"', strip)
    assert (account.id in error_rows) == (status.tone == Tone.error)
    assert (account.id in signin_rows) == (status.state == MailboxState.sign_in_needed)
