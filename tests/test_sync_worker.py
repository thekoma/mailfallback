# tests/test_sync_worker.py
import os
import threading
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from mailfallback.db import Base
from mailfallback.models import Account, JobStatus, MailStore, SyncJob, SyncState
from mailfallback.services import sync_worker
from mailfallback.services.sync_worker import execute_sync_job


def _make_engine(shared: bool = False):
    """In-memory engine; shared=True makes it usable from the sampler thread
    (StaticPool single connection, check_same_thread off)."""
    from sqlalchemy import event

    kwargs = {}
    if shared:
        kwargs = {
            "connect_args": {"check_same_thread": False},
            "poolclass": StaticPool,
        }
    engine = create_engine("sqlite:///:memory:", **kwargs)

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        # mail_index schema needs an attached DB on SQLite (no native schemas)
        cursor.execute("ATTACH DATABASE ':memory:' AS mail_index")
        cursor.close()

    Base.metadata.create_all(engine)
    return engine


def make_session():
    return sessionmaker(bind=_make_engine())()


def make_shared_session():
    """(session, factory) on ONE engine visible across threads — the sampler
    opens its own sessions on the same database as the test's."""
    engine = _make_engine(shared=True)
    factory = sessionmaker(bind=engine)
    return factory(), factory


@pytest.fixture(autouse=True)
def _isolated_sampler_sessions(monkeypatch):
    """execute_sync_job spawns a sampler thread that opens its OWN sessions
    via sync_worker.SessionLocal — point that at a throwaway in-memory DB so
    unit tests never reach for the real database_url (host 'db' does not
    exist here). Sampler-specific tests re-patch with a shared factory."""
    engine = _make_engine(shared=True)
    monkeypatch.setattr(sync_worker, "SessionLocal", sessionmaker(bind=engine))


def _make_store(session):
    store = MailStore(name="default", path="/data/mailboxes")
    session.add(store)
    session.commit()
    session.refresh(store)
    return store


def _make_account_and_job(session):
    store = _make_store(session)
    account = Account(
        name="Test",
        imap_host="imap.test.com",
        maildir_path="/tmp/test_maildir",
        credentials=None,
        store_id=store.id,
    )
    session.add(account)
    session.commit()
    job = SyncJob(account_id=account.id, source="test")
    session.add(job)
    session.commit()
    return account, job


def test_successful_sync():
    session = make_session()
    account, job = _make_account_and_job(session)

    mock_proc = MagicMock()
    mock_proc.stdout = iter(["synced ok\n"])
    mock_proc.returncode = 0
    with (
        patch("mailfallback.services.sync_worker.subprocess.Popen", return_value=mock_proc),
        patch("mailfallback.services.sync_worker.generate_mbsyncrc", return_value="config"),
    ):
        execute_sync_job(session, job.id)

    session.refresh(job)
    session.refresh(account)
    assert job.status == JobStatus.completed
    assert job.exit_code == 0
    assert job.completed_at is not None
    assert account.sync_state == SyncState.idle
    assert account.last_sync_at is not None


def test_failed_sync():
    session = make_session()
    account, job = _make_account_and_job(session)

    mock_proc = MagicMock()
    mock_proc.stdout = iter(["auth failed\n"])
    mock_proc.returncode = 1
    with (
        patch("mailfallback.services.sync_worker.subprocess.Popen", return_value=mock_proc),
        patch("mailfallback.services.sync_worker.generate_mbsyncrc", return_value="config"),
    ):
        execute_sync_job(session, job.id)

    session.refresh(job)
    session.refresh(account)
    assert job.status == JobStatus.failed
    assert job.exit_code == 1
    assert "auth failed" in job.log
    assert account.sync_state == SyncState.error
    assert account.last_error is not None


def test_sync_sets_running_state():
    session = make_session()
    account, job = _make_account_and_job(session)

    captured_state = {}

    def capture_state(*args, **kwargs):
        session.refresh(account)
        session.refresh(job)
        captured_state["account_state"] = account.sync_state
        captured_state["job_status"] = job.status
        mock_proc = MagicMock()
        mock_proc.stdout = iter(["ok\n"])
        mock_proc.returncode = 0
        return mock_proc

    with (
        patch("mailfallback.services.sync_worker.subprocess.Popen", side_effect=capture_state),
        patch("mailfallback.services.sync_worker.generate_mbsyncrc", return_value="config"),
    ):
        execute_sync_job(session, job.id)

    assert captured_state["account_state"] == SyncState.syncing
    assert captured_state["job_status"] == JobStatus.running


