from unittest.mock import MagicMock, patch

import pytest

from mailfallback.models import Account, JobStatus, RestoreJob, RestoreMode, User
from mailfallback.services.restore_worker import execute_restore_job


@pytest.fixture
def restore_job_fixtures(db_session, default_store):
    user = User(username="worker_test", password_hash="x", store_id=default_store.id)
    db_session.add(user)
    db_session.flush()

    src = Account(
        name="source",
        email_address="src@example.com",
        imap_host="imap.src.com",
        imap_port=993,
        maildir_path="/data/mailboxes/src",
        store_id=default_store.id,
        credentials="encrypted",
    )
    tgt = Account(
        name="target",
        email_address="tgt@example.com",
        imap_host="imap.tgt.com",
        imap_port=993,
        maildir_path="/data/mailboxes/tgt",
        store_id=default_store.id,
        credentials="encrypted",
    )
    db_session.add_all([src, tgt])
    db_session.flush()
    src.owners.append(user)
    db_session.commit()

    job = RestoreJob(
        source_account_id=src.id,
        target_account_id=tgt.id,
        restore_mode=RestoreMode.folder,
        selected_folders=["INBOX"],
        skip_duplicates=False,
        requested_by=user.id,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return {"job": job, "user": user, "source": src, "target": tgt}


@patch("mailfallback.services.restore_worker.delete_temp_imap_user")
@patch("mailfallback.services.restore_worker.create_temp_imap_user")
@patch("mailfallback.services.restore_worker.connect_imap")
@patch("mailfallback.services.restore_worker.decrypt_credentials")
def test_execute_restore_folder(
    mock_decrypt, mock_connect, mock_create_temp, mock_delete_temp, db_session, restore_job_fixtures
):
    f = restore_job_fixtures
    mock_decrypt.return_value = "plaintext-pass"
    mock_create_temp.return_value = ("_restore_test1234", "random-pass")

    src_conn = MagicMock()
    tgt_conn = MagicMock()
    mock_connect.side_effect = [src_conn, tgt_conn]

    src_conn.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "INBOX"'])
    src_conn.select.return_value = ("OK", [b"2"])
    src_conn.search.return_value = ("OK", [b"1 2"])
    src_conn.fetch.side_effect = [
        ("OK", [(b"1 (RFC822 {100}", b"From: a@b.com\r\nSubject: Test1\r\n\r\nBody1"), b")"]),
        ("OK", [(b"2 (RFC822 {100}", b"From: c@d.com\r\nSubject: Test2\r\n\r\nBody2"), b")"]),
    ]

    tgt_conn.append.return_value = ("OK", [b"APPEND completed"])

    execute_restore_job(db_session, f["job"].id)

    db_session.refresh(f["job"])
    assert f["job"].status == JobStatus.completed
    assert f["job"].restored_messages == 2
    assert f["job"].total_messages == 2
    assert tgt_conn.append.call_count == 2
    mock_create_temp.assert_called_once()
    mock_delete_temp.assert_called_once_with(db_session, "_restore_test1234")


@patch("mailfallback.services.restore_worker.connect_imap")
@patch("mailfallback.services.restore_worker.decrypt_credentials")
def test_execute_restore_job_not_found(mock_decrypt, mock_connect, db_session):
    execute_restore_job(db_session, "nonexistent-id")
    mock_connect.assert_not_called()


@patch("mailfallback.services.restore_worker.delete_temp_imap_user")
@patch("mailfallback.services.restore_worker.create_temp_imap_user")
@patch("mailfallback.services.restore_worker.connect_imap")
@patch("mailfallback.services.restore_worker.decrypt_credentials")
def test_execute_restore_from_snapshot_namespace_strips_prefix(
    mock_decrypt,
    mock_connect,
    mock_create_temp,
    mock_delete_temp,
    db_session,
    restore_job_fixtures,
):
    """Bug 2 regression: when selected_uids keys are Recovery-namespaced
    (e.g. "Recovery — name (2026-05-11) [snap-abc]/INBOX"), the destination
    APPEND must land in the bare folder ("INBOX"), not the Recovery-prefixed
    one. The prefix lives only on the source side (mounted snapshot)."""
    f = restore_job_fixtures
    mock_decrypt.return_value = "plaintext-pass"
    mock_create_temp.return_value = ("_restore_snap_test", "random-pass")

    namespaced_folder = "Recovery — source (2026-05-11) [snap-abc]/INBOX"

    # Reconfigure the existing job for selection-mode with a snapshot key.
    job = f["job"]
    job.restore_mode = RestoreMode.selection
    job.selected_folders = None
    job.selected_uids = {namespaced_folder: ["1"]}
    job.folder_mapping = "original"
    db_session.commit()

    src_conn = MagicMock()
    tgt_conn = MagicMock()
    mock_connect.side_effect = [src_conn, tgt_conn]

    src_conn.select.return_value = ("OK", [b"1"])
    src_conn.search.return_value = ("OK", [b"1"])
    src_conn.fetch.return_value = (
        "OK",
        [(b"1 (RFC822 {50}", b"From: a@b.com\r\nSubject: SnapHit\r\n\r\nBody"), b")"],
    )
    # _get_hierarchy_separator -> "/"
    tgt_conn.list.return_value = ("OK", [b'(\\Noselect) "/" ""'])
    tgt_conn.append.return_value = ("OK", [b"APPEND completed"])

    execute_restore_job(db_session, job.id)

    db_session.refresh(job)
    assert job.status == JobStatus.completed, f"job error: {job.error}"
    assert job.restored_messages == 1
    assert tgt_conn.append.call_count == 1
    # The append call's first positional arg is the quoted target folder.
    args, _kwargs = tgt_conn.append.call_args
    target_folder_arg = args[0]
    assert target_folder_arg == '"INBOX"', (
        f"expected destination folder to be bare INBOX, got {target_folder_arg!r}"
    )
    # And src_conn.select must have been called with the namespaced source
    # path so the worker actually traverses the mounted snapshot.
    select_calls = [c.args[0] for c in src_conn.select.call_args_list]
    assert any(namespaced_folder in s for s in select_calls), (
        f"src_conn.select never opened the namespaced folder; calls={select_calls}"
    )


@patch("mailfallback.services.restore_worker.delete_temp_imap_user")
@patch("mailfallback.services.restore_worker.create_temp_imap_user")
@patch("mailfallback.services.restore_worker.connect_imap")
@patch("mailfallback.services.restore_worker.decrypt_credentials")
def test_execute_restore_handles_append_failure(
    mock_decrypt,
    mock_connect,
    mock_create_temp,
    mock_delete_temp,
    db_session,
    restore_job_fixtures,
):
    f = restore_job_fixtures
    mock_decrypt.return_value = "plaintext-pass"
    mock_create_temp.return_value = ("_restore_test5678", "random-pass")

    src_conn = MagicMock()
    tgt_conn = MagicMock()
    mock_connect.side_effect = [src_conn, tgt_conn]

    src_conn.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "INBOX"'])
    src_conn.select.return_value = ("OK", [b"1"])
    src_conn.search.return_value = ("OK", [b"1"])
    src_conn.fetch.return_value = (
        "OK",
        [(b"1 (RFC822 {50}", b"From: a@b.com\r\nSubject: Fail\r\n\r\nBody"), b")"],
    )

    tgt_conn.append.return_value = ("NO", [b"Quota exceeded"])

    execute_restore_job(db_session, f["job"].id)

    db_session.refresh(f["job"])
    assert f["job"].status == JobStatus.failed
    assert f["job"].restored_messages == 0
    assert f["job"].failed_messages == 1
    assert "failed" in f["job"].error.lower()
    mock_delete_temp.assert_called_once()
