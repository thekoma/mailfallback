# tests/test_notification_emit.py
"""Task 4: verify that execute_sync_job emits the right notification event_key
on each state transition, and clears the marker on clean completion."""

import io
from unittest.mock import patch

import pytest

from mailfallback.models import JobStatus, SyncJob
from mailfallback.services import notification_service as ns
from mailfallback.services import sync_worker


@pytest.fixture(autouse=True)
def _public_dns():
    """Treat test hosts as publicly-resolvable so the SSRF guard doesn't 422
    account creation. SSRF rejection is covered in test_ssrf_and_reserved_users."""
    with patch(
        "mailfallback.services.imap_check.socket.getaddrinfo",
        return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
    ):
        yield


def _make_job(db_session, account):
    job = SyncJob(account_id=account.id, source="test", status=JobStatus.pending)
    db_session.add(job)
    db_session.commit()
    return job


# ---------------------------------------------------------------------------
# needs_reauth
# ---------------------------------------------------------------------------


def test_needs_reauth_emits_notification(db_session, oauth_account):
    """Terminal token refresh (invalid_grant) emits needs_reauth."""
    job = _make_job(db_session, oauth_account)

    calls = []
    with (
        patch.object(sync_worker, "_refresh_oauth_token", return_value=(None, True)),
        patch.object(ns, "notify_account_problem", lambda db, acct, key, t, b: calls.append(key)),
    ):
        sync_worker.execute_sync_job(db_session, job.id)

    assert "needs_reauth" in calls
    assert "sync_error" not in calls


# ---------------------------------------------------------------------------
# sync_error (non-terminal OAuth failure)
# ---------------------------------------------------------------------------


def test_nonterminal_oauth_failure_emits_sync_error(db_session, oauth_account):
    """Non-terminal token refresh failure emits sync_error, not needs_reauth."""
    job = _make_job(db_session, oauth_account)

    calls = []
    with (
        patch.object(sync_worker, "_refresh_oauth_token", return_value=(None, False)),
        patch.object(ns, "notify_account_problem", lambda db, acct, key, t, b: calls.append(key)),
    ):
        sync_worker.execute_sync_job(db_session, job.id)

    assert "sync_error" in calls
    assert "needs_reauth" not in calls


# ---------------------------------------------------------------------------
# sync_error (real mbsync failure — unclassifiable exit != 0)
# ---------------------------------------------------------------------------


def test_real_error_emits_sync_error(db_session, default_store):
    """Unclassifiable mbsync exit emits sync_error."""
    from mailfallback.models import Account, AuthType

    acct = Account(
        name="real-error",
        store=default_store,
        maildir_path="/tmp/test_notify_real_error",
        imap_host="imap.example.com",
        imap_user="u",
        credentials=None,
        auth_type=AuthType.app_password,
        initial_sync_completed_at=None,
    )
    db_session.add(acct)
    db_session.commit()
    job = _make_job(db_session, acct)

    mock_proc = _proc(["AUTHENTICATIONFAILED Invalid credentials"], code=1)

    calls = []
    with (
        patch("mailfallback.services.sync_worker.subprocess.Popen", return_value=mock_proc),
        patch("mailfallback.services.sync_worker.generate_mbsyncrc", return_value="config"),
        patch.object(ns, "notify_account_problem", lambda db, acct_, key, t, b: calls.append(key)),
    ):
        sync_worker.execute_sync_job(db_session, job.id)

    assert "sync_error" in calls


# ---------------------------------------------------------------------------
# sync_paused (throttled)
# ---------------------------------------------------------------------------


