# tests/test_ui_sync_budget.py
"""Task 8 UI: initial-sync chips + progress, pause states, budget override,
dashboard exclusions (sync-budget spec §8).

Exclusion logic per the adopted seam notes: account-level
`pause_reason IS NOT NULL`; never `failure_kind != "error"`.
"""

from datetime import UTC, datetime, timedelta

from mailfallback.models import Account, SyncState, UserRole
from mailfallback.services import sync_worker
from mailfallback.services.user_service import create_user

BUDGET_HELP = "0 = unlimited. Applies to all syncs for this account."


def _login(client, db_session, default_store):
    user = create_user(db_session, "uibud", "pass", UserRole.admin, store_id=default_store.id)
    client.post("/api/auth/login", json={"username": "uibud", "password": "pass"})
    return user


def _mk_account(db_session, default_store, **kw):
    account = Account(
        name=kw.pop("name", "Main gMail"),
        provider=kw.pop("provider", "google"),
        imap_host="imap.gmail.com",
        maildir_path=kw.pop("maildir_path", "/data/mailboxes/uibud"),
        store_id=default_store.id,
        **kw,
    )
    db_session.add(account)
    db_session.commit()
    return account


def _seed_progress(account, pct=38.0, done=46_200, eta="≈ 3d"):
    sync_worker._live_progress["uibud-job"] = {
        "account_id": account.id,
        "done_msgs": done,
        "done_bytes": done * 1024,
        "run_msgs": 100,
        "run_bytes": 100 * 1024,
        "bytes_today": int(1.9 * 1024**3),
        "budget_bytes": 2000 * 1024 * 1024,
        "pct": pct,
        "eta": {"seconds": 3 * 86400, "days": 3.0, "label": eta},
        "rate_msgs_per_s": 12.5,
    }


def _clear_progress():
    sync_worker._live_progress.pop("uibud-job", None)


# ---------------------------------------------------------------------------
# Live-status helper (the per-account payload the partials render)
# ---------------------------------------------------------------------------


def test_account_live_status_payload_shape(db_session, default_store):
    from mailfallback.routers.ui import account_live_status

    account = _mk_account(
        db_session,
        default_store,
        initial_sync_total_messages=121_000,
        sync_paused_until=datetime(2026, 6, 14, 2, 0, tzinfo=UTC),
        pause_reason="budget",
    )
    _seed_progress(account)
    try:
        ls = account_live_status(account)
    finally:
        _clear_progress()

    assert ls["pct"] == 38.0
    assert ls["done_msgs"] == 46_200
    assert ls["total_msgs"] == 121_000
    assert ls["bytes_today"] == int(1.9 * 1024**3)
    assert ls["budget_bytes"] == 2000 * 1024 * 1024
    assert ls["eta_label"] == "≈ 3d"
    assert ls["rate_msgs_per_s"] == 12.5
    assert ls["paused_until"] is not None
    # relative, timezone-independent label (the fixed 2026-06-14 pause is in
    # the past relative to "now", so it collapses to "shortly")
    assert ls["resume_rel"] == "shortly"
    assert ls["pause_reason"] == "budget"
    assert ls["pause_tooltip"] == "Daily sync budget reached"
    assert ls["initial_sync"] is True


def test_account_live_status_without_progress_or_pause(db_session, default_store):
    """No sampler entry: budget falls back to the provider default, the rest
    degrades to None; a completed initial sync reads initial_sync=False."""
    from mailfallback.routers.ui import account_live_status

    account = _mk_account(
        db_session,
        default_store,
        maildir_path="/data/mailboxes/uibud2",
        initial_sync_completed_at=datetime.now(UTC),
    )

    ls = account_live_status(account)

    assert ls["pct"] is None
    assert ls["eta_label"] is None
    assert ls["budget_bytes"] == 2000 * 1024 * 1024  # google provider default
    assert ls["initial_sync"] is False
    assert ls["pause_reason"] is None
    assert ls["resume_rel"] is None


# ---------------------------------------------------------------------------
# Accounts table chips
# ---------------------------------------------------------------------------


def test_accounts_table_initial_sync_chip_with_pct(client, db_session, default_store):
    _login(client, db_session, default_store)
    account = _mk_account(db_session, default_store)
    _seed_progress(account)
    try:
        resp = client.get("/partials/accounts-table")
    finally:
        _clear_progress()

    assert resp.status_code == 200
    assert "Initial sync 38%" in resp.text
    assert "badge-info" in resp.text


def test_accounts_table_paused_chip_with_resume_and_tooltip(client, db_session, default_store):
    _login(client, db_session, default_store)
    _mk_account(
        db_session,
        default_store,
        sync_paused_until=datetime(2026, 6, 14, 14, 0, tzinfo=UTC),
        pause_reason="budget",
    )

    resp = client.get("/partials/accounts-table")

    assert resp.status_code == 200
    assert "Paused · resumes shortly" in resp.text
    assert 'title="Daily sync budget reached"' in resp.text
    # Self-recovering pause is NOT the red path.
    assert "badge-error" not in resp.text


def test_accounts_table_throttle_tooltip(client, db_session, default_store):
    _login(client, db_session, default_store)
    _mk_account(
        db_session,
        default_store,
        sync_paused_until=datetime(2026, 6, 14, 4, 0, tzinfo=UTC),
        pause_reason="throttle",
    )

    resp = client.get("/partials/accounts-table")

    assert 'title="Provider throttled"' in resp.text


