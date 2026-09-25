"""Tests for index_service — Mail Index lifecycle."""

import os
import shutil
from pathlib import Path
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


def test_upsert_message_set_indexes_cc_and_bcc(db_session, maildir_account, tmp_path):
    (tmp_path / "INBOX" / "cur" / "1234567892.M3.host:2,S").write_bytes(
        b"From: me@example.com\r\n"
        b"Subject: Reply all\r\n"
        b"Message-Id: <cc@host>\r\n"
        b"To: one@example.com\r\n"
        b"Cc: Two <two@example.com>, three@example.com\r\n"
        b"Bcc: four@example.com\r\n"
        b"\r\n"
        b"body"
    )
    index_service.upsert_message_set(db_session, maildir_account.id)

    by_subject = {m.subject: m for m in db_session.query(MailIndexMessage).all()}
    row = by_subject["Reply all"]
    assert row.to_addrs == ["one@example.com"]
    assert row.cc_addrs == ["two@example.com", "three@example.com"]
    assert row.bcc_addrs == ["four@example.com"]
    assert row.recipients_indexed_at is not None
    # No Cc/Bcc header: NULL, not an empty list pretending we looked
    assert by_subject["Hello"].cc_addrs is None
    assert by_subject["Hello"].bcc_addrs is None


def test_upsert_message_set_reindexes_recipients_of_pre_cc_rows(
    db_session, maildir_account, tmp_path
):
    """Rows indexed before migration 029 carry only To. The next walk must
    re-read their headers — otherwise old mail keeps a partial recipient list
    forever, since the walk never reopens a file it already knows."""
    (tmp_path / "INBOX" / "cur" / "1234567892.M3.host:2,S").write_bytes(
        b"From: me@example.com\r\n"
        b"Subject: Old reply\r\n"
        b"Message-Id: <old@host>\r\n"
        b"To: one@example.com\r\n"
        b"Cc: two@example.com\r\n"
        b"\r\n"
        b"body"
    )
    index_service.upsert_message_set(db_session, maildir_account.id)
    # Simulate the pre-029 state of every row
    db_session.query(MailIndexMessage).update(
        {"cc_addrs": None, "bcc_addrs": None, "recipients_indexed_at": None}
    )
    db_session.commit()

    n = index_service.upsert_message_set(db_session, maildir_account.id)
    db_session.expire_all()

    assert n == 3  # every row re-read once
    rows = {m.subject: m for m in db_session.query(MailIndexMessage).all()}
    assert rows["Old reply"].cc_addrs == ["two@example.com"]
    assert all(r.recipients_indexed_at is not None for r in rows.values())
    # One-off: a third walk has nothing left to re-read
    assert index_service.upsert_message_set(db_session, maildir_account.id) == 0


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


def _locate_on_disk(maildir_path, folder_path, filename):
    """Absolute path of an indexed (folder, filename) coordinate on disk, or None."""
    for base in index_service.maildir_folder_bases(maildir_path, folder_path):
        for sub in ("cur", "new"):
            p = os.path.join(base, sub, filename)
            if os.path.exists(p):
                return p
    return None


def test_second_run_unchanged_maildir_writes_nothing(db_session, maildir_account, monkeypatch):
    index_service.upsert_message_set(db_session, maildir_account.id)
    rows_before = {
        r.message_id_hash: (r.folder_path, r.maildir_filename, r.last_seen_at, r.deleted_at)
        for r in db_session.query(index_service.MailIndexMessage).all()
    }
    parse_calls = []
    real_parse = index_service._parse_headers
    monkeypatch.setattr(
        index_service,
        "_parse_headers",
        lambda p: parse_calls.append(p) or real_parse(p),
    )
    touched = index_service.upsert_message_set(db_session, maildir_account.id)
    rows_after = {
        r.message_id_hash: (r.folder_path, r.maildir_filename, r.last_seen_at, r.deleted_at)
        for r in db_session.query(index_service.MailIndexMessage).all()
    }
    assert touched == 0
    assert parse_calls == []  # zero parses for known files
    assert rows_after == rows_before  # zero row writes (incl. last_seen_at)


def test_flag_rename_relocates_row(db_session, maildir_account):
    index_service.upsert_message_set(db_session, maildir_account.id)
    row = db_session.query(index_service.MailIndexMessage).first()
    # simulate mbsync flag rename: fn -> fn:2,S  (locate the file on disk first)
    src = _locate_on_disk(maildir_account.maildir_path, row.folder_path, row.maildir_filename)
    assert src is not None
    new_fn = row.maildir_filename + ":2,S"
    os.rename(src, os.path.join(os.path.dirname(src), new_fn))
    touched = index_service.upsert_message_set(db_session, maildir_account.id)
    db_session.refresh(row)
    assert touched == 1
    assert row.maildir_filename == new_fn
    assert row.deleted_at is None


def test_duplicate_copies_keep_stable_pointer(db_session, maildir_account):
    index_service.upsert_message_set(db_session, maildir_account.id)
    row = db_session.query(index_service.MailIndexMessage).first()
    orig = (row.folder_path, row.maildir_filename)
    # add a second on-disk copy of the same message in another folder (same Message-Id)
    src = _locate_on_disk(maildir_account.maildir_path, row.folder_path, row.maildir_filename)
    assert src is not None
    dup_dir = os.path.join(maildir_account.maildir_path, "Dup", "cur")
    os.makedirs(dup_dir, exist_ok=True)
    shutil.copy(src, os.path.join(dup_dir, "dupcopy"))
    index_service.upsert_message_set(db_session, maildir_account.id)
    db_session.refresh(row)
    assert (row.folder_path, row.maildir_filename) == orig  # pointer stable
    # delete the stored copy -> pointer must relocate to the surviving duplicate
    os.remove(src)
    index_service.upsert_message_set(db_session, maildir_account.id)
    db_session.refresh(row)
    assert (row.folder_path, row.maildir_filename) == ("Dup", "dupcopy")
    assert row.deleted_at is None


