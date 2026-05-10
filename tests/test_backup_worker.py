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
