"""Tests for index_service — Mail Index lifecycle."""

from unittest.mock import patch

import pytest

from mailfallback.models import Account, MailIndexMessage, MailIndexRebuildStatus
from mailfallback.services import index_service


@pytest.fixture
def maildir_account(db_session, default_store, tmp_path):
    """Account with a real on-disk Maildir at tmp_path."""
    acct = Account(
        name="a",
        store=default_store,
        maildir_path=str(tmp_path),
        imap_host="imap.example.com",
    )
    db_session.add(acct)
    db_session.commit()

    # Create INBOX/cur with two mails
    inbox_cur = tmp_path / "INBOX" / "cur"
    inbox_cur.mkdir(parents=True)

    (inbox_cur / "1234567890.M1.host:2,S").write_bytes(
        b"From: alice@example.com\r\n"
        b"Subject: Hello\r\n"
        b"Message-Id: <abc@host>\r\n"
        b"Date: Mon, 11 May 2026 12:00:00 +0000\r\n"
        b"To: bob@example.com\r\n"
        b"\r\n"
        b"body content here"
    )
    (inbox_cur / "1234567891.M2.host:2,").write_bytes(
        b"From: carol@example.com\r\nSubject: Howdy\r\nMessage-Id: <def@host>\r\n\r\nanother body"
    )
    return acct


def test_upsert_message_set_inserts_new_messages(db_session, maildir_account):
    n = index_service.upsert_message_set(db_session, maildir_account.id)
    assert n == 2

    msgs = (
        db_session.query(MailIndexMessage)
        .filter(MailIndexMessage.account_id == maildir_account.id)
        .all()
    )
    assert len(msgs) == 2
    by_subject = {m.subject: m for m in msgs}
    assert "Hello" in by_subject
    assert by_subject["Hello"].from_addr == "alice@example.com"
    assert by_subject["Hello"].to_addrs == ["bob@example.com"]
    assert by_subject["Hello"].folder_path == "INBOX"


def test_upsert_message_set_marks_missing_as_deleted(db_session, maildir_account, tmp_path):
    index_service.upsert_message_set(db_session, maildir_account.id)
    # Remove the second file
    (tmp_path / "INBOX" / "cur" / "1234567891.M2.host:2,").unlink()

    index_service.upsert_message_set(db_session, maildir_account.id)
    deleted = (
        db_session.query(MailIndexMessage)
        .filter(MailIndexMessage.account_id == maildir_account.id)
        .filter(MailIndexMessage.deleted_at.is_not(None))
        .all()
    )
    assert len(deleted) == 1
    assert deleted[0].subject == "Howdy"


def test_upsert_message_set_updates_rebuild_status_watermark(db_session, maildir_account):
    index_service.upsert_message_set(db_session, maildir_account.id)
    rs = (
        db_session.query(MailIndexRebuildStatus)
        .filter(MailIndexRebuildStatus.account_id == maildir_account.id)
        .one()
    )
    assert rs.state == "idle"
    assert rs.last_indexed_at is not None


def test_record_snapshot_inserts_bits_for_alive_messages(db_session, maildir_account):
    from mailfallback.models import SnapshotMessage

    index_service.upsert_message_set(db_session, maildir_account.id)
    n = index_service.record_snapshot(db_session, maildir_account.id, "snap00001")
    assert n == 2

    bits = (
        db_session.query(SnapshotMessage)
        .filter(
            SnapshotMessage.snapshot_id == "snap00001",
            SnapshotMessage.account_id == maildir_account.id,
        )
        .all()
    )
    assert len(bits) == 2


def test_record_snapshot_idempotent(db_session, maildir_account):
    from mailfallback.models import SnapshotMessage

    index_service.upsert_message_set(db_session, maildir_account.id)
    index_service.record_snapshot(db_session, maildir_account.id, "snap00001")
    n = index_service.record_snapshot(db_session, maildir_account.id, "snap00001")
    assert n == 0  # nothing new

    bits = (
        db_session.query(SnapshotMessage).filter(SnapshotMessage.snapshot_id == "snap00001").count()
    )
    assert bits == 2  # still 2, not 4


def test_record_snapshot_excludes_deleted_messages(db_session, maildir_account, tmp_path):
    from mailfallback.models import SnapshotMessage

    index_service.upsert_message_set(db_session, maildir_account.id)
    (tmp_path / "INBOX" / "cur" / "1234567891.M2.host:2,").unlink()
    index_service.upsert_message_set(db_session, maildir_account.id)
    # Now one message is deleted_at-set

    index_service.record_snapshot(db_session, maildir_account.id, "snap00002")
    bits = (
        db_session.query(SnapshotMessage).filter(SnapshotMessage.snapshot_id == "snap00002").count()
    )
    assert bits == 1  # only the alive one


