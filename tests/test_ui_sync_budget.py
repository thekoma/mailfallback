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
    assert ls["resume_hhmm"] == "02:00"
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
    assert ls["resume_hhmm"] is None


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
    assert "Paused · resumes 14:00" in resp.text
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
    assert "Resumes at 02:00" in text
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
    # Recap Messages item (| number); ETA + priority note in the summary.
    assert "46,200" in text and "121,000" in text
    assert "ETA ≈ 3d" in text
    assert "INBOX first, then the archive" in text
    # Pico-native progress, not the old reinvented bar.
    assert "<progress" in text
    assert "progress-bar-fill" not in text


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


# ---------------------------------------------------------------------------
# First-sync panel: Pico-native <progress> + recap, no dot grid (mockup)
# ---------------------------------------------------------------------------


def test_first_sync_panel_pico_progress_and_recap(client, db_session, default_store):
    """The first-sync hero matches the frozen mockup: a Pico native
    <progress> bar, the Folders/Messages/Downloaded recap card, the summary
    line — and NONE of the old custom progress divs or the dot grid."""
    _login(client, db_session, default_store)
    account = _mk_account(
        db_session,
        default_store,
        sync_state=SyncState.syncing,
        initial_sync_total_messages=169_403,
    )
    _seed_progress(account, pct=44.0, done=75_844, eta="≈ 2 days")
    from types import SimpleNamespace

    snap = SimpleNamespace(
        current_folder="SoTeHa/Inbox",
        folder_index=100,  # log parser: selectable boxes mbsync has opened
        folder_total_estimate=1,  # the broken first-sync estimate — must be ignored
        per_folder=[],
        phase="syncing",
    )
    from unittest.mock import patch

    from mailfallback.routers import ui_accounts

    real = ui_accounts._compute_hero_state

    def fake_state(acc, db):
        _state, _snap, last_job = real(acc, db)
        return "first-sync", snap, last_job

    try:
        with patch.object(ui_accounts, "_compute_hero_state", fake_state):
            resp = client.get(f"/accounts/{account.id}/partials/sync-panel")
    finally:
        _clear_progress()

    text = resp.text
    # Pico native progress (house pattern), value/max.
    assert "<progress" in text
    assert 'value="44"' in text and 'max="100"' in text
    # Recap card: three labelled items. Folders numerator = the log
    # parser's folder_index (boxes mbsync opened, same basis as the total);
    # no total set on this account -> bare count, no "/ N".
    assert "sync-recap" in text
    assert "Folders" in text and "100" in text
    assert "100 / " not in text
    assert "Messages" in text and "75,844" in text and "169,403" in text
    assert "Downloaded" in text  # done_bytes humanized
    # Summary line (44% bolded per the mockup) + ETA + priority note.
    assert "<strong>44%</strong>" in text
    assert "ETA ≈ 2 days · INBOX first, then the archive" in text
    # Liveness line: current folder + today's budget burn.
    assert "Downloading" in text and "SoTeHa/Inbox" in text
    assert "1.9 GB / 2.0 GB budget today" in text
    # The reinvented progress + dot grid are GONE.
    assert "progress-bar-fill" not in text
    assert "folder-chips" not in text
    assert "Folder 342 of" not in text  # the "Folder N of 1" bug string


def test_first_sync_panel_early_phase_indeterminate(client, db_session, default_store):
    """Before any folder data: bare indeterminate <progress> + phase label,
    no recap (nothing to show yet)."""
    _login(client, db_session, default_store)
    account = _mk_account(
        db_session,
        default_store,
        maildir_path="/data/mailboxes/uibud_early",
        sync_state=SyncState.syncing,
    )
    from types import SimpleNamespace
    from unittest.mock import patch

    from mailfallback.routers import ui_accounts

    snap = SimpleNamespace(
        current_folder=None,
        folder_index=0,
        folder_total_estimate=0,
        per_folder=[],
        phase="connecting",
    )
    real = ui_accounts._compute_hero_state

    def fake_state(acc, db):
        _s, _sn, lj = real(acc, db)
        return "first-sync", snap, lj

    # No sampler entry -> pct None; total None too.
    with patch.object(ui_accounts, "_compute_hero_state", fake_state):
        resp = client.get(f"/accounts/{account.id}/partials/sync-panel")

    text = resp.text
    assert "<progress></progress>" in text  # bare = indeterminate
    assert "Connecting" in text
    assert "progress-bar-fill" not in text


