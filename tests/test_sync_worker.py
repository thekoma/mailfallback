# tests/test_sync_worker.py
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mailfallback.db import Base
from mailfallback.models import Account, JobStatus, SyncJob, SyncState
from mailfallback.services.sync_worker import execute_sync_job


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _make_account_and_job(session):
    account = Account(
        name="Test",
        imap_host="imap.test.com",
        maildir_path="/tmp/test_maildir",
        credentials=None,
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

    mock_result = type("Result", (), {"returncode": 0, "stdout": "synced ok", "stderr": ""})()
    with (
        patch("mailfallback.services.sync_worker.subprocess.run", return_value=mock_result),
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

    mock_result = type("Result", (), {"returncode": 1, "stdout": "", "stderr": "auth failed"})()
    with (
        patch("mailfallback.services.sync_worker.subprocess.run", return_value=mock_result),
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
        return type("Result", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    with (
        patch("mailfallback.services.sync_worker.subprocess.run", side_effect=capture_state),
        patch("mailfallback.services.sync_worker.generate_mbsyncrc", return_value="config"),
    ):
        execute_sync_job(session, job.id)

    assert captured_state["account_state"] == SyncState.syncing
    assert captured_state["job_status"] == JobStatus.running
