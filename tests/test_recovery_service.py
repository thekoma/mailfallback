"""Tests for recovery_service."""

import os
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


@patch("mailfallback.services.recovery_service.restic_service")
def test_create_recovery_from_attached_source(mock_restic, db_session, tmp_store):
    """An attachment source restores from its own prefix, ignoring BackupPolicy.

    The account deliberately has NO BackupPolicy: an orphan prefix attached to
    a policy-less account must still be restorable.
    """
    repo = Repository(
        name="ghost-repo",
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
    db_session.commit()

    mock_restic.restore_snapshot.return_value = {"snapshot_id": "ab12"}
    mock_restic.list_snapshots.return_value = [{"short_id": "ab12", "time": "2026-06-01T00:00:00Z"}]

    rec = recovery_service.create_recovery(
        db_session,
        acct.id,
        "ab12",
        source_repository=repo,
        source_prefix="old-ghost-prefix",
    )

    assert rec.status.value == "ready"
    assert rec.repository_id == repo.id
    args = mock_restic.restore_snapshot.call_args.args
    assert args[1] == "old-ghost-prefix"
    # Snapshot metadata lookup must also use the attachment prefix.
    list_args = mock_restic.list_snapshots.call_args.args
    assert list_args[1] == "old-ghost-prefix"


@patch("mailfallback.services.recovery_service.restic_service")
def test_create_recovery_without_source_still_requires_policy(mock_restic, db_session, tmp_store):
    """No source kwargs + no BackupPolicy keeps raising ValueError."""
    acct = Account(
        name="a",
        store=tmp_store,
        maildir_path=f"{tmp_store.path}/a",
        imap_host="imap.example.com",
    )
    db_session.add(acct)
    db_session.commit()

    with pytest.raises(ValueError, match="no backup policy"):
        recovery_service.create_recovery(db_session, acct.id, "ab12")


def _mk_policyless_account(db_session, tmp_store):
    repo = Repository(
        name="ghost-repo",
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
    db_session.commit()
    return repo, acct


def _materialize_foreign_tree(target_path, dirname):
    """Simulate restic restore of a LAYOUT=fs maildir from another instance."""
    base = os.path.join(target_path, "data", "mailboxes", dirname)
    for folder in ("INBOX", "Sent", os.path.join("Archive", "2025")):
        for sub in ("cur", "new", "tmp"):
            os.makedirs(os.path.join(base, folder, sub), exist_ok=True)
    with open(os.path.join(base, "INBOX", "cur", "msg1:2,S"), "w") as f:
        f.write("Subject: hi\n")
    return base


@patch("mailfallback.services.recovery_service.restic_service")
def test_attached_restore_resolves_foreign_maildir_root(mock_restic, db_session, tmp_store):
    """restore_path must be the foreign maildir ROOT (basename == prefix),
    not the first folder containing cur/new/tmp (e.g. INBOX)."""
    repo, acct = _mk_policyless_account(db_session, tmp_store)

    def materialize(destination, prefix, snapshot_id, target_path):
        _materialize_foreign_tree(target_path, "old-ghost-prefix")

    mock_restic.restore_snapshot.side_effect = materialize
    mock_restic.list_snapshots.return_value = []

    rec = recovery_service.create_recovery(
        db_session,
        acct.id,
        "ab12",
        source_repository=repo,
        source_prefix="old-ghost-prefix",
    )

    assert rec.status.value == "ready"
    assert rec.restore_path.endswith("old-ghost-prefix")
    assert os.path.isdir(os.path.join(rec.restore_path, "INBOX", "cur"))


@patch("mailfallback.services.recovery_service.restic_service")
def test_attached_restore_fallback_uses_commonpath(mock_restic, db_session, tmp_store):
    """When no directory matches the prefix, the fallback must return the
    common parent of all mail folders (the maildir root), not INBOX."""
    repo, acct = _mk_policyless_account(db_session, tmp_store)

    def materialize(destination, prefix, snapshot_id, target_path):
        # Directory name does NOT match the restic prefix.
        _materialize_foreign_tree(target_path, "renamed-dir")

    mock_restic.restore_snapshot.side_effect = materialize
    mock_restic.list_snapshots.return_value = []

    rec = recovery_service.create_recovery(
        db_session,
        acct.id,
        "ab12",
        source_repository=repo,
        source_prefix="old-ghost-prefix",
    )

    assert rec.status.value == "ready"
    assert rec.restore_path.endswith("renamed-dir")
    assert os.path.isdir(os.path.join(rec.restore_path, "INBOX", "cur"))
    assert os.path.isdir(os.path.join(rec.restore_path, "Archive", "2025", "cur"))


def test_create_recovery_rejects_partial_source_kwargs(db_session, tmp_store):
    repo, acct = _mk_policyless_account(db_session, tmp_store)

    with pytest.raises(ValueError, match="provided together"):
        recovery_service.create_recovery(db_session, acct.id, "ab12", source_repository=repo)
    with pytest.raises(ValueError, match="provided together"):
        recovery_service.create_recovery(
            db_session, acct.id, "ab12", source_prefix="old-ghost-prefix"
        )
