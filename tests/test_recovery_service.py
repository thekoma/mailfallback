"""Tests for recovery_service."""

from unittest.mock import patch

import pytest

from mailfallback.models import (
    Account,
    BackupPolicy,
    MailStore,
    RecoveryKind,
    Repository,
)
from mailfallback.services import recovery_service


@pytest.fixture
def tmp_store(db_session, tmp_path):
    """A MailStore rooted in a tmp dir, so on-disk restore_root creation works."""
    store = MailStore(name="tmp", path=str(tmp_path))
    db_session.add(store)
    db_session.commit()
    db_session.refresh(store)
    return store


@patch("mailfallback.services.recovery_service.restic_service")
def test_create_recovery_defaults_to_persistent(mock_restic, db_session, tmp_store):
    repo = Repository(
        name="r",
        backend_type="local",
        local_path="/tmp/r",
        restic_password="secret",
    )
    db_session.add(repo)
    acct = Account(
        name="a",
        store=tmp_store,
        maildir_path=f"{tmp_store.path}/a",
        imap_host="imap.example.com",
    )
    db_session.add(acct)
    db_session.flush()
    db_session.add(BackupPolicy(account_id=acct.id, destination_id=repo.id))
    db_session.commit()

    mock_restic.restore_snapshot.return_value = None
    mock_restic.list_snapshots.return_value = []

    # Explicit defaults (exercises the kwarg path; the bare-positional path is
    # already covered by the existing call-site at ui_backup.py).
    rec = recovery_service.create_recovery(
        db_session,
        acct.id,
        "snap-1",
        kind=RecoveryKind.persistent,
        ttl_minutes=None,
    )
    assert rec.kind == RecoveryKind.persistent
    assert rec.ttl_minutes is None


@patch("mailfallback.services.recovery_service.restic_service")
def test_create_recovery_can_be_ephemeral(mock_restic, db_session, tmp_store):
    repo = Repository(
        name="r",
        backend_type="local",
        local_path="/tmp/r",
        restic_password="secret",
    )
    db_session.add(repo)
    acct = Account(
        name="a",
        store=tmp_store,
        maildir_path=f"{tmp_store.path}/a",
        imap_host="imap.example.com",
    )
    db_session.add(acct)
    db_session.flush()
    db_session.add(BackupPolicy(account_id=acct.id, destination_id=repo.id))
    db_session.commit()

    mock_restic.restore_snapshot.return_value = None
    mock_restic.list_snapshots.return_value = []

    rec = recovery_service.create_recovery(
        db_session, acct.id, "snap-1", kind=RecoveryKind.ephemeral, ttl_minutes=15
    )
    assert rec.kind == RecoveryKind.ephemeral
    assert rec.ttl_minutes == 15


def test_namespace_prefix_uses_recovery_id_for_uniqueness(db_session, default_store):
    """Two Recoveries for the same snapshot must produce DIFFERENT namespace prefixes."""
    from datetime import UTC, datetime

    from mailfallback.models import Account, Recovery, RecoveryStatus
    from mailfallback.services.recovery_service import namespace_prefix

    acct = Account(
        name="koma",
        store=default_store,
        maildir_path="/data/mailboxes/k",
        imap_host="imap.example.com",
    )
    db_session.add(acct)
    db_session.commit()

    rec1 = Recovery(
        account_id=acct.id,
        snapshot_id="duplicate-snap",
        restore_path="/tmp/r1",
        status=RecoveryStatus.ready,
        restored_at=datetime(2026, 5, 11, 10, 0, tzinfo=UTC),
    )
    rec2 = Recovery(
        account_id=acct.id,
        snapshot_id="duplicate-snap",
        restore_path="/tmp/r2",
        status=RecoveryStatus.ready,
        restored_at=datetime(2026, 5, 11, 11, 0, tzinfo=UTC),
    )
    db_session.add_all([rec1, rec2])
    db_session.commit()

    p1 = namespace_prefix(rec1, "koma")
    p2 = namespace_prefix(rec2, "koma")

    assert p1.startswith("Recovery - koma (2026-05-11) [")
    assert p2.startswith("Recovery - koma (2026-05-11) [")
    assert p1 != p2  # the [<rec.id[:8]>] differentiator
    assert p1.endswith("/")
