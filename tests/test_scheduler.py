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