def test_reappearing_file_undeletes_row(db_session, maildir_account):
    index_service.upsert_message_set(db_session, maildir_account.id)
    row = db_session.query(index_service.MailIndexMessage).first()
    src = _locate_on_disk(maildir_account.maildir_path, row.folder_path, row.maildir_filename)
    assert src is not None
    # Stash the file OUTSIDE any cur/new dir so it truly leaves the walk (a
    # rename inside cur/ would look like a relocation, not a deletion).
    stash = os.path.join(maildir_account.maildir_path, ".stash")
    os.makedirs(stash, exist_ok=True)
    saved = os.path.join(stash, "saved")
    os.rename(src, saved)
    index_service.upsert_message_set(db_session, maildir_account.id)
    db_session.refresh(row)
    assert row.deleted_at is not None
    os.rename(saved, src)
    index_service.upsert_message_set(db_session, maildir_account.id)
    db_session.refresh(row)
    assert row.deleted_at is None


def test_parse_failure_on_known_file_does_not_soft_delete(db_session, maildir_account, monkeypatch):
    index_service.upsert_message_set(db_session, maildir_account.id)
    monkeypatch.setattr(index_service, "_parse_headers", lambda p: None)  # everything unparseable
    index_service.upsert_message_set(db_session, maildir_account.id)
    deleted = (
        db_session.query(index_service.MailIndexMessage)
        .filter(index_service.MailIndexMessage.deleted_at.isnot(None))
        .count()
    )
    assert deleted == 0  # known files are never re-parsed, so they stay alive


def test_incremental_new_message_inserted_without_touching_others(db_session, maildir_account):
    index_service.upsert_message_set(db_session, maildir_account.id)
    rows_before = {
        r.message_id_hash: (r.folder_path, r.maildir_filename, r.last_seen_at, r.deleted_at)
        for r in db_session.query(index_service.MailIndexMessage).all()
    }
    # Drop a single new message (distinct Message-Id) into the existing folder.
    inbox_cur = os.path.join(maildir_account.maildir_path, "INBOX", "cur")
    new_fn = "1234567892.M3.host:2,"
    with open(os.path.join(inbox_cur, new_fn), "wb") as f:
        f.write(
            b"From: dave@example.com\r\nSubject: Fresh\r\nMessage-Id: <ghi@host>\r\n\r\nnew body"
        )

    touched = index_service.upsert_message_set(db_session, maildir_account.id)
    assert touched == 1  # only the new row was written

    rows_after = {
        r.message_id_hash: (r.folder_path, r.maildir_filename, r.last_seen_at, r.deleted_at)
        for r in db_session.query(index_service.MailIndexMessage).all()
    }
    new_hash = index_service._hash_message_id("<ghi@host>")
    assert new_hash in rows_after and new_hash not in rows_before
    assert rows_after[new_hash][:2] == ("INBOX", new_fn)
    # Every pre-existing row is byte-for-byte unchanged (incl. last_seen_at).
    assert {h: v for h, v in rows_after.items() if h != new_hash} == rows_before


# Characterisation, not red-green: these two pin behaviour that already
# works. The design (#244 spec §7) leans on _walk_maildir being a plain
# os.walk that never consults mbsync's Patterns, so quarantined mail (moved
# under "Removed from Source/<name> (<timestamp>)") is indexed exactly like
# any other folder — if it weren't, it would also fall out of staging and
# restore. Both are expected to pass on the first run.
def test_quarantined_mail_is_indexed_under_its_new_folder_path(
    db_session, maildir_account, tmp_path
):
    q = tmp_path / "Removed from Source" / "push-dixie (2026-09-15 1430)" / "cur"
    q.mkdir(parents=True)
    (q / "1234567892.M3.host:2,S").write_bytes(
        b"From: dave@example.com\r\nSubject: Quarantined\r\n"
        b"Message-Id: <ghi@host>\r\n\r\nstill here"
    )

    index_service.upsert_message_set(db_session, maildir_account.id)

    msg = db_session.query(MailIndexMessage).filter(MailIndexMessage.subject == "Quarantined").one()
    assert msg.folder_path == "Removed from Source/push-dixie (2026-09-15 1430)"
    assert msg.deleted_at is None


def test_the_file_behind_a_quarantined_message_can_be_reconstructed(
    db_session, maildir_account, tmp_path
):
    """maildir_folder_bases is how staging and restore turn an index row back
    into bytes on disk. A container path with spaces and parentheses must
    survive that round trip."""
    folder = "Removed from Source/push-dixie (2026-09-15 1430)"
    (tmp_path / folder / "cur").mkdir(parents=True)
    (tmp_path / folder / "cur" / "1234567892.M3.host:2,S").write_bytes(
        b"Subject: Quarantined\r\nMessage-Id: <ghi@host>\r\n\r\nstill here"
    )
    index_service.upsert_message_set(db_session, maildir_account.id)

    bases = index_service.maildir_folder_bases(str(tmp_path), folder)

    assert bases == (str(tmp_path / folder),)
    assert (Path(bases[0]) / "cur" / "1234567892.M3.host:2,S").exists()