def test_submit_sync_job_uses_executor():
    """submit_sync_job delegates to the thread pool executor."""
    from mailfallback.services import sync_worker

    mock_executor = MagicMock()
    with (
        patch.object(sync_worker, "_sync_executor", mock_executor),
        patch.object(sync_worker, "get_sync_executor", return_value=mock_executor),
    ):
        sync_worker.submit_sync_job("fake-job-id")

    mock_executor.submit.assert_called_once()


def test_shutdown_sync_executor():
    """shutdown_sync_executor calls shutdown and resets the global."""
    from mailfallback.services import sync_worker

    mock_executor = MagicMock()
    sync_worker._sync_executor = mock_executor

    sync_worker.shutdown_sync_executor()

    mock_executor.shutdown.assert_called_once_with(wait=True)
    assert sync_worker._sync_executor is None


def test_shutdown_sync_executor_noop_when_none():
    """shutdown_sync_executor is a no-op when no executor exists."""
    from mailfallback.services import sync_worker

    sync_worker._sync_executor = None
    sync_worker.shutdown_sync_executor()  # should not raise
    assert sync_worker._sync_executor is None


def test_get_sync_executor_creates_pool():
    """get_sync_executor creates a ThreadPoolExecutor on first call."""
    from concurrent.futures import ThreadPoolExecutor

    from mailfallback.services import sync_worker

    # Ensure clean state
    sync_worker._sync_executor = None
    try:
        executor = sync_worker.get_sync_executor()
        assert isinstance(executor, ThreadPoolExecutor)
        assert executor._max_workers == 4
        # Calling again returns the same instance
        assert sync_worker.get_sync_executor() is executor
    finally:
        sync_worker.shutdown_sync_executor()


@patch("mailfallback.services.sync_worker.index_service")
@patch("mailfallback.services.sync_worker.subprocess.run")
def test_sync_worker_calls_index_service_after_success(
    mock_run, mock_index, db_session, default_store
):
    """A successful mbsync run triggers an index update."""
    from mailfallback.models import Account, AuthType, JobStatus, SyncJob
    from mailfallback.services import sync_worker

    acct = Account(
        name="a",
        store=default_store,
        maildir_path="/tmp/test_index_hook_a",
        imap_host="imap.example.com",
        imap_user="u",
        credentials=None,
        auth_type=AuthType.app_password,
    )
    db_session.add(acct)
    db_session.commit()
    acct_id = acct.id

    job = SyncJob(account_id=acct.id, source="manual", status=JobStatus.pending)
    db_session.add(job)
    db_session.commit()

    mock_run.return_value = type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    mock_proc = MagicMock()
    mock_proc.stdout = iter(["ok\n"])
    mock_proc.returncode = 0
    with (
        patch("mailfallback.services.sync_worker.subprocess.Popen", return_value=mock_proc),
        patch("mailfallback.services.sync_worker.generate_mbsyncrc", return_value="config"),
    ):
        sync_worker.execute_sync_job(db_session, job.id)

    mock_index.upsert_message_set.assert_called_once_with(db_session, acct_id)


@patch("mailfallback.services.sync_worker.index_service")
@patch("mailfallback.services.sync_worker.subprocess.run")
def test_sync_worker_index_failure_does_not_break_sync(
    mock_run, mock_index, db_session, default_store
):
    """If index_service raises, the sync still reports success."""
    from mailfallback.models import Account, AuthType, JobStatus, SyncJob
    from mailfallback.services import sync_worker

    acct = Account(
        name="b",
        store=default_store,
        maildir_path="/tmp/test_index_hook_b",
        imap_host="imap.example.com",
        imap_user="u",
        credentials=None,
        auth_type=AuthType.app_password,
    )
    db_session.add(acct)
    db_session.commit()

    job = SyncJob(account_id=acct.id, source="manual", status=JobStatus.pending)
    db_session.add(job)
    db_session.commit()

    mock_run.return_value = type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
    mock_index.upsert_message_set.side_effect = RuntimeError("indexer kaboom")

    mock_proc = MagicMock()
    mock_proc.stdout = iter(["ok\n"])
    mock_proc.returncode = 0
    with (
        patch("mailfallback.services.sync_worker.subprocess.Popen", return_value=mock_proc),
        patch("mailfallback.services.sync_worker.generate_mbsyncrc", return_value="config"),
    ):
        sync_worker.execute_sync_job(db_session, job.id)
    db_session.refresh(job)
    assert job.status == JobStatus.completed  # NOT failed


def test_sync_blocked_unauthenticated():
    db = MagicMock()
    job = MagicMock()
    job.id = "job-1"
    job.account_id = "acc-1"
    account = MagicMock()
    account.id = "acc-1"
    account.name = "Test"
    account.suspended = False
    account.migrating = False
    account.auth_type = MagicMock()
    account.auth_type.value = "oauth2"
    account.credentials = None
    account.is_authenticated = False
    account.owners = []
    db.query.return_value.filter.return_value.first.side_effect = [job, account]

    from mailfallback.services.sync_worker import execute_sync_job

    execute_sync_job(db, "job-1")

    assert job.log == "Sync blocked: account not authenticated"


