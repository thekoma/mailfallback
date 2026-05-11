"""Tests for scheduler — focus on job registration."""

from unittest.mock import patch

from mailfallback.services.scheduler import scheduler, start_scheduler


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
