"""Boot sweep and stall watchdog for off-site backups.

Regression cover for the 2026-08-01 incident: the app container was OOMKilled
mid-backup, the SIGKILL skipped execute_backup's finally, and the policy
reported "running" for 18 hours because nothing swept backup jobs.
"""

from datetime import UTC, datetime, timedelta

import pytest

from mailfallback.models import (
    Account,
    BackupJob,
    BackupPolicy,
    BackupStatus,
    JobStatus,
    Repository,
    RetentionPreset,
)
from mailfallback.services.backup_worker import recover_zombie_backup_jobs


@pytest.fixture
def policy(db_session, default_store):
    account = Account(
        name="Main gMail",
        imap_host="imap.gmail.com",
        maildir_path="/data/mailboxes/test-uuid",
        store_id=default_store.id,
    )
    repo = Repository(name="Repo01", backend_type="s3", restic_password="enc-password")
    db_session.add_all([account, repo])
    db_session.commit()
    p = BackupPolicy(
        account_id=account.id,
        destination_id=repo.id,
        retention_preset=RetentionPreset.standard,
        last_status=BackupStatus.running,
    )
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def _job(db_session, policy, status=JobStatus.running, *, age_s: int = 0) -> BackupJob:
    job = BackupJob(
        policy_id=policy.id,
        account_id=policy.account_id,
        status=status,
        started_at=datetime.now(UTC) - timedelta(seconds=age_s),
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


class TestBootSweep:
    def test_closes_a_running_job_left_by_a_crash(self, db_session, policy):
        job = _job(db_session, policy, age_s=64800)  # 18h, as in the incident

        assert recover_zombie_backup_jobs(db_session) == 1

        db_session.refresh(job)
        assert job.status == JobStatus.failed
        assert job.failure_kind == "interrupted"
        assert job.completed_at is not None
        assert "[recovered]" in job.log

    def test_heals_the_policy(self, db_session, policy):
        """The policy is what the dashboard counts and the account page shows."""
        _job(db_session, policy)

        recover_zombie_backup_jobs(db_session)

        db_session.refresh(policy)
        assert policy.last_status == BackupStatus.failed
        assert "[recovered]" in policy.last_error

    def test_closes_pending_rows_too(self, db_session, policy):
        job = _job(db_session, policy, JobStatus.pending)
        assert recover_zombie_backup_jobs(db_session) == 1
        db_session.refresh(job)
        assert job.status == JobStatus.failed

    def test_skips_a_genuinely_live_run(self, db_session, policy):
        """Idempotent re-call safety: a tracked process is not a zombie."""
        from mailfallback.services import backup_worker

        job = _job(db_session, policy)
        backup_worker._running_backup_procs[job.id] = object()
        try:
            assert recover_zombie_backup_jobs(db_session) == 0
            db_session.refresh(job)
            assert job.status == JobStatus.running
        finally:
            backup_worker._running_backup_procs.pop(job.id, None)

    def test_leaves_finished_rows_alone(self, db_session, policy):
        _job(db_session, policy, JobStatus.completed)
        assert recover_zombie_backup_jobs(db_session) == 0

    def test_appends_to_an_existing_log_rather_than_replacing_it(self, db_session, policy):
        job = _job(db_session, policy)
        job.log = "earlier output"
        db_session.commit()

        recover_zombie_backup_jobs(db_session)

        db_session.refresh(job)
        assert "earlier output" in job.log
        assert "[recovered]" in job.log

    def test_does_not_touch_a_policy_that_already_completed(self, db_session, policy):
        """A newer successful run must not be clobbered by sweeping an old row."""
        policy.last_status = BackupStatus.completed
        db_session.commit()
        _job(db_session, policy)

        recover_zombie_backup_jobs(db_session)

        db_session.refresh(policy)
        assert policy.last_status == BackupStatus.completed


class TestBootWiring:
    """app.py must run BOTH sweeps, and one failing must not skip the other."""

    def test_both_sweeps_run(self, db_session):
        from unittest.mock import patch

        from mailfallback.app import _recover_zombie_jobs

        with (
            patch("mailfallback.services.sync_worker.recover_zombie_sync_jobs") as sync_sweep,
            patch("mailfallback.services.backup_worker.recover_zombie_backup_jobs") as backup_sweep,
        ):
            _recover_zombie_jobs(db_session)

        sync_sweep.assert_called_once_with(db_session)
        backup_sweep.assert_called_once_with(db_session)

    def test_a_failing_sync_sweep_does_not_skip_the_backup_sweep(self, db_session):
        from unittest.mock import patch

        from mailfallback.app import _recover_zombie_jobs

        with (
            patch(
                "mailfallback.services.sync_worker.recover_zombie_sync_jobs",
                side_effect=RuntimeError("boom"),
            ),
            patch("mailfallback.services.backup_worker.recover_zombie_backup_jobs") as backup_sweep,
        ):
            _recover_zombie_jobs(db_session)

        backup_sweep.assert_called_once_with(db_session)

    def test_a_failing_backup_sweep_does_not_block_boot(self, db_session):
        from unittest.mock import patch

        from mailfallback.app import _recover_zombie_jobs

        with (
            patch("mailfallback.services.sync_worker.recover_zombie_sync_jobs"),
            patch(
                "mailfallback.services.backup_worker.recover_zombie_backup_jobs",
                side_effect=RuntimeError("boom"),
            ),
        ):
            _recover_zombie_jobs(db_session)  # must not raise
