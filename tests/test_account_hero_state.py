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
        r'<span class="stats-dot ([^"]*)"></span>\s*<span class="health-label">Sign-in</span>', html
    )
    assert m, "Sign-in health row not found"
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


def test_hero_error_headline_is_classified_not_raw(client, db_session, default_store):
    create_user(db_session, "hadmin", "pass", UserRole.admin, store_id=default_store.id)
    bad = _app_password_box(
        db_session,
        default_store,
        sync_state=SyncState.error,
        last_error="AUTHENTICATIONFAILED Invalid credentials",
    )
    _login(client, "hadmin", "pass")
    panel = client.get(f"/accounts/{bad.id}/partials/sync-panel").text
    assert "<strong>Sync failed</strong>" in panel
    assert (
        '<p class="hero-error-headline"><strong>The server rejected the password.</strong></p>'
        in panel
    )
    # The raw text survives only inside the collapsed error log.
    headline_end = panel.index("The server rejected the password.")
    assert "AUTHENTICATIONFAILED" not in panel[:headline_end]
    assert "<summary" in panel[headline_end : panel.index("AUTHENTICATIONFAILED")]


def test_hero_sign_in_needed_tells_group_member_who_can_reconnect(
    client, db_session, default_store
):
    from mailfallback.services import group_service
    from mailfallback.services.account_service import assign_owner

    owner = create_user(db_session, "gowner", "pass", UserRole.user, store_id=default_store.id)
    member = create_user(db_session, "gmember", "pass", UserRole.user, store_id=default_store.id)
    a = Account(
        name="FamilyGmail",
        provider="google",
        imap_host="imap.gmail.com",
        maildir_path=f"/data/mailboxes/{uuid.uuid4()}",
        store_id=default_store.id,
        auth_type=AuthType.oauth2,
        credentials="x",
        sync_state=SyncState.needs_reauth,
        initial_sync_completed_at=datetime.now(UTC) - timedelta(days=30),
    )
    db_session.add(a)
    db_session.commit()
    assign_owner(db_session, a.id, owner.id)
    group = group_service.create_group(db_session, "family", owner.id)
    group_service.add_member(db_session, group.id, member.id)
    group_service.set_group_accounts(db_session, group.id, [a.id])

    _login(client, "gowner", "pass")
    panel = client.get(f"/accounts/{a.id}/partials/sync-panel").text
    assert "Reconnect with Google" in panel
    assert "Google sign-in expired. Reconnect to resume syncing." in panel
    assert "Ask the mailbox owner" not in panel

    client.post("/api/auth/logout")
    client.cookies.clear()
    _login(client, "gmember", "pass")
    panel = client.get(f"/accounts/{a.id}/partials/sync-panel").text
    assert "Sign-in needed" in panel
    assert "Ask the mailbox owner or an admin to reconnect it." in panel
    assert "/auth/google/start" not in panel
    assert "Reconnect with" not in panel
    assert "as soon as you reconnect" not in panel


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
    # Said once: the Last sync line, not also the detail sentence.
    assert "Last sync was 10 days ago." not in html
    assert html.count("Last sync 10d ago") == 1
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
        assert "Last successful sync" in html
        assert "4,321 messages" in html


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


def test_hero_headline_matches_dashboard_when_the_log_has_two_errors(
    client, db_session, default_store
):
    """The snapshot's FIRST Error: line is a network timeout, the log also
    holds an auth failure. The hero and the dashboard classify the same
    input (last_error), so they show the same headline."""
    import dataclasses

    from mailfallback.services.sync_progress import parse_mbsync_lines

    lines = [
        "Error: connection timed out to imap.example.com",
        "IMAP error: AUTHENTICATIONFAILED Invalid credentials",
    ]
    log = "\n".join(lines)
    snap = parse_mbsync_lines(lines)
    assert snap.errors[0].category == "network"  # the trap
    create_user(db_session, "hadmin", "pass", UserRole.admin, store_id=default_store.id)
    a = _app_password_box(db_session, default_store, sync_state=SyncState.error, last_error=log)
    now = datetime.now(UTC)
    db_session.add(
        SyncJob(
            account_id=a.id,
            status=JobStatus.failed,
            failure_kind="error",
            log=log,
            parsed_summary=json.dumps(dataclasses.asdict(snap), default=str),
            started_at=now - timedelta(minutes=5),
            completed_at=now - timedelta(minutes=4),
        )
    )
    db_session.commit()
    _state, hero_snap, _job, _status = _compute_hero_state(a, db_session)
    assert hero_snap is not None and hero_snap.errors  # the snapshot is really used

    _login(client, "hadmin", "pass")
    panel = client.get(f"/accounts/{a.id}/partials/sync-panel").text
    headline = re.search(r'<p class="hero-error-headline"><strong>(.*?)</strong>', panel).group(1)
    dash = client.get("/").text
    reason = re.search(r'<span class="text-small text-muted">— (.*?)</span>', dash).group(1)
    assert headline == reason == "The server rejected the password."
    # Sub-text and buttons follow the same classification, not the network line.
    assert "Check the password, then update it." in panel
    assert "Test connection" not in panel