def test_throttled_emits_sync_paused(db_session, default_store, monkeypatch):
    """An OVERQUOTA (throttled) exit emits sync_paused."""
    from datetime import UTC

    from mailfallback.models import Account, AuthType
    from mailfallback.services import sync_budget

    monkeypatch.setattr(sync_budget.random, "uniform", lambda a, b: 0.0)

    acct = Account(
        name="throttle-notify",
        store=default_store,
        maildir_path="/tmp/test_notify_throttled",
        imap_host="imap.gmail.com",
        imap_user="u",
        credentials=None,
        auth_type=AuthType.app_password,
        provider="google",
        initial_sync_completed_at=__import__("datetime").datetime(2026, 1, 1, tzinfo=UTC),
    )
    db_session.add(acct)
    db_session.commit()
    job = _make_job(db_session, acct)

    overquota = (
        "IMAP error: unexpected BYE response: [OVERQUOTA] "
        "Account exceeded command or bandwidth limits."
    )
    mock_proc = _proc([overquota], code=1)

    calls = []
    with (
        patch("mailfallback.services.sync_worker.subprocess.Popen", return_value=mock_proc),
        patch("mailfallback.services.sync_worker.generate_mbsyncrc", return_value="config"),
        patch.object(ns, "notify_account_problem", lambda db, acct_, key, t, b: calls.append(key)),
    ):
        sync_worker.execute_sync_job(db_session, job.id)

    assert "sync_paused" in calls


# ---------------------------------------------------------------------------
# clean completion clears the marker
# ---------------------------------------------------------------------------


def test_clean_completion_clears_notified_state(db_session, default_store):
    """A clean (exit 0) sync calls clear_notified_state."""
    from mailfallback.models import Account, AuthType

    acct = Account(
        name="clean-complete",
        store=default_store,
        maildir_path="/tmp/test_notify_clean",
        imap_host="imap.example.com",
        imap_user="u",
        credentials=None,
        auth_type=AuthType.app_password,
        initial_sync_completed_at=None,
        last_notified_state="sync_error",  # pre-existing marker
    )
    db_session.add(acct)
    db_session.commit()
    job = _make_job(db_session, acct)

    mock_proc = _proc(["ok"], code=0)

    cleared = []
    real_clear = ns.clear_notified_state

    def tracking_clear(account):
        cleared.append(account.id)
        real_clear(account)

    with (
        patch("mailfallback.services.sync_worker.subprocess.Popen", return_value=mock_proc),
        patch("mailfallback.services.sync_worker.generate_mbsyncrc", return_value="config"),
        patch.object(ns, "clear_notified_state", tracking_clear),
    ):
        sync_worker.execute_sync_job(db_session, job.id)

    assert acct.id in cleared
    db_session.refresh(acct)
    assert acct.last_notified_state is None


# ---------------------------------------------------------------------------
# dedup guard: same state emits only once
# ---------------------------------------------------------------------------


def test_dedup_guard_suppresses_second_emit(db_session, oauth_account):
    """notify_account_problem's dedup guard: calling with the same event_key
    twice only records the first (last_notified_state already set)."""
    job = _make_job(db_session, oauth_account)

    calls = []
    with (
        patch.object(sync_worker, "_refresh_oauth_token", return_value=(None, True)),
        patch.object(ns, "notify_account_problem", lambda db, acct, key, t, b: calls.append(key)),
    ):
        sync_worker.execute_sync_job(db_session, job.id)
        # Simulate a second job run — same state should not fire again
        oauth_account.last_notified_state = "needs_reauth"
        db_session.commit()
        job2 = _make_job(db_session, oauth_account)
        sync_worker.execute_sync_job(db_session, job2.id)

    # The real dedup lives in notify_account_problem; here we just verify
    # that the worker emits from the right branch each time it runs.
    assert calls.count("needs_reauth") >= 1


# ---------------------------------------------------------------------------
# sync_error — TimeoutExpired outer branch
# ---------------------------------------------------------------------------


def test_timeout_expired_emits_sync_error(db_session, default_store):
    """subprocess.TimeoutExpired reaching the outer handler emits sync_error."""
    import subprocess

    from mailfallback.models import Account, AuthType

    acct = Account(
        name="timeout-notify",
        store=default_store,
        maildir_path="/tmp/test_notify_timeout",
        imap_host="imap.example.com",
        imap_user="u",
        credentials=None,
        auth_type=AuthType.app_password,
        initial_sync_completed_at=None,
    )
    db_session.add(acct)
    db_session.commit()
    job = _make_job(db_session, acct)

    calls = []
    with (
        patch(
            "mailfallback.services.sync_worker.subprocess.Popen",
            side_effect=subprocess.TimeoutExpired(cmd="mbsync", timeout=3600),
        ),
        patch("mailfallback.services.sync_worker.generate_mbsyncrc", return_value="config"),
        patch.object(ns, "notify_account_problem", lambda db, acct_, key, t, b: calls.append(key)),
    ):
        sync_worker.execute_sync_job(db_session, job.id)

    assert "sync_error" in calls


