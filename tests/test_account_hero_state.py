# tests/test_account_hero_state.py
"""Hero panel state for the account detail page."""

import json
import re
import uuid
from datetime import UTC, datetime, timedelta

from mailfallback.models import Account, AuthType, JobStatus, SyncJob, SyncState, UserRole
from mailfallback.routers.ui_accounts import _compute_hero_state
from mailfallback.services.account_service import create_account
from mailfallback.services.sync_worker import TOKEN_REFRESH_FAILED
from mailfallback.services.user_service import create_user


def _oauth_account(db_session, default_store):
    create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    account = create_account(
        db_session,
        name="Gmail",
        imap_host="imap.gmail.com",
        imap_port=993,
        auth_type="oauth2",
        store=default_store,
        provider="google",
    )
    account.credentials = "encrypted-but-stale"
    db_session.commit()
    return account


def test_token_refresh_failure_maps_to_sign_in_needed(db_session, default_store):
    """A revoked/expired refresh token leaves credentials in place, so
    is_authenticated stays True — the hero must still ask to reconnect
    instead of showing 'Backup failed — unknown error'."""
    account = _oauth_account(db_session, default_store)
    account.sync_state = SyncState.error
    account.last_error = TOKEN_REFRESH_FAILED
    db_session.commit()

    state, _snap, _job, _status = _compute_hero_state(account, db_session)
    assert state == "sign-in-needed"


def test_needs_reauth_hero_is_sign_in_needed(db_session, oauth_account):
    """needs_reauth must surface the reconnect flow, not the generic error panel."""
    from mailfallback.models import SyncState
    from mailfallback.routers.ui_accounts import _compute_hero_state

    oauth_account.sync_state = SyncState.needs_reauth
    db_session.commit()
    state, _snap, _job, _status = _compute_hero_state(oauth_account, db_session)
    assert state == "sign-in-needed"


def test_other_errors_still_map_to_error_state(db_session, default_store):
    account = _oauth_account(db_session, default_store)
    account.sync_state = SyncState.error
    account.last_error = "mbsync exited with code 1"
    db_session.commit()

    state, _snap, _job, _status = _compute_hero_state(account, db_session)
    assert state == "error"


# --- mailbox status resolver (docs/designs/mailbox-status-resolver.md) -------


def _login(client, username, password):
    client.post("/api/auth/login", json={"username": username, "password": password})


def _auth_dot(html: str) -> str:
    m = re.search(
        r'<span class="stats-dot ([^"]*)"></span>\s*<span class="health-label">Auth</span>', html
    )
    assert m, "Auth health row not found"
    return m.group(1)


def _hero_class(html: str) -> str:
    m = re.search(r'id="hero-panel"\s+class="hero-panel hero-([a-z-]+)"', html)
    assert m, "hero panel not found"
    return m.group(1)


def _app_password_box(db_session, default_store, **kw):
    kw.setdefault("initial_sync_completed_at", datetime.now(UTC) - timedelta(days=30))
    kw.setdefault("last_sync_at", datetime.now(UTC) - timedelta(minutes=5))
    account = Account(
        name=kw.pop("name", "ISP"),
        imap_host="imap.example.com",
        maildir_path=f"/data/mailboxes/{uuid.uuid4()}",
        store_id=default_store.id,
        auth_type=AuthType.app_password,
        **kw,
    )
    db_session.add(account)
    db_session.commit()
    return account


def test_health_auth_dot_warns_on_needs_reauth(client, db_session, oauth_account):
    create_user(db_session, "hadmin", "pass", UserRole.admin, store_id=oauth_account.store_id)
    oauth_account.sync_state = SyncState.needs_reauth
    db_session.commit()
    _login(client, "hadmin", "pass")
    html = client.get(f"/accounts/{oauth_account.id}").text
    assert _auth_dot(html) == "stats-dot-warning"
    assert _hero_class(html) == "sign-in-needed"


