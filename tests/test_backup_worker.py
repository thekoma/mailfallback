"""Tests for backup_worker — restic_service functions mocked."""

from unittest.mock import patch

import pytest

from mailfallback.models import (
    Account,
    BackupPolicy,
    BackupStatus,
    MailStore,
    Repository,
    RetentionPreset,
)
from mailfallback.services.backup_worker import execute_backup


@pytest.fixture
def store(db_session):
    s = MailStore(name="default", path="/data/mailboxes")
    db_session.add(s)
    db_session.commit()
    db_session.refresh(s)
    return s


@pytest.fixture
def destination(db_session):
    d = Repository(
        name="test-s3",
        backend_type="s3",
        s3_endpoint="enc-endpoint",
        s3_bucket="enc-bucket",
        s3_access_key="enc-access",
        s3_secret_key="enc-secret",
        restic_password="enc-password",
    )
    db_session.add(d)
    db_session.commit()
    db_session.refresh(d)
    return d


@pytest.fixture
def account(db_session, store):
    a = Account(
        name="test-account",
        imap_host="imap.example.com",
        maildir_path="/data/mailboxes/test-uuid",
        store_id=store.id,
    )
    db_session.add(a)
    db_session.commit()
    db_session.refresh(a)
    return a


@pytest.fixture
def account_backup(db_session, account, destination):
    ab = BackupPolicy(
        account_id=account.id,
        destination_id=destination.id,
        schedule="0 2 * * *",
        retention_preset=RetentionPreset.standard,
    )
    db_session.add(ab)
    db_session.commit()
    db_session.refresh(ab)
    return ab