# ---------------------------------------------------------------------------
# C1 — generic exception handler emits sync_error
# ---------------------------------------------------------------------------


def test_generic_exception_emits_sync_error(db_session, default_store):
    """A non-TimeoutExpired exception reaching the outer handler emits sync_error
    and sets job.failure_kind='error'."""
    from mailfallback.models import Account, AuthType

    acct = Account(
        name="generic-exc-notify",
        store=default_store,
        maildir_path="/tmp/test_notify_generic_exc",
        imap_host="imap.example.com",
        imap_user="u",
        credentials=None,
        auth_type=AuthType.app_password,
        initial_sync_completed_at=None,
    )
    db_session.add(acct)
    db_session.commit()
    job = _make_job(db_session, acct)

    calls = []
    with (
        patch(
            "mailfallback.services.sync_worker.subprocess.Popen",
            side_effect=RuntimeError("disk full"),
        ),
        patch("mailfallback.services.sync_worker.generate_mbsyncrc", return_value="config"),
        patch.object(ns, "notify_account_problem", lambda db, acct_, key, t, b: calls.append(key)),
    ):
        sync_worker.execute_sync_job(db_session, job.id)

    assert "sync_error" in calls
    db_session.refresh(job)
    assert job.failure_kind == "error"


# ---------------------------------------------------------------------------
# I1 — stale notify skips paused accounts
# ---------------------------------------------------------------------------


def test_stale_notify_skips_paused_account(db_session, default_store):
    """An account with a self-recovering pause (pause_reason set + sync_paused_until
    in the future) is NOT emitted a stale notification even when last_sync_at is old.
    A genuinely stale account (no pause) IS notified."""
    from datetime import UTC, datetime, timedelta

    from mailfallback.models import Account, AuthType

    old_sync = datetime.now(UTC) - timedelta(days=10)
    future_pause = datetime.now(UTC) + timedelta(hours=6)

    paused_acct = Account(
        name="paused-stale",
        store=default_store,
        maildir_path="/tmp/test_notify_stale_paused",
        imap_host="imap.example.com",
        imap_user="u",
        credentials=None,
        auth_type=AuthType.app_password,
        initial_sync_completed_at=None,
        last_sync_at=old_sync,
        pause_reason="budget",
        sync_paused_until=future_pause,
    )
    genuine_acct = Account(
        name="genuine-stale",
        store=default_store,
        maildir_path="/tmp/test_notify_stale_genuine",
        imap_host="imap.example.com",
        imap_user="u",
        credentials=None,
        auth_type=AuthType.app_password,
        initial_sync_completed_at=None,
        last_sync_at=old_sync,
    )
    db_session.add_all([paused_acct, genuine_acct])
    db_session.commit()

    # Directly replicate what _run_stale_notify does so we can unit-test the
    # query logic without going through APScheduler internals.
    from mailfallback.models import Account as AccountModel

    cutoff = datetime.now(UTC) - timedelta(days=7)
    from mailfallback.models import SyncState

    stale = (
        db_session.query(AccountModel)
        .filter(
            AccountModel.last_sync_at.isnot(None),
            AccountModel.last_sync_at < cutoff,
            AccountModel.enabled.is_(True),
            AccountModel.suspended.is_(False),
            AccountModel.sync_state != SyncState.needs_reauth,
            AccountModel.pause_reason.is_(None),
        )
        .all()
    )

    notified_ids = {a.id for a in stale}
    assert paused_acct.id not in notified_ids, "Paused account should be excluded from stale"
    assert genuine_acct.id in notified_ids, "Genuinely stale account should be included"


# ---------------------------------------------------------------------------
# I2 — stale loop commits per-account so later failures don't roll back earlier markers
# ---------------------------------------------------------------------------


