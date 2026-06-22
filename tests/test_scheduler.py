"""Tests for scheduler — focus on job registration."""

import os
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from mailfallback.models import MailStore, StagingArea, User
from mailfallback.services import staging_service
from mailfallback.services.scheduler import _run_staging_cleanup, scheduler, start_scheduler


def test_start_scheduler_registers_mount_cleanup(db_session):
    # Stop the scheduler if it's already running from a prior test
    if scheduler.running:
        scheduler.shutdown(wait=False)
    # Clear any pre-existing jobs
    for job in list(scheduler.get_jobs()):
        scheduler.remove_job(job.id)

    with (
        patch("mailfallback.services.scheduler.sync_scheduler_jobs"),
        patch("mailfallback.services.scheduler.backup_scheduler_jobs"),
    ):
        start_scheduler(db_session)

    job_ids = {j.id for j in scheduler.get_jobs()}
    assert "mount-cleanup" in job_ids

    # Cleanup
    if scheduler.running:
        scheduler.shutdown(wait=False)
    for job in list(scheduler.get_jobs()):
        scheduler.remove_job(job.id)


def test_start_scheduler_registers_staging_cleanup(db_session):
    # Stop the scheduler if it's already running from a prior test
    if scheduler.running:
        scheduler.shutdown(wait=False)
    # Clear any pre-existing jobs
    for job in list(scheduler.get_jobs()):
        scheduler.remove_job(job.id)

    with (
        patch("mailfallback.services.scheduler.sync_scheduler_jobs"),
        patch("mailfallback.services.scheduler.backup_scheduler_jobs"),
    ):
        start_scheduler(db_session)

    job_ids = {j.id for j in scheduler.get_jobs()}
    assert "staging-cleanup" in job_ids

    # Cleanup
    if scheduler.running:
        scheduler.shutdown(wait=False)
    for job in list(scheduler.get_jobs()):
        scheduler.remove_job(job.id)


def test_run_staging_cleanup_purges_expired_area(db_session, tmp_path):
    """The job function purges an expired area end-to-end: files and row."""
    store = MailStore(name="sched-staging", path=str(tmp_path / "store"))
    db_session.add(store)
    db_session.flush()
    user = User(username="sched-user", password_hash="x", store_id=store.id)
    db_session.add(user)
    db_session.flush()
    db_session.add(
        StagingArea(user_id=user.id, expires_at=datetime.now(UTC) - timedelta(minutes=1))
    )
    db_session.commit()

    sdir = staging_service.staging_dir(user)
    cur = os.path.join(sdir, "cur")
    os.makedirs(cur, exist_ok=True)
    with open(os.path.join(cur, "100.msg.host:2,S"), "w") as f:
        f.write("x")

    with patch("mailfallback.services.scheduler.SessionLocal", return_value=db_session):
        _run_staging_cleanup()

    assert db_session.query(StagingArea).count() == 0
    assert not os.path.isdir(sdir)


# ---------------------------------------------------------------------------
# Pause gating + expiry resume (sync-budget Task 7, spec §6)
# ---------------------------------------------------------------------------


def _mk_account(db_session, store, **kw):
    from mailfallback.models import Account

    account = Account(
        name=kw.pop("name", "Sched"),
        imap_host="imap.test.com",
        maildir_path=kw.pop("maildir_path", "/data/mailboxes/sched"),
        store_id=store.id,
        **kw,
    )
    db_session.add(account)
    db_session.commit()
    return account


def test_periodic_sync_skips_paused_account(db_session, default_store):
    """The periodic path must not enqueue while a self-recovering pause is
    live — ANY pause_reason (budget|throttle|transient) gates uniformly."""
    from mailfallback.models import SyncJob
    from mailfallback.services import scheduler as sched

    account = _mk_account(
        db_session,
        default_store,
        sync_paused_until=datetime.now(UTC) + timedelta(hours=4),
        pause_reason="throttle",
    )

    with (
        patch("mailfallback.services.scheduler.SessionLocal", return_value=db_session),
        patch("mailfallback.services.scheduler.submit_sync_job") as submit,
    ):
        sched._run_scheduled_sync(account.id)

    assert db_session.query(SyncJob).count() == 0
    submit.assert_not_called()


def test_periodic_sync_runs_when_pause_expired(db_session, default_store):
    """An EXPIRED pause does not gate the cron path (the expiry tick may not
    have cleared it yet — clearing is its job, not the gate's)."""
    from mailfallback.models import SyncJob
    from mailfallback.services import scheduler as sched

    account = _mk_account(
        db_session,
        default_store,
        maildir_path="/data/mailboxes/sched2",
        sync_paused_until=datetime.now(UTC) - timedelta(minutes=5),
        pause_reason="budget",
    )

    with (
        patch("mailfallback.services.scheduler.SessionLocal", return_value=db_session),
        patch("mailfallback.services.scheduler.submit_sync_job") as submit,
    ):
        sched._run_scheduled_sync(account.id)

    assert db_session.query(SyncJob).count() == 1
    submit.assert_called_once()