def test_token_refresh_retry_never_claims_expired(client, db_session, default_store):
    """error + TOKEN_REFRESH_FAILED self-heals: owner and member copy both say
    it retries, and the member is told who can reconnect."""
    from mailfallback.services import group_service
    from mailfallback.services.account_service import assign_owner

    owner = create_user(db_session, "rowner", "pass", UserRole.user, store_id=default_store.id)
    member = create_user(db_session, "rmember", "pass", UserRole.user, store_id=default_store.id)
    a = Account(
        name="FamilyGmail",
        provider="google",
        imap_host="imap.gmail.com",
        maildir_path=f"/data/mailboxes/{uuid.uuid4()}",
        store_id=default_store.id,
        auth_type=AuthType.oauth2,
        credentials="x",
        sync_state=SyncState.error,
        last_error=TOKEN_REFRESH_FAILED,
        initial_sync_completed_at=datetime.now(UTC) - timedelta(days=30),
        last_sync_at=datetime.now(UTC) - timedelta(hours=1),
    )
    db_session.add(a)
    db_session.commit()
    assign_owner(db_session, a.id, owner.id)
    group = group_service.create_group(db_session, "family", owner.id)
    group_service.add_member(db_session, group.id, member.id)
    group_service.set_group_accounts(db_session, group.id, [a.id])

    _login(client, "rowner", "pass")
    panel = client.get(f"/accounts/{a.id}/partials/sync-panel").text
    assert "It retries on the next sync; reconnect if this keeps happening." in panel
    assert "expired" not in panel

    client.post("/api/auth/logout")
    client.cookies.clear()
    _login(client, "rmember", "pass")
    member_copy = (
        "Couldn&#39;t refresh the sign-in. It retries on the next sync; if this keeps "
        "happening, ask the mailbox owner or an admin to reconnect it."
    )
    panel = client.get(f"/accounts/{a.id}/partials/sync-panel").text
    assert member_copy in panel
    assert "expired" not in panel
    assert "/auth/google/start" not in panel
    dash = client.get("/").text
    block = dash[dash.index("Needs attention") : dash.index("</details>")]
    assert member_copy in block
    assert "expired" not in block
    assert "/auth/google/start" not in block


def test_host_error_instruction_follows_the_viewer(client, db_session, default_store):
    """The headline (cause) is shared; the instruction is per viewer."""
    from mailfallback.services import group_service
    from mailfallback.services.account_service import assign_owner

    owner = create_user(db_session, "hoowner", "pass", UserRole.user, store_id=default_store.id)
    member = create_user(db_session, "homember", "pass", UserRole.user, store_id=default_store.id)
    a = _app_password_box(
        db_session,
        default_store,
        sync_state=SyncState.error,
        last_error="Host could not be resolved: imap.nowhere.invalid",
    )
    assign_owner(db_session, a.id, owner.id)
    group = group_service.create_group(db_session, "family", owner.id)
    group_service.add_member(db_session, group.id, member.id)
    group_service.set_group_accounts(db_session, group.id, [a.id])

    _login(client, "hoowner", "pass")
    panel = client.get(f"/accounts/{a.id}/partials/sync-panel").text
    assert "<strong>Couldn&#39;t find the IMAP host.</strong>" in panel
    assert "Check the IMAP host in the mailbox settings." in panel
    assert "Ask the mailbox owner" not in panel

    client.post("/api/auth/logout")
    client.cookies.clear()
    _login(client, "homember", "pass")
    panel = client.get(f"/accounts/{a.id}/partials/sync-panel").text
    assert "<strong>Couldn&#39;t find the IMAP host.</strong>" in panel
    assert "Ask the mailbox owner or an admin to check the mailbox settings." in panel
    assert "Check the IMAP host" not in panel
    dash = client.get("/").text
    # Dashboard reason = the headline only, no instruction.
    assert "— Couldn&#39;t find the IMAP host.</span>" in dash
    assert "Check the IMAP host" not in dash