class TestExecuteBackup:
    @patch("mailfallback.services.backup_worker.restic_service")
    def test_successful_backup(self, mock_restic, db_session, account_backup):
        mock_restic.init_repo.return_value = True
        mock_restic.run_backup.return_value = {
            "message_type": "summary",
            "files_new": 5,
        }
        mock_restic.apply_retention.return_value = {"pruned": True}
        mock_restic.list_snapshots.return_value = [
            {"short_id": "abc123", "time": "2026-05-10T20:00:00Z", "hostname": "mfb"},
            {"short_id": "def456", "time": "2026-05-09T20:00:00Z", "hostname": "mfb"},
        ]

        execute_backup(db_session, account_backup.id)

        db_session.refresh(account_backup)
        assert account_backup.last_status == BackupStatus.completed
        assert account_backup.last_backup_at is not None
        assert account_backup.last_run_at is not None
        assert account_backup.last_successful_run_at is not None
        assert account_backup.last_snapshot_count == 2
        assert account_backup.last_snapshot_at is not None
        assert account_backup.last_error is None

    @patch("mailfallback.services.backup_worker.restic_service")
    def test_backup_tags_snapshots_with_account_metadata(
        self, mock_restic, db_session, account_backup, account
    ):
        account.email_address = "w@x.y"
        db_session.commit()
        mock_restic.init_repo.return_value = True
        mock_restic.run_backup.return_value = {"message_type": "summary"}
        mock_restic.apply_retention.return_value = {"pruned": True}
        mock_restic.list_snapshots.return_value = []

        execute_backup(db_session, account_backup.id)

        tags = mock_restic.run_backup.call_args.kwargs["tags"]
        assert "mfb:email=w@x.y" in tags
        assert any(t.startswith("mfb:name=") for t in tags)

    @patch("mailfallback.services.backup_worker.restic_service")
    def test_failed_run_clears_success_timestamp(self, mock_restic, db_session, account_backup):
        """A failed run records last_run_at but leaves last_successful_run_at untouched."""
        # Pre-populate a previous success.
        from datetime import UTC, datetime, timedelta

        prior = datetime.now(UTC) - timedelta(hours=2)
        account_backup.last_successful_run_at = prior
        db_session.commit()

        mock_restic.init_repo.return_value = True
        mock_restic.run_backup.side_effect = RuntimeError("Restic backup failed: disk full")

        execute_backup(db_session, account_backup.id)

        db_session.refresh(account_backup)
        assert account_backup.last_status == BackupStatus.failed
        assert account_backup.last_run_at is not None
        # Prior success timestamp untouched (SQLite drops tzinfo, so compare naive).
        assert account_backup.last_successful_run_at.replace(tzinfo=None) == prior.replace(
            tzinfo=None
        )

    @patch("mailfallback.services.backup_worker.restic_service")
    def test_failed_init(self, mock_restic, db_session, account_backup):
        mock_restic.init_repo.return_value = False

        execute_backup(db_session, account_backup.id)

        db_session.refresh(account_backup)
        assert account_backup.last_status == BackupStatus.failed
        assert "initialize" in account_backup.last_error.lower()

    @patch("mailfallback.services.backup_worker.restic_service")
    def test_failed_backup(self, mock_restic, db_session, account_backup):
        mock_restic.init_repo.return_value = True
        mock_restic.run_backup.side_effect = RuntimeError("Restic backup failed: disk full")

        execute_backup(db_session, account_backup.id)

        db_session.refresh(account_backup)
        assert account_backup.last_status == BackupStatus.failed
        assert "disk full" in account_backup.last_error

    @patch("mailfallback.services.backup_worker.restic_service")
    def test_failed_retention(self, mock_restic, db_session, account_backup):
        mock_restic.init_repo.return_value = True
        mock_restic.run_backup.return_value = {"message_type": "summary"}
        mock_restic.apply_retention.side_effect = RuntimeError("Restic forget failed: prune error")

        execute_backup(db_session, account_backup.id)

        db_session.refresh(account_backup)
        assert account_backup.last_status == BackupStatus.failed
        assert "prune error" in account_backup.last_error

    def test_missing_backup_id(self, db_session):
        """execute_backup with nonexistent ID should not raise."""
        execute_backup(db_session, "nonexistent-id")

    @patch("mailfallback.services.backup_worker.restic_service")
    def test_status_is_running_during_backup(self, mock_restic, db_session, account_backup):
        """Verify status transitions to running before completing."""
        observed_statuses = []

        def capture_status(*_args, **_kwargs):
            db_session.refresh(account_backup)
            observed_statuses.append(account_backup.last_status)
            return True

        mock_restic.init_repo.side_effect = capture_status
        mock_restic.run_backup.return_value = {"message_type": "summary"}
        mock_restic.apply_retention.return_value = {"pruned": True}

        execute_backup(db_session, account_backup.id)

        assert BackupStatus.running in observed_statuses