def test_health_auth_dot_warns_on_rejected_app_password(client, db_session, default_store):
    create_user(db_session, "hadmin", "pass", UserRole.admin, store_id=default_store.id)
    ok = _app_password_box(db_session, default_store, name="fine")
    bad = _app_password_box(
        db_session,
        default_store,
        name="rejected",
        sync_state=SyncState.error,
        last_error="AUTHENTICATIONFAILED Invalid credentials",
    )
    _login(client, "hadmin", "pass")
    assert _auth_dot(client.get(f"/accounts/{ok.id}").text) == "stats-dot-ok"
    html = client.get(f"/accounts/{bad.id}").text
    assert _auth_dot(html) == "stats-dot-warning"
    # Local sync dot follows the resolver's tone (error → red)
    assert re.search(
        r'<span class="stats-dot stats-dot-error"></span>\s*<span class="health-label">Local sync',
        html,
    )


def test_hero_error_offers_update_password_to_owner_only(client, db_session, default_store):
    from mailfallback.services import group_service
    from mailfallback.services.account_service import assign_owner

    owner = create_user(db_session, "howner", "pass", UserRole.user, store_id=default_store.id)
    member = create_user(db_session, "hmember", "pass", UserRole.user, store_id=default_store.id)
    bad = _app_password_box(
        db_session,
        default_store,
        sync_state=SyncState.error,
        last_error="AUTHENTICATIONFAILED Invalid credentials",
    )
    assign_owner(db_session, bad.id, owner.id)
    group = group_service.create_group(db_session, "family", owner.id)
    group_service.add_member(db_session, group.id, member.id)
    group_service.set_group_accounts(db_session, group.id, [bad.id])

    _login(client, "howner", "pass")
    html = client.get(f"/accounts/{bad.id}").text
    assert _hero_class(html) == "error"
    assert '<a class="icon-btn" href="#admin-edit">' in html
    panel = client.get(f"/accounts/{bad.id}/partials/sync-panel").text
    assert '<a class="icon-btn" href="#admin-edit">' in panel

    client.post("/api/auth/logout")
    client.cookies.clear()
    _login(client, "hmember", "pass")
    html = client.get(f"/accounts/{bad.id}").text
    assert _hero_class(html) == "error"
    assert '<a class="icon-btn" href="#admin-edit">' not in html
    panel = client.get(f"/accounts/{bad.id}/partials/sync-panel").text
    assert '<a class="icon-btn" href="#admin-edit">' not in panel


def test_user_stop_hero_is_stopped_with_sync_now(client, db_session, default_store):
    create_user(db_session, "hadmin", "pass", UserRole.admin, store_id=default_store.id)
    a = _app_password_box(db_session, default_store, sync_state=SyncState.error, last_error="x")
    now = datetime.now(UTC)
    db_session.add(
        SyncJob(
            account_id=a.id,
            status=JobStatus.failed,
            signal="SIGTERM",
            log="x",
            started_at=now - timedelta(minutes=5),
            completed_at=now - timedelta(minutes=4),
        )
    )
    db_session.commit()
    state, _snap, job, _status = _compute_hero_state(a, db_session)
    assert state == "stopped"
    assert job is not None
    _login(client, "hadmin", "pass")
    html = client.get(f"/accounts/{a.id}").text
    assert _hero_class(html) == "stopped"
    assert "Sync stopped" in html
    assert "The last sync was stopped. The next scheduled sync runs normally." in html
    assert f'hx-post="/api/sync/{a.id}"' in html
    assert "if(d.warning){showToast(d.warning,'error')}" in html


def test_stale_account_hero_is_out_of_date(client, db_session, default_store):
    create_user(db_session, "hadmin", "pass", UserRole.admin, store_id=default_store.id)
    a = _app_password_box(
        db_session, default_store, last_sync_at=datetime.now(UTC) - timedelta(days=10)
    )
    state, _snap, _job, _status = _compute_hero_state(a, db_session)
    assert state == "out-of-date"
    _login(client, "hadmin", "pass")
    html = client.get(f"/accounts/{a.id}").text
    assert _hero_class(html) == "out-of-date"
    assert "Out of date" in html
    assert "Last sync was 10 days ago." in html
    assert "Sync now" in html