def test_pause_expiry_tick_enqueues_initial_incomplete_once(db_session, default_store):
    """An expired pause on an account still in the initial-sync regime is
    resumed IMMEDIATELY (the budget window just opened) — once: the columns
    clear on enqueue, so the next tick no-ops."""
    from mailfallback.models import SyncJob
    from mailfallback.services import scheduler as sched

    account = _mk_account(
        db_session,
        default_store,
        maildir_path="/data/mailboxes/sched3",
        sync_paused_until=datetime.now(UTC) - timedelta(minutes=1),
        pause_reason="budget",
    )
    account_id = account.id  # the tick closes the session — capture early

    with (
        patch("mailfallback.services.scheduler.SessionLocal", return_value=db_session),
        patch("mailfallback.services.scheduler.submit_sync_job") as submit,
    ):
        sched._run_pause_expiry_tick()
        first_count = db_session.query(SyncJob).count()
        sched._run_pause_expiry_tick()  # second tick must not enqueue again

    # The tick closes the session in finally — re-acquire, don't refresh.
    from mailfallback.models import Account

    account = db_session.get(Account, account_id)
    assert first_count == 1
    assert db_session.query(SyncJob).count() == 1
    assert submit.call_count == 1
    assert account.sync_paused_until is None
    assert account.pause_reason is None
    job = db_session.query(SyncJob).one()
    assert job.source == "scheduler"


def test_pause_expiry_tick_initial_complete_clears_without_enqueue(db_session, default_store):
    """Initial sync done: a routine incremental has no urgency — clear the
    columns, let the account's own cron resume it."""
    from mailfallback.models import SyncJob
    from mailfallback.services import scheduler as sched

    account = _mk_account(
        db_session,
        default_store,
        maildir_path="/data/mailboxes/sched4",
        sync_paused_until=datetime.now(UTC) - timedelta(minutes=1),
        pause_reason="throttle",
        initial_sync_completed_at=datetime.now(UTC) - timedelta(days=30),
    )

    with (
        patch("mailfallback.services.scheduler.SessionLocal", return_value=db_session),
        patch("mailfallback.services.scheduler.submit_sync_job") as submit,
    ):
        sched._run_pause_expiry_tick()

    account = db_session.get(type(account), account.id)
    assert account.sync_paused_until is None
    assert account.pause_reason is None
    assert db_session.query(SyncJob).count() == 0
    submit.assert_not_called()


def test_pause_expiry_tick_future_pause_untouched(db_session, default_store):
    from mailfallback.models import SyncJob
    from mailfallback.services import scheduler as sched

    account = _mk_account(
        db_session,
        default_store,
        maildir_path="/data/mailboxes/sched5",
        sync_paused_until=datetime.now(UTC) + timedelta(hours=2),
        pause_reason="budget",
    )

    with (
        patch("mailfallback.services.scheduler.SessionLocal", return_value=db_session),
        patch("mailfallback.services.scheduler.submit_sync_job") as submit,
    ):
        sched._run_pause_expiry_tick()

    account = db_session.get(type(account), account.id)
    assert account.pause_reason == "budget"
    assert account.sync_paused_until is not None
    assert db_session.query(SyncJob).count() == 0
    submit.assert_not_called()


def test_scheduled_sync_skips_needs_reauth(db_session, oauth_account, monkeypatch):
    """The periodic path must not enqueue an account parked in needs_reauth."""
    from mailfallback.models import SyncState
    from mailfallback.services import scheduler as sched

    oauth_account.sync_state = SyncState.needs_reauth
    db_session.commit()
    created = []
    monkeypatch.setattr(sched, "create_sync_job", lambda db, aid, source: created.append(aid))
    monkeypatch.setattr(sched, "SessionLocal", lambda: db_session)
    sched._run_scheduled_sync(oauth_account.id)
    assert created == []


def test_pause_expiry_tick_skips_needs_reauth(db_session, oauth_account, monkeypatch):
    """An expired pause must NOT resume an account that is needs_reauth."""
    from datetime import UTC, datetime, timedelta

    from mailfallback.models import SyncJob, SyncState
    from mailfallback.services import scheduler as sched

    oauth_account.sync_state = SyncState.needs_reauth
    oauth_account.sync_paused_until = datetime.now(UTC) - timedelta(minutes=1)
    oauth_account.pause_reason = "budget"
    db_session.commit()

    monkeypatch.setattr(sched, "SessionLocal", lambda: db_session)
    with patch("mailfallback.services.scheduler.submit_sync_job") as submit:
        sched._run_pause_expiry_tick()

    assert db_session.query(SyncJob).count() == 0
    submit.assert_not_called()


def test_start_scheduler_registers_pause_expiry(db_session):
    if scheduler.running:
        scheduler.shutdown(wait=False)
    for job in list(scheduler.get_jobs()):
        scheduler.remove_job(job.id)

    # Cleanup in finally: a failing assert must not leave the scheduler
    # running — refresh_scheduler() in later tests would dial the REAL
    # SessionLocal from the test thread (observed pollution).
    try:
        with (
            patch("mailfallback.services.scheduler.sync_scheduler_jobs"),
            patch("mailfallback.services.scheduler.backup_scheduler_jobs"),
        ):
            start_scheduler(db_session)

        job_ids = {j.id for j in scheduler.get_jobs()}
        assert "pause-expiry" in job_ids
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)
        for job in list(scheduler.get_jobs()):
            scheduler.remove_job(job.id)