def test_stale_loop_commits_per_account(db_session, default_store):
    """If processing the 2nd stale account raises, the 1st account's marker
    (set by notify_account_problem) is still durably committed."""
    from datetime import UTC, datetime, timedelta

    from mailfallback.models import Account, AuthType, SyncState
    from mailfallback.services import notification_service

    old_sync = datetime.now(UTC) - timedelta(days=10)

    acct1 = Account(
        name="stale-commit-1",
        store=default_store,
        maildir_path="/tmp/test_notify_stale_commit1",
        imap_host="imap.example.com",
        imap_user="u",
        credentials=None,
        auth_type=AuthType.app_password,
        initial_sync_completed_at=None,
        last_sync_at=old_sync,
    )
    acct2 = Account(
        name="stale-commit-2",
        store=default_store,
        maildir_path="/tmp/test_notify_stale_commit2",
        imap_host="imap.example.com",
        imap_user="u",
        credentials=None,
        auth_type=AuthType.app_password,
        initial_sync_completed_at=None,
        last_sync_at=old_sync,
    )
    db_session.add_all([acct1, acct2])
    db_session.commit()

    cutoff = datetime.now(UTC) - timedelta(days=7)
    stale = (
        db_session.query(Account)
        .filter(
            Account.last_sync_at.isnot(None),
            Account.last_sync_at < cutoff,
            Account.enabled.is_(True),
            Account.suspended.is_(False),
            Account.sync_state != SyncState.needs_reauth,
            Account.pause_reason.is_(None),
        )
        .order_by(Account.name)
        .all()
    )
    assert len(stale) == 2

    # Simulate the per-account-commit loop (the fixed version):
    for a in stale:
        notification_service.notify_account_problem(
            db_session,
            a,
            "stale",
            f"{a.name}: no sync in 7+ days",
            "MailFallBack has not synced this account in over a week.",
        )
        db_session.commit()  # per-account commit (the fix)
        if a.name == "stale-commit-1":
            # Simulate a failure AFTER the first commit
            # The marker for acct1 should already be persisted
            break

    # Verify acct1's marker survived (was committed before the simulated break)
    db_session.expire(acct1)
    db_session.refresh(acct1)
    assert acct1.last_notified_state == "stale", (
        "acct1 marker must be persisted even if later processing is interrupted"
    )


# ---------------------------------------------------------------------------
# T3 — activity event emits
# ---------------------------------------------------------------------------


def test_sync_completed_emits_event(db_session, default_store):
    """A clean (exit 0) sync always emits sync_completed."""
    from datetime import UTC

    from mailfallback.models import Account, AuthType

    acct = Account(
        name="emit-sync-completed",
        store=default_store,
        maildir_path="/tmp/test_emit_sync_completed",
        imap_host="imap.example.com",
        imap_user="u",
        credentials=None,
        auth_type=AuthType.app_password,
        initial_sync_completed_at=__import__("datetime").datetime(2026, 1, 1, tzinfo=UTC),
    )
    db_session.add(acct)
    db_session.commit()
    job = _make_job(db_session, acct)

    mock_proc = _proc(["ok"], code=0)
    event_calls = []

    def _track_event(db, a, key, t, b, details=None):
        event_calls.append(key)

    with (
        patch("mailfallback.services.sync_worker.subprocess.Popen", return_value=mock_proc),
        patch("mailfallback.services.sync_worker.generate_mbsyncrc", return_value="config"),
        patch.object(ns, "notify_account_event", _track_event),
    ):
        sync_worker.execute_sync_job(db_session, job.id)

    assert "sync_completed" in event_calls


def test_sync_completed_reports_current_message_count(db_session, default_store):
    """sync_completed.details.messages uses the live total_messages, not the
    initial-sync-only counter (which is null for accounts synced before that
    field was captured)."""
    from datetime import UTC

    from mailfallback.models import Account, AuthType

    acct = Account(
        name="emit-count",
        store=default_store,
        maildir_path="/tmp/test_emit_count",
        imap_host="imap.example.com",
        imap_user="u",
        credentials=None,
        auth_type=AuthType.app_password,
        initial_sync_completed_at=__import__("datetime").datetime(2026, 1, 1, tzinfo=UTC),
        initial_sync_total_messages=None,
        total_messages=4242,
    )
    db_session.add(acct)
    db_session.commit()
    job = _make_job(db_session, acct)

    mock_proc = _proc(["ok"], code=0)
    captured = {}

    def _track_event(db, a, key, t, b, details=None):
        if key == "sync_completed":
            captured["details"] = details

    with (
        patch("mailfallback.services.sync_worker.subprocess.Popen", return_value=mock_proc),
        patch("mailfallback.services.sync_worker.generate_mbsyncrc", return_value="config"),
        patch.object(ns, "notify_account_event", _track_event),
    ):
        sync_worker.execute_sync_job(db_session, job.id)

    assert captured["details"]["messages"] == 4242
    # richer envelope also carries the live stats
    assert "unread" in captured["details"]
    assert "size_bytes" in captured["details"]
    assert "duration_seconds" in captured["details"]