def test_hero_ignores_pending_retry_after_error(db_session, default_store):
    a = _app_password_box(
        db_session, default_store, sync_state=SyncState.error, last_error="mbsync exited 1"
    )
    now = datetime.now(UTC)
    failed = SyncJob(
        account_id=a.id,
        status=JobStatus.failed,
        failure_kind="error",
        log="mbsync exited 1",  # the worker's real-error branch: last_error = job.log
        started_at=now - timedelta(minutes=10),
        completed_at=now - timedelta(minutes=9),
        parsed_summary=json.dumps({"phase": "done"}),
    )
    db_session.add(failed)
    db_session.commit()
    before = _compute_hero_state(a, db_session)
    db_session.add(SyncJob(account_id=a.id, status=JobStatus.pending, requested_at=now))
    db_session.commit()
    after = _compute_hero_state(a, db_session)
    assert before[0] == after[0] == "error"
    assert after[2].id == failed.id
    assert after[1] is not None and after[1] == before[1]


def test_error_hero_ignores_older_job_when_host_guard_failed_unstarted(
    client, db_session, default_store
):
    """The host re-validation guard fails a job BEFORE started_at is set, so
    that job is never `last_job`; the latest started job is an OLDER failure.
    The hero must explain the account's current last_error, not that older
    job's message, log tail or "Failed <time>"."""
    create_user(db_session, "hadmin", "pass", UserRole.admin, store_id=default_store.id)
    old_error = "IMAP command 'LOGIN' returned NO - old failure A"
    a = _app_password_box(db_session, default_store, total_messages=4321)
    now = datetime.now(UTC)
    db_session.add(
        SyncJob(
            account_id=a.id,
            status=JobStatus.failed,
            failure_kind="error",
            log=old_error,
            started_at=now - timedelta(days=3),
            completed_at=now - timedelta(days=3),
            parsed_summary=json.dumps(
                {
                    "phase": "error",
                    "errors": [
                        {
                            "at_line": 1,
                            "category": "auth",
                            "user_message": "Old failure A headline",
                            "technical_detail": "old technical detail A",
                        }
                    ],
                    "raw_tail": ["old tail line A"],
                }
            ),
        )
    )
    guard_error = "Sync blocked: host resolves to an internal address"
    a.sync_state = SyncState.error
    a.last_error = guard_error
    db_session.add(
        SyncJob(
            account_id=a.id,
            status=JobStatus.failed,
            log=f"Sync blocked: {guard_error}",
            completed_at=now - timedelta(minutes=1),
        )
    )
    db_session.commit()

    state, snap, _job, _status = _compute_hero_state(a, db_session)
    assert state == "error"
    assert snap is None

    _login(client, "hadmin", "pass")
    for html in (
        client.get(f"/accounts/{a.id}").text,
        client.get(f"/accounts/{a.id}/partials/sync-panel").text,
    ):
        assert _hero_class(html) == "error"
        assert guard_error in html
        for stale in ("Old failure A headline", "old technical detail A", "old tail line A"):
            assert stale not in html
        assert "Failed 3 days ago" not in html
        assert "/log/download" not in html
        # Account facts, not job facts: they stay true and stay visible.
        assert "Last success:" in html
        assert "4,321 msgs" in html


def test_error_hero_keeps_snap_for_runtime_cap_kill(db_session, default_store):
    """The runtime-cap branch sets last_error to the cap message and APPENDS
    it to job.log, so that job still explains the hero."""
    cap = "Sync exceeded the 21600s runtime cap"
    a = _app_password_box(db_session, default_store, sync_state=SyncState.error, last_error=cap)
    now = datetime.now(UTC)
    job = SyncJob(
        account_id=a.id,
        status=JobStatus.failed,
        failure_kind="error",
        signal="SIGTERM",
        log=f"C: 1/2  B: 3/9\n{cap}",
        started_at=now - timedelta(hours=6),
        completed_at=now - timedelta(minutes=1),
        parsed_summary=json.dumps({"phase": "syncing", "raw_tail": ["C: 1/2  B: 3/9"]}),
    )
    db_session.add(job)
    db_session.commit()
    state, snap, last_job, _status = _compute_hero_state(a, db_session)
    assert state == "error"
    assert last_job is not None and last_job.id == job.id
    assert snap is not None and snap.raw_tail == ["C: 1/2  B: 3/9"]