def test_accounts_table_error_chip_unchanged(client, db_session, default_store):
    _login(client, db_session, default_store)
    _mk_account(
        db_session,
        default_store,
        sync_state=SyncState.error,
        last_error="boom",
        initial_sync_completed_at=datetime.now(UTC),
    )

    resp = client.get("/partials/accounts-table")

    assert '<span class="badge badge-error"><i data-lucide="alert-circle"' in resp.text


# ---------------------------------------------------------------------------
# Account detail: initial-sync panel + budget field
# ---------------------------------------------------------------------------


def test_detail_budget_field_google_placeholder(client, db_session, default_store):
    _login(client, db_session, default_store)
    account = _mk_account(db_session, default_store)

    resp = client.get(f"/accounts/{account.id}")

    assert resp.status_code == 200
    assert 'name="daily_sync_budget_mb"' in resp.text
    assert 'placeholder="2000 (provider default)"' in resp.text
    assert BUDGET_HELP in resp.text


def test_detail_budget_field_other_provider_placeholder(client, db_session, default_store):
    _login(client, db_session, default_store)
    account = _mk_account(db_session, default_store, provider="other", name="Plain IMAP")

    resp = client.get(f"/accounts/{account.id}")

    assert 'placeholder="Unlimited (provider default)"' in resp.text


def test_detail_paused_panel_shows_resume_and_override(client, db_session, default_store):
    _login(client, db_session, default_store)
    account = _mk_account(
        db_session,
        default_store,
        sync_paused_until=datetime(2026, 6, 14, 2, 0, tzinfo=UTC),
        pause_reason="budget",
    )
    _seed_progress(account)
    try:
        resp = client.get(f"/accounts/{account.id}")
    finally:
        _clear_progress()

    text = resp.text
    assert "Paused — daily budget" in text
    assert "Resumes shortly." in text
    # Initial-sync progress line survives the pause (last-known state).
    assert "38%" in text
    # Manual override stays available.
    assert "Sync now" in text


def test_detail_first_sync_shows_progress_and_priority_note(client, db_session, default_store):
    _login(client, db_session, default_store)
    account = _mk_account(
        db_session,
        default_store,
        sync_state=SyncState.syncing,
        initial_sync_total_messages=121_000,
    )
    _seed_progress(account)
    try:
        resp = client.get(f"/accounts/{account.id}")
    finally:
        _clear_progress()

    text = resp.text
    assert "46,200" in text and "121,000" in text  # messages line (| number)
    assert "ETA ≈ 3d" in text
    assert "today" in text  # bytes/budget line
    assert "INBOX first, then the archive" in text


# ---------------------------------------------------------------------------
# Budget override round-trips (form + service allowlist + API schema)
# ---------------------------------------------------------------------------


def test_edit_budget_roundtrip_n_zero_empty(client, db_session, default_store):
    _login(client, db_session, default_store)
    account = _mk_account(db_session, default_store)

    client.post(f"/accounts/{account.id}/edit", data={"daily_sync_budget_mb": "512"})
    db_session.refresh(account)
    assert account.daily_sync_budget_mb == 512

    client.post(f"/accounts/{account.id}/edit", data={"daily_sync_budget_mb": "0"})
    db_session.refresh(account)
    assert account.daily_sync_budget_mb == 0  # explicit unlimited

    client.post(f"/accounts/{account.id}/edit", data={"daily_sync_budget_mb": ""})
    db_session.refresh(account)
    assert account.daily_sync_budget_mb is None  # back to provider default


def test_api_patch_budget(client, db_session, default_store):
    _login(client, db_session, default_store)
    account = _mk_account(db_session, default_store)

    resp = client.patch(f"/api/accounts/{account.id}", json={"daily_sync_budget_mb": 256})

    assert resp.status_code == 200
    db_session.refresh(account)
    assert account.daily_sync_budget_mb == 256


# ---------------------------------------------------------------------------
# Dashboard: exclusions + initial-sync info line
# ---------------------------------------------------------------------------


def test_dashboard_excludes_paused_and_shows_initial_info(client, db_session, default_store):
    """A self-recovering pause never reads as an error (account-level
    pause_reason check); initial-syncing accounts surface as an INFO line
    with pct/ETA, not as an error with a Retry button."""
    _login(client, db_session, default_store)
    # Artificial worst case: error state WITH a pause set — the account-level
    # exclusion must keep it out of the error stat and the error list.
    _mk_account(
        db_session,
        default_store,
        name="PausedNotError",
        maildir_path="/data/mailboxes/uibud3",
        sync_state=SyncState.error,
        last_error="OVERQUOTA tail",
        sync_paused_until=datetime.now(UTC) + timedelta(hours=4),
        pause_reason="throttle",
        initial_sync_completed_at=datetime.now(UTC),
    )
    initial = _mk_account(db_session, default_store, name="Main gMail")
    _seed_progress(initial)
    try:
        resp = client.get("/")
    finally:
        _clear_progress()

    text = resp.text
    assert resp.status_code == 200
    # Initial-sync info line, compact, with pct + ETA.
    assert "initial sync 38%" in text
    assert "ETA ≈ 3d" in text
    # The paused account is not listed as a red error.
    assert "OVERQUOTA tail" not in text


def test_dashboard_true_error_still_counts(client, db_session, default_store):
    _login(client, db_session, default_store)
    _mk_account(
        db_session,
        default_store,
        name="ReallyBroken",
        sync_state=SyncState.error,
        last_error="AUTHENTICATIONFAILED",
        initial_sync_completed_at=datetime.now(UTC),
    )

    resp = client.get("/")

    assert "ReallyBroken" in resp.text
    assert "AUTHENTICATIONFAILED" in resp.text