class TestIndexHooks:
    @patch("mailfallback.services.backup_worker.index_service")
    @patch("mailfallback.services.backup_worker.restic_service")
    def test_backup_worker_calls_record_snapshot_after_success(
        self, mock_restic, mock_index, db_session, account_backup
    ):
        """After a successful restic backup, record_snapshot is called with the new snapshot id."""
        mock_restic.init_repo.return_value = True
        mock_restic.run_backup.return_value = {
            "snapshot_id": "abc12345",
            "files_new": 5,
            "files_changed": 0,
            "data_added": 1024,
        }
        mock_restic.apply_retention.return_value = {"pruned": False, "removed_snapshot_ids": []}
        mock_restic.list_snapshots.return_value = []

        execute_backup(db_session, account_backup.id)

        mock_index.record_snapshot.assert_called_once_with(
            db_session, account_backup.account_id, "abc12345"
        )

    @patch("mailfallback.services.backup_worker.index_service")
    @patch("mailfallback.services.backup_worker.restic_service")
    def test_backup_worker_calls_prune_snapshot_for_each_removed(
        self, mock_restic, mock_index, db_session, account_backup
    ):
        """When apply_retention prunes snapshots, prune_snapshot is called for each id."""
        mock_restic.init_repo.return_value = True
        mock_restic.run_backup.return_value = {"snapshot_id": "new00001"}
        mock_restic.apply_retention.return_value = {
            "pruned": True,
            "removed_snapshot_ids": ["old00001", "old00002"],
        }
        mock_restic.list_snapshots.return_value = []

        execute_backup(db_session, account_backup.id)

        assert mock_index.prune_snapshot.call_count == 2
        mock_index.prune_snapshot.assert_any_call(db_session, "old00001")
        mock_index.prune_snapshot.assert_any_call(db_session, "old00002")

    @patch("mailfallback.services.backup_worker.index_service")
    @patch("mailfallback.services.backup_worker.restic_service")
    def test_backup_worker_index_failure_does_not_break_backup(
        self, mock_restic, mock_index, db_session, account_backup
    ):
        """If index_service raises, the backup still completes successfully."""
        mock_restic.init_repo.return_value = True
        mock_restic.run_backup.return_value = {"snapshot_id": "new00002"}
        mock_restic.apply_retention.return_value = {"pruned": False, "removed_snapshot_ids": []}
        mock_restic.list_snapshots.return_value = []
        mock_index.record_snapshot.side_effect = RuntimeError("indexer kaboom")

        # Must NOT raise
        execute_backup(db_session, account_backup.id)

        db_session.refresh(account_backup)
        assert account_backup.last_status == BackupStatus.completed


class TestGetBackupProgress:
    def test_no_progress(self):
        from mailfallback.services.backup_worker import get_backup_progress

        assert get_backup_progress("nonexistent") is None


class TestBackupExecutor:
    def test_get_executor(self):
        from mailfallback.services.backup_worker import (
            get_backup_executor,
            shutdown_backup_executor,
        )

        executor = get_backup_executor()
        assert executor is not None
        shutdown_backup_executor()

    def test_shutdown_idempotent(self):
        from mailfallback.services.backup_worker import shutdown_backup_executor

        # Should not raise when no executor exists
        shutdown_backup_executor()


class TestBackupJobRows:
    """execute_backup must leave a per-run record behind — the thing whose
    absence let the 2026-08-01 OOMKill strand a policy on "running"."""

    @patch("mailfallback.services.backup_worker.restic_service")
    def test_successful_run_creates_a_completed_job(self, mock_restic, db_session, account_backup):
        from mailfallback.models import BackupJob, JobStatus

        mock_restic.init_repo.return_value = True
        mock_restic.run_backup.return_value = {
            "message_type": "summary",
            "snapshot_id": "snap1",
            "total_bytes_processed": 14_000_000_000,
            "data_added": 4096,
        }
        mock_restic.apply_retention.return_value = {"pruned": True}
        mock_restic.list_snapshots.return_value = []

        execute_backup(db_session, account_backup.id)

        job = db_session.query(BackupJob).one()
        assert job.status == JobStatus.completed
        assert job.snapshot_id == "snap1"
        assert job.bytes_processed == 14_000_000_000
        assert job.bytes_added == 4096
        assert job.started_at is not None
        assert job.completed_at is not None
        assert job.account_id == account_backup.account_id
        assert job.policy_id == account_backup.id

    @patch("mailfallback.services.backup_worker.restic_service")
    def test_failed_run_creates_a_failed_job_with_the_error(
        self, mock_restic, db_session, account_backup
    ):
        from mailfallback.models import BackupJob, JobStatus

        mock_restic.init_repo.return_value = True
        mock_restic.run_backup.side_effect = RuntimeError(
            "Restic backup failed: repository is locked"
        )

        execute_backup(db_session, account_backup.id)

        job = db_session.query(BackupJob).one()
        assert job.status == JobStatus.failed
        assert job.failure_kind == "error"
        assert "repository is locked" in job.log
        assert job.completed_at is not None

    @patch("mailfallback.services.backup_worker.restic_service")
    def test_source_defaults_to_schedule(self, mock_restic, db_session, account_backup):
        from mailfallback.models import BackupJob

        mock_restic.init_repo.return_value = True
        mock_restic.run_backup.return_value = {}
        mock_restic.apply_retention.return_value = {"pruned": True}
        mock_restic.list_snapshots.return_value = []

        execute_backup(db_session, account_backup.id)

        assert db_session.query(BackupJob).one().source == "schedule"

    @patch("mailfallback.services.backup_worker.restic_service")
    def test_source_records_a_manual_trigger(self, mock_restic, db_session, account_backup):
        from mailfallback.models import BackupJob

        mock_restic.init_repo.return_value = True
        mock_restic.run_backup.return_value = {}
        mock_restic.apply_retention.return_value = {"pruned": True}
        mock_restic.list_snapshots.return_value = []

        execute_backup(db_session, account_backup.id, source="manual")

        assert db_session.query(BackupJob).one().source == "manual"

    @patch("mailfallback.services.backup_worker.restic_service")
    def test_a_missing_summary_does_not_break_the_job_row(
        self, mock_restic, db_session, account_backup
    ):
        """restic can exit 0 with no summary; byte counters must stay 0, not None."""
        from mailfallback.models import BackupJob, JobStatus

        mock_restic.init_repo.return_value = True
        mock_restic.run_backup.return_value = {}
        mock_restic.apply_retention.return_value = {"pruned": True}
        mock_restic.list_snapshots.return_value = []

        execute_backup(db_session, account_backup.id)

        job = db_session.query(BackupJob).one()
        assert job.status == JobStatus.completed
        assert job.bytes_processed == 0
        assert job.bytes_added == 0
        assert job.snapshot_id is None


