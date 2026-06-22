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
    exist here). Sampler-specific tests re-patch with a shared factory.

    The worker also runs an upstream STATUS pass for initial-sync accounts
    (non-fatal by contract) — fail it by default so the unit suite stays
    OFFLINE-deterministic; STATUS tests override via `with patch(...)`."""
    engine = _make_engine(shared=True)
    monkeypatch.setattr(sync_worker, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(
        "mailfallback.services.imap_check.connect_imap",
        MagicMock(side_effect=OSError("offline unit tests")),
    )


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


def test_sampler_tick_stamps_updated_ts(tmp_path, monkeypatch):
    """Each _sampler_tick populates _live_progress with updated_ts wall-clock
    timestamp for watchdog detection of stalled syncs."""
    session, factory = make_shared_session()
    monkeypatch.setattr(sync_worker, "SessionLocal", factory)
    account, job = _mk_maildir_account_and_job(session, tmp_path)
    run_start = time.time() - 60
    _write(str(tmp_path / "maildir" / "INBOX" / "cur" / "1.msg"), 1000)

    state = sync_worker._new_sampler_state(run_start)
    sync_worker._sampler_tick(job.id, account.id, account.maildir_path, state)
    prog = sync_worker.get_live_progress(job.id)
    assert prog is not None and "updated_ts" in prog and prog["updated_ts"] > 0
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


# ---------------------------------------------------------------------------
# Throttle-aware worker: priority pass, STATUS totals, pause columns (Task 5)
# ---------------------------------------------------------------------------


def _proc(lines, code=0):
    p = MagicMock()
    p.stdout = iter([ln + "\n" for ln in lines])
    p.returncode = code
    return p


def _zero_jitter(monkeypatch):
    from mailfallback.services import sync_budget

    monkeypatch.setattr(sync_budget.random, "uniform", lambda a, b: 0.0)


PATCH_RC = patch("mailfallback.services.sync_worker.generate_mbsyncrc", return_value="config")
DONE = datetime(2026, 1, 1, tzinfo=UTC)  # any past initial-sync completion


def test_initial_sync_runs_priority_pass_then_full(tmp_path):
    """Initial sync incomplete: invocation 1 = <channel>:INBOX, invocation 2 =
    full channel; one job row, both outputs logged with a marker; the clean
    FULL exit completes the initial sync."""
    session = make_session()
    account, job = _mk_maildir_account_and_job(session, tmp_path)
    job.status = JobStatus.pending
    session.commit()

    cmds = []

    def fake_popen(cmd, **kw):
        cmds.append(cmd)
        return _proc([f"pass {len(cmds)}"])

    with (
        patch("mailfallback.services.sync_worker.subprocess.Popen", side_effect=fake_popen),
        PATCH_RC,
        patch(
            "mailfallback.services.imap_check.connect_imap",
            side_effect=OSError("no upstream in tests"),
        ),
    ):
        execute_sync_job(session, job.id)

    assert len(cmds) == 2
    assert cmds[0][-1] == "meter:INBOX"
    assert cmds[1][-1] == "meter"
    session.refresh(job)
    session.refresh(account)
    assert job.status == JobStatus.completed
    assert "pass 1" in job.log and "pass 2" in job.log
    assert "invocation 2/2" in job.log  # the marker line
    assert account.initial_sync_completed_at is not None
    assert account.sync_paused_until is None and account.pause_reason is None


def test_completed_initial_sync_runs_single_full_pass(tmp_path):
    session = make_session()
    account, job = _mk_maildir_account_and_job(session, tmp_path, initial_sync_completed_at=DONE)
    job.status = JobStatus.pending
    session.commit()

    cmds = []

    def fake_popen(cmd, **kw):
        cmds.append(cmd)
        return _proc(["ok"])

    with (
        patch("mailfallback.services.sync_worker.subprocess.Popen", side_effect=fake_popen),
        PATCH_RC,
    ):
        execute_sync_job(session, job.id)

    assert len(cmds) == 1
    assert cmds[0][-1] == "-a"
    session.refresh(account)
    # The completion timestamp is NOT rewritten (SQLite hands it back naive).
    assert account.initial_sync_completed_at == DONE.replace(tzinfo=None)


def test_inbox_pass_failure_skips_full_pass(tmp_path):
    session = make_session()
    account, job = _mk_maildir_account_and_job(session, tmp_path)
    job.status = JobStatus.pending
    session.commit()

    cmds = []

    def fake_popen(cmd, **kw):
        cmds.append(cmd)
        return _proc(["AUTHENTICATIONFAILED junk"], code=1)

    with (
        patch("mailfallback.services.sync_worker.subprocess.Popen", side_effect=fake_popen),
        PATCH_RC,
        patch(
            "mailfallback.services.imap_check.connect_imap",
            side_effect=OSError("no upstream in tests"),
        ),
    ):
        execute_sync_job(session, job.id)

    assert len(cmds) == 1  # the full pass never started
    session.refresh(job)
    session.refresh(account)
    assert job.status == JobStatus.failed
    assert job.failure_kind == "error"
    assert account.sync_state == SyncState.error
    assert account.initial_sync_completed_at is None


def test_throttled_exit_pauses_idle_not_error(tmp_path, monkeypatch):
    """The production OVERQUOTA line: self-recovering pause, NOT the red
    error path — state idle, last_error cleared, 4h backoff (attempt 1)."""
    _zero_jitter(monkeypatch)
    session = make_session()
    account, job = _mk_maildir_account_and_job(
        session, tmp_path, provider="google", initial_sync_completed_at=DONE
    )
    job.status = JobStatus.pending
    session.commit()

    overquota = (
        "IMAP error: unexpected BYE response: [OVERQUOTA] "
        "Account exceeded command or bandwidth limits."
    )
    with (
        patch(
            "mailfallback.services.sync_worker.subprocess.Popen",
            return_value=_proc([overquota], code=1),
        ),
        PATCH_RC,
    ):
        execute_sync_job(session, job.id)

    session.refresh(job)
    session.refresh(account)
    assert job.status == JobStatus.failed
    assert job.failure_kind == "throttled"
    assert account.sync_state == SyncState.idle
    assert account.last_error is None
    assert account.pause_reason == "throttle"
    delta = account.sync_paused_until - job.completed_at
    assert abs(delta.total_seconds() - 4 * 3600) < 60


def test_transient_exit_short_backoff(tmp_path, monkeypatch):
    _zero_jitter(monkeypatch)
    session = make_session()
    account, job = _mk_maildir_account_and_job(session, tmp_path, initial_sync_completed_at=DONE)
    job.status = JobStatus.pending
    session.commit()

    with (
        patch(
            "mailfallback.services.sync_worker.subprocess.Popen",
            return_value=_proc(["socket: unexpected EOF"], code=1),
        ),
        PATCH_RC,
    ):
        execute_sync_job(session, job.id)

    session.refresh(job)
    session.refresh(account)
    assert job.failure_kind == "transient"
    assert account.pause_reason == "transient"
    assert account.sync_state == SyncState.idle
    delta = account.sync_paused_until - job.completed_at
    assert abs(delta.total_seconds() - 120) < 30


def test_attempt_count_escalates_backoff(tmp_path, monkeypatch):
    """A second throttle TODAY doubles the backoff (4h -> 8h)."""
    _zero_jitter(monkeypatch)
    session = make_session()
    account, job = _mk_maildir_account_and_job(
        session, tmp_path, provider="google", initial_sync_completed_at=DONE
    )
    prior = SyncJob(
        account_id=account.id,
        source="test",
        status=JobStatus.failed,
        failure_kind="throttled",
        started_at=datetime.now(UTC),
    )
    session.add(prior)
    job.status = JobStatus.pending
    session.commit()

    with (
        patch(
            "mailfallback.services.sync_worker.subprocess.Popen",
            return_value=_proc(["BYE [OVERQUOTA] limits"], code=1),
        ),
        PATCH_RC,
    ):
        execute_sync_job(session, job.id)

    session.refresh(job)
    session.refresh(account)
    assert job.failure_kind == "throttled"
    delta = account.sync_paused_until - job.completed_at
    assert abs(delta.total_seconds() - 8 * 3600) < 60


def test_budget_stop_wins_over_signal(tmp_path, monkeypatch):
    """A budget stop SIGTERMs the proc — the marker must beat the signal
    interpretation: budget_paused, resume at next UTC midnight, marker
    consumed."""
    _zero_jitter(monkeypatch)
    session = make_session()
    account, job = _mk_maildir_account_and_job(session, tmp_path, initial_sync_completed_at=DONE)
    job.status = JobStatus.pending
    session.commit()

    def fake_popen(cmd, **kw):
        # Emulate the sampler's mid-run stop: marker + SIGTERM bookkeeping.
        sync_worker._budget_stops.add(job.id)
        sync_worker._killed_signals[job.id] = "SIGTERM"
        return _proc(["killed mid-fetch"], code=-15)

    with (
        patch("mailfallback.services.sync_worker.subprocess.Popen", side_effect=fake_popen),
        PATCH_RC,
    ):
        execute_sync_job(session, job.id)

    session.refresh(job)
    session.refresh(account)
    assert job.failure_kind == "budget_paused"
    assert job.signal == "SIGTERM"
    assert account.pause_reason == "budget"
    assert account.sync_state == SyncState.idle
    assert account.last_error is None
    assert job.id not in sync_worker._budget_stops  # consumed
    resume = account.sync_paused_until
    assert (resume.hour, resume.minute) == (0, 0)  # next UTC midnight (no jitter)
    assert resume.date() > job.completed_at.date()


def test_user_stop_keeps_error_behavior_and_clears_stale_pause(tmp_path):
    """User stop: today's error behavior for job/state, but a PRE-EXISTING
    pause clears (review F2): an error state with a live pause would be
    skipped by the scheduler and hidden by the dashboard exclusion."""
    session = make_session()
    account, job = _mk_maildir_account_and_job(session, tmp_path, initial_sync_completed_at=DONE)
    job.status = JobStatus.pending
    account.sync_paused_until = datetime.now(UTC) + timedelta(hours=2)
    account.pause_reason = "throttle"
    session.commit()

    def fake_popen(cmd, **kw):
        sync_worker._killed_signals[job.id] = "SIGTERM"
        return _proc(["terminated"], code=-15)

    with (
        patch("mailfallback.services.sync_worker.subprocess.Popen", side_effect=fake_popen),
        PATCH_RC,
    ):
        execute_sync_job(session, job.id)

    session.refresh(job)
    session.refresh(account)
    assert job.signal == "SIGTERM"
    assert job.failure_kind is None  # untouched — today's behavior exactly
    assert account.sync_state == SyncState.error
    assert account.pause_reason is None and account.sync_paused_until is None


def test_real_error_clears_stale_pause(tmp_path):
    """Unclassifiable failure on a previously-paused account (review F2):
    the red error must win — pause columns clear."""
    session = make_session()
    account, job = _mk_maildir_account_and_job(session, tmp_path, initial_sync_completed_at=DONE)
    job.status = JobStatus.pending
    account.sync_paused_until = datetime.now(UTC) + timedelta(hours=2)
    account.pause_reason = "throttle"
    session.commit()

    with (
        patch(
            "mailfallback.services.sync_worker.subprocess.Popen",
            return_value=_proc(["AUTHENTICATIONFAILED Invalid credentials"], code=1),
        ),
        PATCH_RC,
    ):
        execute_sync_job(session, job.id)

    session.refresh(job)
    session.refresh(account)
    assert job.failure_kind == "error"
    assert account.sync_state == SyncState.error
    assert account.pause_reason is None and account.sync_paused_until is None


def test_status_pass_persists_total_with_exclusions(tmp_path):
    """The upstream STATUS pass sums MESSAGES over included folders only:
    pattern !-exclusions and \\Noselect placeholders are skipped, and
    literal-encoded LIST entries (review F4a: imaplib hands non-ASCII
    folder names back as tuples) are parsed, not garbled."""
    import json as _json

    session = make_session()
    account, job = _mk_maildir_account_and_job(session, tmp_path)
    account.extra_config = _json.dumps(
        {"patterns": '* !"[Gmail]/All Mail" !"[Gmail]/Spam" !"[Gmail]/Trash"'}
    )
    job.status = JobStatus.pending
    session.commit()

    fake_conn = MagicMock()
    fake_conn.list.return_value = (
        "OK",
        [
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\HasNoChildren) "/" "Sent"',
            b'(\\Noselect \\HasChildren) "/" "[Gmail]"',
            b'(\\HasNoChildren) "/" "[Gmail]/All Mail"',
            # Literal-encoded entry: (prefix, name_bytes) tuple.
            (b'(\\HasNoChildren) "/" {12}', "Posta città".encode()),
        ],
    )
    fake_conn.status.side_effect = [
        ("OK", [b'"INBOX" (MESSAGES 40000)']),
        ("OK", [b'"Sent" (MESSAGES 1200)']),
        ("OK", ['"Posta città" (MESSAGES 7)'.encode()]),
    ]

    with (
        patch("mailfallback.services.sync_worker.subprocess.Popen", return_value=_proc(["ok"])),
        PATCH_RC,
        patch("mailfallback.services.imap_check.connect_imap", return_value=fake_conn),
    ):
        execute_sync_job(session, job.id)

    session.refresh(account)
    assert account.initial_sync_total_messages == 41207
    assert fake_conn.status.call_count == 3  # excluded + Noselect cost nothing
    # The literal-named folder was STATUSed by its REAL name.
    assert any("Posta città" in str(c) for c in fake_conn.status.call_args_list)


def test_status_pass_failure_is_nonfatal(tmp_path):
    session = make_session()
    account, job = _mk_maildir_account_and_job(session, tmp_path)
    job.status = JobStatus.pending
    session.commit()

    with (
        patch("mailfallback.services.sync_worker.subprocess.Popen", return_value=_proc(["ok"])),
        PATCH_RC,
        patch(
            "mailfallback.services.imap_check.connect_imap",
            side_effect=OSError("upstream down"),
        ),
    ):
        execute_sync_job(session, job.id)

    session.refresh(job)
    session.refresh(account)
    assert job.status == JobStatus.completed  # the sync itself proceeded
    assert account.initial_sync_total_messages is None


def test_pipeline_depth_injected_only_while_initial_incomplete(tmp_path):
    """PipelineDepth 1 rides the RUNTIME config during the initial sync —
    never persisted, gone once the initial sync completed."""
    import json as _json

    session = make_session()
    account, job = _mk_maildir_account_and_job(session, tmp_path)
    job.status = JobStatus.pending
    session.commit()

    captured = []

    def fake_rc(**kwargs):
        captured.append(kwargs.get("extra_config"))
        return "config"

    with (
        patch("mailfallback.services.sync_worker.subprocess.Popen", return_value=_proc(["ok"])),
        patch("mailfallback.services.sync_worker.generate_mbsyncrc", side_effect=fake_rc),
        patch(
            "mailfallback.services.imap_check.connect_imap",
            side_effect=OSError("no upstream"),
        ),
    ):
        execute_sync_job(session, job.id)

    assert _json.loads(captured[0])["pipeline_depth"] == "1"
    session.refresh(account)
    assert account.extra_config is None  # runtime-only, never persisted

    # Initial sync now complete -> no injection (passthrough of the stored config).
    job2 = SyncJob(account_id=account.id, source="test")
    session.add(job2)
    session.commit()
    with (
        patch("mailfallback.services.sync_worker.subprocess.Popen", return_value=_proc(["ok"])),
        patch("mailfallback.services.sync_worker.generate_mbsyncrc", side_effect=fake_rc),
    ):
        execute_sync_job(session, job2.id)
    assert captured[1] is None


# ---------------------------------------------------------------------------
# Crash recovery sweep (Task 6, spec §9)
# ---------------------------------------------------------------------------


def test_recover_zombie_closes_running_and_makes_schedulable(tmp_path):
    """A running job from a dead process closes as interrupted (marker
    appended to the log); the syncing account returns to idle and — initial
    sync incomplete + budget headroom (none configured = unlimited) — any
    stale pause is CLEARED so the scheduler can resume immediately. The
    sweep itself never enqueues."""
    session = make_session()
    account, job = _mk_maildir_account_and_job(session, tmp_path)
    job.status = JobStatus.running
    job.log = "partial output"
    account.sync_state = SyncState.syncing
    account.sync_paused_until = datetime.now(UTC) + timedelta(hours=4)
    account.pause_reason = "throttle"
    session.commit()

    recovered = sync_worker.recover_zombie_sync_jobs(session)

    assert recovered == 1
    session.refresh(job)
    session.refresh(account)
    assert job.status == JobStatus.failed
    assert job.failure_kind == "interrupted"
    assert job.completed_at is not None
    assert job.log.startswith("partial output")
    assert "[recovered]" in job.log
    assert account.sync_state == SyncState.idle
    assert account.sync_paused_until is None
    assert account.pause_reason is None


def test_recover_zombie_respects_pause_without_headroom(tmp_path):
    """Initial sync incomplete but today's budget is spent: the pause stays
    (resuming now would re-burn the provider quota)."""
    session = make_session()
    account, job = _mk_maildir_account_and_job(session, tmp_path, daily_sync_budget_mb=1)
    job.status = JobStatus.running
    account.sync_state = SyncState.syncing
    account.traffic_date = datetime.now(UTC).date()
    account.bytes_synced_today = 2 * 1024 * 1024  # over the 1 MiB budget
    paused_until = datetime.now(UTC) + timedelta(hours=6)
    account.sync_paused_until = paused_until
    account.pause_reason = "budget"
    session.commit()

    sync_worker.recover_zombie_sync_jobs(session)

    session.refresh(account)
    session.refresh(job)
    assert job.failure_kind == "interrupted"
    assert account.sync_state == SyncState.idle
    assert account.pause_reason == "budget"  # untouched
    assert account.sync_paused_until is not None


def test_recover_zombie_stale_traffic_date_is_fresh_budget(tmp_path):
    """Yesterday's ledger does not count against today: a stale traffic_date
    means full headroom — the pause clears."""
    session = make_session()
    account, job = _mk_maildir_account_and_job(session, tmp_path, daily_sync_budget_mb=1)
    job.status = JobStatus.running
    account.traffic_date = (datetime.now(UTC) - timedelta(days=1)).date()
    account.bytes_synced_today = 5 * 1024 * 1024  # spent — but YESTERDAY
    account.sync_paused_until = datetime.now(UTC) + timedelta(hours=6)
    account.pause_reason = "budget"
    session.commit()

    sync_worker.recover_zombie_sync_jobs(session)

    session.refresh(account)
    assert account.sync_paused_until is None
    assert account.pause_reason is None


def test_recover_zombie_initial_complete_keeps_pause(tmp_path):
    session = make_session()
    account, job = _mk_maildir_account_and_job(session, tmp_path, initial_sync_completed_at=DONE)
    job.status = JobStatus.running
    account.sync_paused_until = datetime.now(UTC) + timedelta(hours=2)
    account.pause_reason = "throttle"
    session.commit()

    sync_worker.recover_zombie_sync_jobs(session)

    session.refresh(account)
    assert account.pause_reason == "throttle"
    assert account.sync_paused_until is not None


def test_recover_zombie_closes_orphaned_pending_jobs(tmp_path):
    """The queue is DB rows + an IN-MEMORY executor: a crash orphans pending
    rows, and an orphaned pending row blocks create_sync_job for that
    account FOREVER (the existing-job guard) — the sweep must close them."""
    session = make_session()
    account, job = _mk_maildir_account_and_job(session, tmp_path)
    job.status = JobStatus.pending
    job.log = None
    session.commit()

    recovered = sync_worker.recover_zombie_sync_jobs(session)

    assert recovered == 1
    session.refresh(job)
    assert job.status == JobStatus.failed
    assert job.failure_kind == "interrupted"
    assert "[recovered]" in job.log
    # The account is schedulable again: create_sync_job no longer blocked.
    from mailfallback.services.sync_service import create_sync_job

    assert create_sync_job(session, account.id, source="test") is not None


def test_recover_zombie_skips_live_and_finished_jobs(tmp_path):
    """Idempotent re-call safety: a running job whose process is alive
    (_running_procs) is untouched; completed/failed jobs are never touched;
    a healthy DB is a no-op."""
    session = make_session()
    account, job = _mk_maildir_account_and_job(session, tmp_path)
    job.status = JobStatus.running
    done = SyncJob(account_id=account.id, source="test", status=JobStatus.completed)
    failed = SyncJob(account_id=account.id, source="test", status=JobStatus.failed)
    session.add_all([done, failed])
    session.commit()

    sync_worker._running_procs[job.id] = MagicMock()  # alive process
    try:
        recovered = sync_worker.recover_zombie_sync_jobs(session)
    finally:
        sync_worker._running_procs.pop(job.id, None)

    assert recovered == 0
    session.refresh(job)
    session.refresh(done)
    assert job.status == JobStatus.running  # untouched
    assert done.status == JobStatus.completed
    assert failed.status == JobStatus.failed
    # Healthy DB after the real recovery: still a no-op.
    job.status = JobStatus.completed
    session.commit()
    assert sync_worker.recover_zombie_sync_jobs(session) == 0


def test_budget_trip_between_invocations_skips_full_pass(tmp_path, monkeypatch):
    """Review F7: a budget marker landing while the INBOX pass was finishing
    (the sampler's stop found an already-finished proc) must prevent the
    full pass from starting — and the job lands budget_paused."""
    _zero_jitter(monkeypatch)
    session = make_session()
    account, job = _mk_maildir_account_and_job(session, tmp_path)
    job.status = JobStatus.pending
    session.commit()

    cmds = []

    def fake_popen(cmd, **kw):
        cmds.append(cmd)
        # Marker lands during the INBOX pass; the proc still exits clean.
        sync_worker._budget_stops.add(job.id)
        return _proc(["inbox done"])

    with (
        patch("mailfallback.services.sync_worker.subprocess.Popen", side_effect=fake_popen),
        PATCH_RC,
    ):
        execute_sync_job(session, job.id)

    assert len(cmds) == 1  # the full pass never started
    session.refresh(job)
    session.refresh(account)
    assert job.failure_kind == "budget_paused"
    assert account.pause_reason == "budget"
    assert account.initial_sync_completed_at is None  # NOT a clean full pass
    assert job.id not in sync_worker._budget_stops  # consumed


def test_budget_rearm_stops_freshly_registered_subprocess(tmp_path, monkeypatch):
    """Review F1: the marker landing in the window AFTER the
    inter-invocation check but before the new Popen registers would have
    stopped the already-reaped previous proc (a no-op) — the once-guard
    would never fire again and the full pass would run unbounded. The
    worker re-arms right after registering the new proc."""
    _zero_jitter(monkeypatch)
    session = make_session()
    _account, job = _mk_maildir_account_and_job(session, tmp_path)
    job.status = JobStatus.pending
    session.commit()

    stops = []
    monkeypatch.setattr(sync_worker, "stop_sync_job", lambda jid: stops.append(jid))
    cmds = []

    def fake_popen(cmd, **kw):
        cmds.append(cmd)
        if len(cmds) == 2:
            # EXACTLY the race window: after the marker check passed for
            # idx 1, before the new proc is registered.
            sync_worker._budget_stops.add(job.id)
        return _proc([f"pass {len(cmds)}"])

    with (
        patch("mailfallback.services.sync_worker.subprocess.Popen", side_effect=fake_popen),
        PATCH_RC,
    ):
        execute_sync_job(session, job.id)

    assert len(cmds) == 2
    assert stops == [job.id]  # the re-arm stopped the CURRENT subprocess
    session.refresh(job)
    assert job.failure_kind == "budget_paused"
    assert job.id not in sync_worker._budget_stops


def test_priority_pass_skipped_when_patterns_exclude_inbox(tmp_path):
    """Review F5: a command-line box-spec supersedes Patterns — if the user
    excluded INBOX, the priority pass must not resurrect it. Single full
    invocation instead."""
    import json as _json

    session = make_session()
    account, job = _mk_maildir_account_and_job(session, tmp_path)
    account.extra_config = _json.dumps({"patterns": "* !INBOX"})
    job.status = JobStatus.pending
    session.commit()

    cmds = []

    def fake_popen(cmd, **kw):
        cmds.append(cmd)
        return _proc(["ok"])

    with (
        patch("mailfallback.services.sync_worker.subprocess.Popen", side_effect=fake_popen),
        patch("mailfallback.services.sync_worker.generate_mbsyncrc", return_value="config"),
    ):
        execute_sync_job(session, job.id)

    assert len(cmds) == 1
    assert cmds[0][-1] == "meter"  # the full channel, no :INBOX box-spec


# ---------------------------------------------------------------------------
# OAuth2 token refresh terminality (Task 3)
# ---------------------------------------------------------------------------


def _oauth_creds():
    import json

    return json.dumps({"provider": "google", "refresh_token": "rt", "access_token": "old"})


def test_refresh_invalid_grant_is_terminal(db_session, oauth_account):
    from authlib.integrations.base_client.errors import OAuthError

    with patch(
        "mailfallback.services.oauth2.refresh_google_token",
        side_effect=OAuthError(error="invalid_grant", description="Bad Request"),
    ):
        token, terminal = sync_worker._refresh_oauth_token(
            _oauth_creds(), db_session, oauth_account
        )
    assert token is None
    assert terminal is True


def test_refresh_network_error_not_terminal(db_session, oauth_account):
    with patch(
        "mailfallback.services.oauth2.refresh_google_token",
        side_effect=ConnectionError("boom"),
    ):
        token, terminal = sync_worker._refresh_oauth_token(
            _oauth_creds(), db_session, oauth_account
        )
    assert token is None
    assert terminal is False


def test_run_sync_invalid_grant_parks_needs_reauth(db_session, oauth_account):
    """Terminal token refresh (invalid_grant) sets sync_state=needs_reauth."""
    job = SyncJob(account_id=oauth_account.id, source="test", status=JobStatus.pending)
    db_session.add(job)
    db_session.commit()

    with patch.object(sync_worker, "_refresh_oauth_token", return_value=(None, True)):
        execute_sync_job(db_session, job.id)

    db_session.refresh(oauth_account)
    assert oauth_account.sync_state == SyncState.needs_reauth


def test_run_sync_transient_refresh_stays_error(db_session, oauth_account):
    """Non-terminal token refresh failure (network blip) sets sync_state=error."""
    job = SyncJob(account_id=oauth_account.id, source="test", status=JobStatus.pending)
    db_session.add(job)
    db_session.commit()

    with patch.object(sync_worker, "_refresh_oauth_token", return_value=(None, False)):
        execute_sync_job(db_session, job.id)

    db_session.refresh(oauth_account)
    assert oauth_account.sync_state == SyncState.error