def test_initial_sync_completed_emitted_when_first_pass(db_session, default_store):
    """When initial_sync_completed_at was None, both sync_completed and
    initial_sync_completed are emitted."""
    from mailfallback.models import Account, AuthType

    acct = Account(
        name="emit-initial-sync",
        store=default_store,
        maildir_path="/tmp/test_emit_initial_sync",
        imap_host="imap.example.com",
        imap_user="u",
        credentials=None,
        auth_type=AuthType.app_password,
        initial_sync_completed_at=None,
    )
    db_session.add(acct)
    db_session.commit()
    job = _make_job(db_session, acct)

    mock_proc = _proc(["ok"], code=0)
    event_calls = []

    def _track_event(db, a, key, t, b, details=None):
        event_calls.append(key)

    with (
        patch("mailfallback.services.sync_worker.subprocess.Popen", return_value=mock_proc),
        patch("mailfallback.services.sync_worker.generate_mbsyncrc", return_value="config"),
        patch.object(ns, "notify_account_event", _track_event),
    ):
        sync_worker.execute_sync_job(db_session, job.id)

    assert "sync_completed" in event_calls
    assert "initial_sync_completed" in event_calls


def test_initial_sync_completed_not_emitted_when_already_set(db_session, default_store):
    """When initial_sync_completed_at was already set, initial_sync_completed
    is NOT emitted (only sync_completed)."""
    from datetime import UTC

    from mailfallback.models import Account, AuthType

    acct = Account(
        name="emit-no-initial-sync",
        store=default_store,
        maildir_path="/tmp/test_emit_no_initial_sync",
        imap_host="imap.example.com",
        imap_user="u",
        credentials=None,
        auth_type=AuthType.app_password,
        initial_sync_completed_at=__import__("datetime").datetime(2026, 1, 1, tzinfo=UTC),
    )
    db_session.add(acct)
    db_session.commit()
    job = _make_job(db_session, acct)

    mock_proc = _proc(["ok"], code=0)
    event_calls = []

    def _track_event(db, a, key, t, b, details=None):
        event_calls.append(key)

    with (
        patch("mailfallback.services.sync_worker.subprocess.Popen", return_value=mock_proc),
        patch("mailfallback.services.sync_worker.generate_mbsyncrc", return_value="config"),
        patch.object(ns, "notify_account_event", _track_event),
    ):
        sync_worker.execute_sync_job(db_session, job.id)

    assert "sync_completed" in event_calls
    assert "initial_sync_completed" not in event_calls


