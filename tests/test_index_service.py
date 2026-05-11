"""Tests for index_service — Mail Index lifecycle."""

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