def test_account_live_status_forwards_done_bytes(db_session, default_store):
    """The recap 'Downloaded' value needs done_bytes — the helper must
    forward it from the sampler dict."""
    from mailfallback.routers.ui import account_live_status

    account = _mk_account(db_session, default_store, maildir_path="/data/mailboxes/uibud_db")
    _seed_progress(account, done=75_844)
    try:
        ls = account_live_status(account)
    finally:
        _clear_progress()
    assert ls["done_bytes"] == 75_844 * 1024


def test_first_sync_recap_folders_shows_total_when_known(client, db_session, default_store):
    """When the STATUS pass stored initial_sync_total_folders, the recap's
    Folders value is symmetric with Messages: 'X / Y' (muted denominator).
    Numerator = snap.folder_index (log parser), same basis as the total."""
    from types import SimpleNamespace
    from unittest.mock import patch

    from mailfallback.routers import ui_accounts

    _login(client, db_session, default_store)
    account = _mk_account(
        db_session,
        default_store,
        maildir_path="/data/mailboxes/uibud_ft",
        sync_state=SyncState.syncing,
        initial_sync_total_messages=169_403,
        initial_sync_total_folders=1_024,
    )
    _seed_progress(account, pct=44.0, done=75_844)
    snap = SimpleNamespace(
        current_folder="SoTeHa/Inbox",
        folder_index=100,  # log parser: boxes opened so far this walk
        folder_total_estimate=1,
        per_folder=[],
        phase="syncing",
    )
    real = ui_accounts._compute_hero_state

    def fake_state(acc, db):
        _s, _sn, lj = real(acc, db)
        return "first-sync", snap, lj

    try:
        with patch.object(ui_accounts, "_compute_hero_state", fake_state):
            resp = client.get(f"/accounts/{account.id}/partials/sync-panel")
    finally:
        _clear_progress()

    text = resp.text
    # Folders = log folder_index / STATUS total, symmetric with Messages.
    assert "100" in text and "1,024" in text
    # Two muted denominators: Folders "/ 1,024" and Messages "/ 169,403".
    assert text.count("sync-recap-muted") == 2


def test_first_sync_recap_folders_clamped_to_total(client, db_session, default_store):
    """Safety clamp: if the log folder_index somehow exceeds the STATUS
    total (transient over-count), the recap shows the total — never
    done > total / >100%."""
    from types import SimpleNamespace
    from unittest.mock import patch

    from mailfallback.routers import ui_accounts

    _login(client, db_session, default_store)
    account = _mk_account(
        db_session,
        default_store,
        maildir_path="/data/mailboxes/uibud_clamp",
        sync_state=SyncState.syncing,
        initial_sync_total_messages=169_403,
        initial_sync_total_folders=131,
    )
    _seed_progress(account, pct=99.0, done=160_000)
    snap = SimpleNamespace(
        current_folder="SoTeHa/Inbox",
        folder_index=222,  # > total (131) — must clamp
        folder_total_estimate=1,
        per_folder=[],
        phase="syncing",
    )
    real = ui_accounts._compute_hero_state

    def fake_state(acc, db):
        _s, _sn, lj = real(acc, db)
        return "first-sync", snap, lj

    try:
        with patch.object(ui_accounts, "_compute_hero_state", fake_state):
            resp = client.get(f"/accounts/{account.id}/partials/sync-panel")
    finally:
        _clear_progress()

    text = resp.text
    # Clamped: numerator shows the total (131), denominator "/ 131" — never
    # the raw 222 (the markup splits the two with the muted denominator span).
    assert '<span class="sync-recap-value">131 <span class="sync-recap-muted">/ 131</span>' in text
    assert "222" not in text


def test_account_live_status_forwards_total_folders(db_session, default_store):
    from mailfallback.routers.ui import account_live_status

    account = _mk_account(
        db_session,
        default_store,
        maildir_path="/data/mailboxes/uibud_tf",
        initial_sync_total_folders=512,
    )
    assert account_live_status(account)["total_folders"] == 512