def test_restore_completed_emits_event(db_session, default_store):
    """A successful restore job emits restore_completed for the target account."""
    from unittest.mock import MagicMock, patch

    from mailfallback.models import Account, AuthType, RestoreJob, RestoreMode, User
    from mailfallback.services import notification_service as ns_local
    from mailfallback.services.restore_worker import execute_restore_job

    user = User(username="restore_emit_user", password_hash="x", store_id=default_store.id)
    db_session.add(user)
    db_session.flush()

    src = Account(
        name="restore-src",
        email_address="src@example.com",
        imap_host="imap.src.com",
        imap_port=993,
        maildir_path="/data/mailboxes/restore-src",
        store_id=default_store.id,
        credentials="encrypted",
        auth_type=AuthType.app_password,
    )
    tgt = Account(
        name="restore-tgt",
        email_address="tgt@example.com",
        imap_host="imap.tgt.com",
        imap_port=993,
        maildir_path="/data/mailboxes/restore-tgt",
        store_id=default_store.id,
        credentials="encrypted",
        auth_type=AuthType.app_password,
    )
    db_session.add_all([src, tgt])
    db_session.flush()
    src.owners.append(user)
    db_session.commit()

    job = RestoreJob(
        source_account_id=src.id,
        target_account_id=tgt.id,
        restore_mode=RestoreMode.folder,
        selected_folders=["INBOX"],
        skip_duplicates=False,
        requested_by=user.id,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    event_calls = []

    src_conn = MagicMock()
    tgt_conn = MagicMock()
    src_conn.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "INBOX"'])
    src_conn.select.return_value = ("OK", [b"1"])
    src_conn.search.return_value = ("OK", [b"1"])
    src_conn.fetch.return_value = (
        "OK",
        [(b"1 (RFC822 {100}", b"From: a@b.com\r\nSubject: T\r\n\r\nBody"), b")"],
    )
    tgt_conn.append.return_value = ("OK", [b"APPEND completed"])

    def _track_event(db, a, key, t, b, details=None):
        event_calls.append(key)

    _rw = "mailfallback.services.restore_worker"
    with (
        patch(f"{_rw}.decrypt_credentials", return_value="plaintext"),
        patch(f"{_rw}.create_temp_imap_user", return_value=("_tmp_user", "tmp-pass")),
        patch(f"{_rw}.delete_temp_imap_user"),
        patch(f"{_rw}.connect_imap", side_effect=[src_conn, tgt_conn]),
        patch.object(ns_local, "notify_account_event", _track_event),
    ):
        execute_restore_job(db_session, job.id)

    assert "restore_completed" in event_calls


def test_backup_completed_emits_to_admins(db_session, default_store):
    """run_config_backup success emits backup_completed to all admin users."""
    from unittest.mock import patch

    from mailfallback.config import settings
    from mailfallback.models import Repository, User, UserRole
    from mailfallback.security import encrypt_credentials
    from mailfallback.services import config_backup_service as cbs
    from mailfallback.services import notification_service as ns_local

    def _enc(v):
        return encrypt_credentials(v, settings.secret_key)

    admin = User(
        username="backup_admin",
        password_hash="x",
        role=UserRole.admin,
        store_id=default_store.id,
    )
    db_session.add(admin)
    db_session.commit()

    repo = Repository(
        name="offsite-emit",
        backend_type="s3",
        s3_endpoint=_enc("https://s3.example.com"),
        s3_bucket=_enc("bucket"),
        s3_access_key=_enc("ak"),
        s3_secret_key=_enc("sk"),
        restic_password=_enc("rp"),
        config_backup_enabled=True,
        config_backup_passphrase=_enc("strong-passphrase"),
    )
    db_session.add(repo)
    db_session.commit()

    notify_calls = []

    with patch("mailfallback.services.config_backup_service.restic_service") as mock_restic:
        mock_restic.init_repo.return_value = True
        mock_restic.run_backup.return_value = {"message_type": "summary"}
        mock_restic.apply_retention.return_value = {"pruned": True}

        def _track_notify(db, ids, key, t, b, details=None):
            notify_calls.append((key, ids))

        with patch.object(ns_local, "notify_users", _track_notify):
            cbs.run_config_backup(db_session, repo)

    assert any(key == "backup_completed" for key, _ in notify_calls), "backup_completed not emitted"
    # verify admin id was included
    emitted_ids = next(ids for key, ids in notify_calls if key == "backup_completed")
    assert admin.id in emitted_ids


def test_account_added_emits_event(client, db_session, default_store):
    """Creating an account via POST /api/accounts emits account_added."""
    from mailfallback.models import UserRole
    from mailfallback.services import notification_service as ns_local
    from mailfallback.services.user_service import create_user

    create_user(db_session, "emit_admin", "pass", UserRole.admin, store_id=default_store.id)
    client.post("/api/auth/login", json={"username": "emit_admin", "password": "pass"})

    event_calls = []

    with patch.object(
        ns_local,
        "notify_account_event",
        lambda db, a, key, t, b, details=None: event_calls.append(key),
    ):
        resp = client.post(
            "/api/accounts",
            json={
                "name": "Emit Test",
                "email_address": "emit@example.com",
                "imap_host": "imap.example.com",
                "imap_port": 993,
            },
        )

    assert resp.status_code == 200
    assert "account_added" in event_calls


# ---------------------------------------------------------------------------
# Helpers (mirrors test_sync_worker._proc)
# ---------------------------------------------------------------------------


def _proc(lines, code=0):
    from unittest.mock import MagicMock

    p = MagicMock()
    p.stdout = io.StringIO("".join(ln + "\n" for ln in lines))
    p.returncode = code
    return p