def test_disk_error_instruction_is_for_admins_only(client, db_session, default_store):
    from mailfallback.services.account_service import assign_owner

    create_user(db_session, "dadmin", "pass", UserRole.admin, store_id=default_store.id)
    owner = create_user(db_session, "downer", "pass", UserRole.user, store_id=default_store.id)
    a = _app_password_box(
        db_session,
        default_store,
        sync_state=SyncState.error,
        last_error="Maildir: No space left on device",
    )
    assign_owner(db_session, a.id, owner.id)

    _login(client, "dadmin", "pass")
    panel = client.get(f"/accounts/{a.id}/partials/sync-panel").text
    assert "Check the disk space and permissions of the mail store." in panel

    client.post("/api/auth/logout")
    client.cookies.clear()
    _login(client, "downer", "pass")
    panel = client.get(f"/accounts/{a.id}/partials/sync-panel").text
    assert "Ask an admin to check the mail store." in panel
    assert "disk space" not in panel


def test_password_noun_follows_the_provider(client, db_session, default_store):
    """AuthType.app_password also covers a generic server's normal password."""
    create_user(db_session, "padmin", "pass", UserRole.admin, store_id=default_store.id)
    gmail = _app_password_box(db_session, default_store, name="gm", provider="google")
    isp = _app_password_box(db_session, default_store, name="isp", provider="other")
    _login(client, "padmin", "pass")
    table = client.get("/partials/accounts-table").text
    assert "App password" in table and "\n                    Password\n" in table
    gm = client.get(f"/accounts/{gmail.id}").text
    assert '<span class="health-value">App password</span>' in gm
    assert "New app password (leave blank" in gm
    other = client.get(f"/accounts/{isp.id}").text
    assert '<span class="health-value">Password</span>' in other
    assert "New password (leave blank" in other


def test_fixed_host_provider_unresolved_host_points_at_dns(client, db_session, default_store):
    """Gmail's IMAP host is fixed and hidden in the edit form: the hero must
    not send anyone to the settings; admins check DNS/network, others ask."""
    from mailfallback.services.account_service import assign_owner

    create_user(db_session, "fadmin", "pass", UserRole.admin, store_id=default_store.id)
    owner = create_user(db_session, "fowner", "pass", UserRole.user, store_id=default_store.id)
    a = _app_password_box(
        db_session,
        default_store,
        provider="google",
        sync_state=SyncState.error,
        last_error="Host could not be resolved: imap.gmail.com",
    )
    assign_owner(db_session, a.id, owner.id)

    _login(client, "fadmin", "pass")
    panel = client.get(f"/accounts/{a.id}/partials/sync-panel").text
    assert "MFB couldn't look up Google's mail server correctly from here." in panel
    assert "if it keeps failing, check this server's DNS and network." in panel
    assert "Check the IMAP host" not in panel
    assert "mailbox settings" not in panel

    client.post("/api/auth/logout")
    client.cookies.clear()
    _login(client, "fowner", "pass")
    panel = client.get(f"/accounts/{a.id}/partials/sync-panel").text
    assert "ask an admin to check this server's DNS and network." in panel
    assert "Check the IMAP host" not in panel


def test_update_button_uses_the_password_noun(client, db_session, default_store):
    create_user(db_session, "uadmin", "pass", UserRole.admin, store_id=default_store.id)
    gmail = _app_password_box(
        db_session,
        default_store,
        name="gm",
        provider="google",
        sync_state=SyncState.error,
        last_error="AUTHENTICATIONFAILED Invalid credentials",
    )
    _login(client, "uadmin", "pass")
    panel = client.get(f"/accounts/{gmail.id}/partials/sync-panel").text
    assert "</i> Update app password</a>" in panel
    dash = client.get("/").text
    assert "</i> Update app password</a>" in dash
    assert "</i> Update password</a>" not in dash
