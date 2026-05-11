"""Tests for mount_service — ephemeral Recovery lifecycle."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from mailfallback.config import settings
from mailfallback.models import (
    Account,
    BackupPolicy,
    Recovery,
    RecoveryKind,
    RecoveryStatus,
    Repository,
)
from mailfallback.services import mount_service


@pytest.fixture
def account_with_backup(db_session, default_store):
    repo = Repository(name="r", backend_type="local", local_path="/tmp/r", restic_password="x")
    db_session.add(repo)
    acct = Account(
        name="a",
        store=default_store,
        maildir_path="/data/mailboxes/a",
        imap_host="imap.example.com",
    )
    db_session.add(acct)
    db_session.flush()
    db_session.add(BackupPolicy(account_id=acct.id, destination_id=repo.id))
    db_session.commit()
    return acct


@patch("mailfallback.services.mount_service.recovery_service")
def test_ensure_mounted_creates_ephemeral(mock_recovery, db_session, account_with_backup):
    fake = Recovery(
        account_id=account_with_backup.id,
        repository_id="repo-id",
        snapshot_id="snap-1",
        restore_path="/tmp/recovered",
        status=RecoveryStatus.ready,
        kind=RecoveryKind.ephemeral,
        ttl_minutes=settings.recovery_ephemeral_ttl_minutes,
    )
    mock_recovery.create_recovery.return_value = fake

    rec = mount_service.ensure_mounted(db_session, account_with_backup.id, "snap-1")

    assert rec.kind == RecoveryKind.ephemeral
    mock_recovery.create_recovery.assert_called_once_with(
        db_session,
        account_with_backup.id,
        "snap-1",
        kind=RecoveryKind.ephemeral,
        ttl_minutes=settings.recovery_ephemeral_ttl_minutes,
    )


@patch("mailfallback.services.mount_service.recovery_service")
def test_ensure_mounted_returns_existing_and_bumps_last_accessed(
    mock_recovery, db_session, account_with_backup
):
    old = datetime.now(UTC) - timedelta(minutes=20)
    existing = Recovery(
        account_id=account_with_backup.id,
        snapshot_id="snap-1",
        restore_path="/tmp/r",
        status=RecoveryStatus.ready,
        kind=RecoveryKind.ephemeral,
        ttl_minutes=30,
        last_accessed_at=old,
    )
    db_session.add(existing)
    db_session.commit()

    rec = mount_service.ensure_mounted(db_session, account_with_backup.id, "snap-1")

    assert rec.id == existing.id
    # SQLite drops tzinfo on round-trip; compare naive.
    assert rec.last_accessed_at.replace(tzinfo=None) > old.replace(tzinfo=None)
    mock_recovery.create_recovery.assert_not_called()


def test_touch_mount_updates_last_accessed_at(db_session, account_with_backup):
    old = datetime.now(UTC) - timedelta(minutes=20)
    rec = Recovery(
        account_id=account_with_backup.id,
        snapshot_id="snap-1",
        restore_path="/tmp/r",
        status=RecoveryStatus.ready,
        kind=RecoveryKind.ephemeral,
        ttl_minutes=30,
        last_accessed_at=old,
    )
    db_session.add(rec)
    db_session.commit()

    mount_service.touch_mount(db_session, rec.id)

    db_session.refresh(rec)
    # SQLite drops tzinfo on DateTime(timezone=True) round-trips; normalise.
    assert rec.last_accessed_at.replace(tzinfo=None) > old.replace(tzinfo=None)


@patch("mailfallback.services.mount_service.recovery_service")
def test_force_unmount_delegates_to_delete_recovery(mock_recovery, db_session, account_with_backup):
    rec = Recovery(
        account_id=account_with_backup.id,
        snapshot_id="snap-1",
        restore_path="/tmp/r",
        status=RecoveryStatus.ready,
        kind=RecoveryKind.ephemeral,
    )
    db_session.add(rec)
    db_session.commit()

    mount_service.force_unmount(db_session, rec.id)

    mock_recovery.delete_recovery.assert_called_once_with(db_session, rec.id)


@patch("mailfallback.services.mount_service.recovery_service")
def test_cleanup_idle_mounts_removes_expired_ephemeral(
    mock_recovery, db_session, account_with_backup
):
    expired = Recovery(
        account_id=account_with_backup.id,
        snapshot_id="old",
        restore_path="/tmp/r",
        status=RecoveryStatus.ready,
        kind=RecoveryKind.ephemeral,
        ttl_minutes=30,
        last_accessed_at=datetime.now(UTC) - timedelta(minutes=45),
    )
    db_session.add(expired)
    db_session.commit()

    removed = mount_service.cleanup_idle_mounts(db_session)

    assert removed == 1
    mock_recovery.delete_recovery.assert_called_once_with(db_session, expired.id)


@patch("mailfallback.services.mount_service.recovery_service")
def test_cleanup_idle_mounts_keeps_recent_ephemeral(mock_recovery, db_session, account_with_backup):
    fresh = Recovery(
        account_id=account_with_backup.id,
        snapshot_id="fresh",
        restore_path="/tmp/r",
        status=RecoveryStatus.ready,
        kind=RecoveryKind.ephemeral,
        ttl_minutes=30,
        last_accessed_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    db_session.add(fresh)
    db_session.commit()

    removed = mount_service.cleanup_idle_mounts(db_session)

    assert removed == 0
    mock_recovery.delete_recovery.assert_not_called()


@patch("mailfallback.services.mount_service.recovery_service")
def test_cleanup_idle_mounts_keeps_persistent_even_if_old(
    mock_recovery, db_session, account_with_backup
):
    persistent = Recovery(
        account_id=account_with_backup.id,
        snapshot_id="forever",
        restore_path="/tmp/r",
        status=RecoveryStatus.ready,
        kind=RecoveryKind.persistent,
        ttl_minutes=None,
        last_accessed_at=datetime.now(UTC) - timedelta(days=30),
    )
    db_session.add(persistent)
    db_session.commit()

    removed = mount_service.cleanup_idle_mounts(db_session)

    assert removed == 0
    mock_recovery.delete_recovery.assert_not_called()