def test_prune_snapshot_removes_only_target_snapshot_bits(db_session, maildir_account):
    from mailfallback.models import SnapshotMessage

    index_service.upsert_message_set(db_session, maildir_account.id)
    index_service.record_snapshot(db_session, maildir_account.id, "snapA")
    index_service.record_snapshot(db_session, maildir_account.id, "snapB")
    assert db_session.query(SnapshotMessage).count() == 4

    n = index_service.prune_snapshot(db_session, "snapA")
    assert n == 2

    remaining = db_session.query(SnapshotMessage).all()
    assert all(b.snapshot_id == "snapB" for b in remaining)


def test_prune_snapshot_idempotent_for_unknown_id(db_session):
    n = index_service.prune_snapshot(db_session, "nonexistent")
    assert n == 0


@patch("mailfallback.services.index_service.restic_service")
def test_backfill_snapshots_sets_bits_for_matched_filenames(
    mock_restic, db_session, maildir_account
):
    """For each existing restic snapshot, list files, match Maildir filenames
    against alive messages, bulk INSERT snapshot_messages."""
    from mailfallback.models import BackupPolicy, Repository, SnapshotMessage

    # Create a backup policy so the service can find the destination
    repo = Repository(name="r", backend_type="local", local_path="/tmp/r", restic_password="x")
    db_session.add(repo)
    db_session.flush()
    db_session.add(BackupPolicy(account_id=maildir_account.id, destination_id=repo.id))
    db_session.commit()

    # Walk live first so we have alive messages
    index_service.upsert_message_set(db_session, maildir_account.id)

    mock_restic.list_snapshots.return_value = [
        {"short_id": "snapXXXX", "time": "2026-05-01T10:00:00Z"},
    ]
    # Snapshot lists files matching one of the live filenames (the first mail
    # in maildir_account fixture)
    mock_restic.list_files.return_value = iter(
        [
            "/data/mailboxes/abc/INBOX/cur/1234567890.M1.host:2,S",  # matches first mail in fixture
        ]
    )

    list(index_service.backfill_snapshots(db_session, maildir_account.id))

    bits = db_session.query(SnapshotMessage).filter(SnapshotMessage.snapshot_id == "snapXXXX").all()
    assert len(bits) == 1


def test_upsert_message_set_batched_commits(db_session, default_store, tmp_path, monkeypatch):
    """Verify the walk commits in batches (not one giant transaction).

    We monkey-patch BATCH_SIZE to a small value, create more files than that,
    and assert db.commit() was called multiple times.
    """
    from mailfallback.models import Account
    from mailfallback.services import index_service

    # Tiny batch size for the test
    monkeypatch.setattr(index_service, "BATCH_SIZE", 2)

    acct = Account(
        name="a",
        store=default_store,
        maildir_path=str(tmp_path),
        imap_host="imap.example.com",
    )
    db_session.add(acct)
    db_session.commit()

    inbox_cur = tmp_path / "INBOX" / "cur"
    inbox_cur.mkdir(parents=True)
    # Create 5 mails — with BATCH_SIZE=2 we expect ≥2 commits during the walk
    for i in range(5):
        (inbox_cur / f"{i}.host:2,").write_bytes(
            f"From: a{i}@x\r\nSubject: s{i}\r\nMessage-Id: <{i}@h>\r\n\r\n".encode()
        )

    commit_count = [0]
    original_commit = db_session.commit

    def counting_commit():
        commit_count[0] += 1
        original_commit()

    monkeypatch.setattr(db_session, "commit", counting_commit)

    n = index_service.upsert_message_set(db_session, acct.id)
    assert n == 5
    # 5 messages / 2 per batch = 3 batches → at least 3 mid-walk commits
    # plus the final commits in the success path. We just assert >2.
    assert commit_count[0] >= 3


@patch("mailfallback.services.index_service.restic_service")
def test_backfill_snapshots_skips_already_processed(mock_restic, db_session, maildir_account):
    """Snapshots that already have rows in snapshot_messages should be skipped."""
    from mailfallback.models import BackupPolicy, Repository

    repo = Repository(name="r", backend_type="local", local_path="/tmp/r", restic_password="x")
    db_session.add(repo)
    db_session.flush()
    db_session.add(BackupPolicy(account_id=maildir_account.id, destination_id=repo.id))
    db_session.commit()

    # Walk live + record an existing snapshot manually (simulating prior backfill)
    index_service.upsert_message_set(db_session, maildir_account.id)
    index_service.record_snapshot(db_session, maildir_account.id, "snapDONE")

    # Now restic reports two snapshots: one already done, one new
    mock_restic.list_snapshots.return_value = [
        {"short_id": "snapDONE", "time": "2026-05-01T10:00:00Z"},
        {"short_id": "snapNEW", "time": "2026-05-02T10:00:00Z"},
    ]

    list_files_call_args = []

    def fake_list_files(dest, account_id, sid):
        list_files_call_args.append(sid)
        return iter([])

    mock_restic.list_files.side_effect = fake_list_files

    list(index_service.backfill_snapshots(db_session, maildir_account.id))

    # snapDONE should be SKIPPED — list_files only called for snapNEW
    assert "snapDONE" not in list_files_call_args
    assert "snapNEW" in list_files_call_args