class TestBackupHeartbeat:
    @patch("mailfallback.services.backup_worker.restic_service")
    def test_restic_events_refresh_the_heartbeat(self, mock_restic, db_session, account_backup):
        """on_event must stamp updated_ts — the watchdog reads exactly this."""
        from mailfallback.services import backup_worker

        captured = {}

        def fake_run_backup(dest, account_id, path, tags=None, on_event=None, register=None):
            job_id = next(iter(backup_worker._backup_progress))
            on_event({"message_type": "status", "percent_done": 0.5, "bytes_done": 123})
            captured["progress"] = dict(backup_worker._backup_progress[job_id])
            return {}

        mock_restic.init_repo.return_value = True
        mock_restic.run_backup.side_effect = fake_run_backup
        mock_restic.apply_retention.return_value = {"pruned": True}
        mock_restic.list_snapshots.return_value = []

        execute_backup(db_session, account_backup.id)

        assert captured["progress"]["updated_ts"] > 0
        assert captured["progress"]["percent_done"] == 0.5
        assert captured["progress"]["bytes_done"] == 123

    @patch("mailfallback.services.backup_worker.restic_service")
    def test_progress_and_proc_registry_are_cleaned_up(
        self, mock_restic, db_session, account_backup
    ):
        from mailfallback.services import backup_worker

        mock_restic.init_repo.return_value = True
        mock_restic.run_backup.return_value = {}
        mock_restic.apply_retention.return_value = {"pruned": True}
        mock_restic.list_snapshots.return_value = []

        execute_backup(db_session, account_backup.id)

        assert backup_worker._backup_progress == {}
        assert backup_worker._running_backup_procs == {}

    @patch("mailfallback.services.backup_worker.restic_service")
    def test_registry_is_cleaned_up_after_a_failure_too(
        self, mock_restic, db_session, account_backup
    ):
        from mailfallback.services import backup_worker

        mock_restic.init_repo.return_value = True
        mock_restic.run_backup.side_effect = RuntimeError("boom")

        execute_backup(db_session, account_backup.id)

        assert backup_worker._backup_progress == {}
        assert backup_worker._running_backup_procs == {}