# ---------------------------------------------------------------------------
# Byte meter / sampler thread + ledger (Task 4)
# ---------------------------------------------------------------------------


def _write(path, size, mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * size)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def _mk_maildir_account_and_job(session, tmp_path, **account_kw):
    store = _make_store(session)
    account = Account(
        name="Meter",
        imap_host="imap.test.com",
        maildir_path=str(tmp_path / "maildir"),
        store_id=store.id,
        **account_kw,
    )
    session.add(account)
    session.commit()
    job = SyncJob(account_id=account.id, source="test", status=JobStatus.running)
    session.add(job)
    session.commit()
    return account, job


def test_sample_maildir_counts_and_run_delta(tmp_path):
    """Cumulative totals count every message file under cur/new; the run
    delta counts only files written since run start (mtime — depends on
    CopyArrivalDate no). tmp/, dotfiles, dovecot metadata and nested
    .dovecot-home trees never count."""
    root = tmp_path / "maildir"
    old_ts = time.time() - 3600
    _write(str(root / "INBOX" / "cur" / "100.old:2,S"), 100, mtime=old_ts)
    _write(str(root / "INBOX" / "new" / "200.fresh"), 200)
    _write(str(root / "Sent" / "cur" / "300.fresh:2,S"), 300)
    # Never counted:
    _write(str(root / "INBOX" / "tmp" / "999.partial"), 999)
    _write(str(root / "INBOX" / "cur" / "dovecot.index.cache"), 50)
    _write(str(root / "INBOX" / "cur" / ".hidden"), 50)
    _write(str(root / "INBOX" / "dovecot-uidlist"), 50)  # folder root, not cur/new
    _write(str(root / ".dovecot-home" / "u" / "cur" / "1.msg"), 5000)

    since = time.time() - 60
    total_msgs, total_bytes, run_msgs, run_bytes = sync_worker._sample_maildir(str(root), since)

    assert (total_msgs, total_bytes) == (3, 600)
    assert (run_msgs, run_bytes) == (2, 500)


def test_sample_maildir_missing_path_is_zero(tmp_path):
    out = sync_worker._sample_maildir(str(tmp_path / "nope"), time.time())
    assert out == (0, 0, 0, 0)


def test_sampler_tick_advances_ledger_and_live_progress(tmp_path, monkeypatch):
    """Each tick books only the NEW run bytes into the daily ledger
    (max(0, run - last watermark)) with its OWN session, and publishes
    last-known progress (pct/eta from the STATUS denominator)."""
    session, factory = make_shared_session()
    monkeypatch.setattr(sync_worker, "SessionLocal", factory)
    account, job = _mk_maildir_account_and_job(session, tmp_path, initial_sync_total_messages=4)
    run_start = time.time() - 60
    _write(str(tmp_path / "maildir" / "INBOX" / "cur" / "1.msg"), 1000)

    state = sync_worker._new_sampler_state(run_start)
    sync_worker._sampler_tick(job.id, account.id, account.maildir_path, state)
    session.refresh(account)
    assert account.bytes_synced_today == 1000
    assert account.traffic_date == datetime.now(UTC).date()

    # Growth: only the delta is booked, not the cumulative run bytes again.
    _write(str(tmp_path / "maildir" / "INBOX" / "cur" / "2.msg"), 500)
    sync_worker._sampler_tick(job.id, account.id, account.maildir_path, state)
    session.refresh(account)
    assert account.bytes_synced_today == 1500

    prog = sync_worker._live_progress[job.id]
    assert prog["account_id"] == account.id
    assert prog["done_msgs"] == 2
    assert prog["done_bytes"] == 1500
    assert prog["bytes_today"] == 1500
    assert prog["pct"] == 50.0  # 2 of 4 (STATUS denominator)
    assert "eta" in prog and "rate_msgs_per_s" in prog
    # Idempotent without growth: watermark math books nothing.
    sync_worker._sampler_tick(job.id, account.id, account.maildir_path, state)
    session.refresh(account)
    assert account.bytes_synced_today == 1500
    sync_worker._live_progress.pop(job.id, None)


