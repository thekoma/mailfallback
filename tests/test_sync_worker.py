# tests/test_sync_worker.py
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mailfallback.db import Base
from mailfallback.models import Account, JobStatus, MailStore, SyncJob, SyncState
from mailfallback.services.sync_worker import execute_sync_job


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


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