def test_sampler_tick_utc_rollover_resets_ledger(tmp_path, monkeypatch):
    """A tick on a new UTC day resets the ledger before booking — yesterday's
    spend never bleeds into today's budget."""
    session, factory = make_shared_session()
    monkeypatch.setattr(sync_worker, "SessionLocal", factory)
    account, job = _mk_maildir_account_and_job(session, tmp_path)
    account.traffic_date = (datetime.now(UTC) - timedelta(days=1)).date()
    account.bytes_synced_today = 999_999
    session.commit()
    run_start = time.time() - 60
    _write(str(tmp_path / "maildir" / "INBOX" / "cur" / "1.msg"), 700)

    state = sync_worker._new_sampler_state(run_start)
    sync_worker._sampler_tick(job.id, account.id, account.maildir_path, state)

    session.refresh(account)
    assert account.traffic_date == datetime.now(UTC).date()
    assert account.bytes_synced_today == 700  # reset, then today's delta only
    sync_worker._live_progress.pop(job.id, None)


def test_sampler_budget_crossing_stops_job_once(tmp_path, monkeypatch):
    """Crossing the daily budget records the marker for the worker and stops
    the job through the existing graceful-stop path — exactly once."""
    session, factory = make_shared_session()
    monkeypatch.setattr(sync_worker, "SessionLocal", factory)
    stops = []
    monkeypatch.setattr(sync_worker, "stop_sync_job", lambda job_id: stops.append(job_id))
    account, job = _mk_maildir_account_and_job(session, tmp_path, daily_sync_budget_mb=1)
    run_start = time.time() - 60
    _write(str(tmp_path / "maildir" / "INBOX" / "cur" / "big.msg"), 2 * 1024 * 1024)

    state = sync_worker._new_sampler_state(run_start)
    sync_worker._sampler_tick(job.id, account.id, account.maildir_path, state)

    assert job.id in sync_worker._budget_stops
    assert stops == [job.id]
    # The ledger row was committed BEFORE the stop (crash-safe).
    session.refresh(account)
    assert account.bytes_synced_today == 2 * 1024 * 1024
    # A second tick must not stop again (marker guard).
    sync_worker._sampler_tick(job.id, account.id, account.maildir_path, state)
    assert stops == [job.id]
    sync_worker._budget_stops.discard(job.id)
    sync_worker._live_progress.pop(job.id, None)


def test_worker_runs_sampler_with_own_sessions_and_keeps_progress(tmp_path, monkeypatch):
    """execute_sync_job starts the sampler thread around the subprocess: the
    thread opens sessions via sync_worker.SessionLocal ONLY (never the
    worker's session), the final flush books the run's bytes, and the
    last-known progress entry SURVIVES job end (the UI reads it briefly)."""
    session, factory = make_shared_session()
    factory_calls = []

    def counting_factory():
        factory_calls.append(threading.current_thread().name)
        return factory()

    monkeypatch.setattr(sync_worker, "SessionLocal", counting_factory)
    monkeypatch.setattr(sync_worker, "SAMPLE_INTERVAL", 0.01)
    account, job = _mk_maildir_account_and_job(session, tmp_path)
    job.status = JobStatus.pending
    session.commit()

    def slow_stdout():
        yield "syncing\n"
        # The "download" lands MID-RUN (mtime >= run start) — pre-run files
        # are deliberately not booked as run bytes.
        _write(str(tmp_path / "maildir" / "INBOX" / "cur" / "1.msg"), 1234)
        time.sleep(0.05)  # give the loop at least one interval tick
        yield "done\n"

    mock_proc = MagicMock()
    mock_proc.stdout = slow_stdout()
    mock_proc.returncode = 0
    with (
        patch("mailfallback.services.sync_worker.subprocess.Popen", return_value=mock_proc),
        patch("mailfallback.services.sync_worker.generate_mbsyncrc", return_value="config"),
    ):
        execute_sync_job(session, job.id)

    # Own-session discipline: every factory call came from the sampler thread.
    assert factory_calls, "sampler never opened a session"
    assert all(name.startswith("sync-sampler") for name in factory_calls)
    # Final flush booked the run bytes exactly once (watermark math).
    session.refresh(account)
    assert account.bytes_synced_today == 1234
    # Last-known progress survives job end...
    assert job.id in sync_worker._live_progress
    # ...and dies when the NEXT job for the same account starts.
    job2 = SyncJob(account_id=account.id, source="test")
    session.add(job2)
    session.commit()
    mock_proc2 = MagicMock()
    mock_proc2.stdout = iter(["ok\n"])
    mock_proc2.returncode = 0
    with (
        patch("mailfallback.services.sync_worker.subprocess.Popen", return_value=mock_proc2),
        patch("mailfallback.services.sync_worker.generate_mbsyncrc", return_value="config"),
    ):
        execute_sync_job(session, job2.id)
    assert job.id not in sync_worker._live_progress
    assert job2.id in sync_worker._live_progress
    sync_worker._live_progress.pop(job2.id, None)
