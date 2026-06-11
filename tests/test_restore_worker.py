import imaplib
from unittest.mock import MagicMock, patch

import pytest

from mailfallback.models import Account, AuthType, JobStatus, RestoreJob, RestoreMode, User
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
    (e.g. "Recovery - name (2026-05-11) [snap-abc]/INBOX"), the destination
    APPEND must land in the bare folder ("INBOX"), not the Recovery-prefixed
    one. The prefix lives only on the source side (mounted snapshot)."""
    f = restore_job_fixtures
    mock_decrypt.return_value = "plaintext-pass"
    mock_create_temp.return_value = ("_restore_snap_test", "random-pass")

    namespaced_folder = "Recovery - source (2026-05-11) [snap-abc]/INBOX"

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

    def uid_side_effect(command, *args):
        if command == "SEARCH":
            return ("OK", [b"1"])
        return (
            "OK",
            [(b"1 (UID 1 RFC822 {50}", b"From: a@b.com\r\nSubject: SnapHit\r\n\r\nBody"), b")"],
        )

    src_conn.uid.side_effect = uid_side_effect
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
def test_execute_restore_to_origin_uses_uids_and_strips_live_namespace(
    mock_decrypt,
    mock_connect,
    mock_create_temp,
    mock_delete_temp,
    db_session,
    restore_job_fixtures,
):
    """Restore-to-origin (resolve-uids contract): selected_uids keys are the
    source account's live-namespaced IMAP paths and values are REAL UIDs.
    The worker must SELECT the key verbatim on the temp Dovecot user, filter
    via UID SEARCH / UID FETCH (sequence numbers diverge from UIDs on folders
    with expunge history), and APPEND to the bare folder on the target."""
    f = restore_job_fixtures
    mock_decrypt.return_value = "plaintext-pass"
    mock_create_temp.return_value = ("_restore_origin1", "random-pass")

    src = f["source"]
    live_key = f"{src.name} ({src.email_address}) [{src.id[-4:]}]/INBOX"

    job = f["job"]
    job.restore_mode = RestoreMode.selection
    job.selected_folders = None
    job.selected_uids = {live_key: ["7"]}
    job.folder_mapping = "original"
    db_session.commit()

    src_conn = MagicMock()
    tgt_conn = MagicMock()
    mock_connect.side_effect = [src_conn, tgt_conn]

    src_conn.select.return_value = ("OK", [b"3"])

    def uid_side_effect(command, *args):
        if command == "SEARCH":
            # Three messages: sequence numbers 1-3, UIDs sparse (2, 7, 9).
            # The selected message is UID 7 = sequence 2: a seq-based filter
            # would restore the wrong message (or none at all).
            return ("OK", [b"2 7 9"])
        assert command == "FETCH"
        assert args[0] == "7", f"expected UID FETCH 7, got {args}"
        return (
            "OK",
            [(b"2 (UID 7 RFC822 {50}", b"From: a@b.com\r\nSubject: Origin\r\n\r\nBody"), b")"],
        )

    src_conn.uid.side_effect = uid_side_effect

    tgt_conn.list.return_value = ("OK", [b'(\\Noselect) "/" ""'])
    tgt_conn.append.return_value = ("OK", [b"APPEND completed"])

    execute_restore_job(db_session, job.id)

    db_session.refresh(job)
    assert job.status == JobStatus.completed, f"job error: {job.error}"
    assert job.restored_messages == 1
    assert job.total_messages == 1
    # Source side: the namespaced key is SELECTed verbatim on the temp user.
    select_calls = [c.args[0] for c in src_conn.select.call_args_list]
    assert f'"{live_key}"' in select_calls, f"select calls: {select_calls}"
    # Selection mode must be UID-consistent: no sequence-number commands.
    src_conn.search.assert_not_called()
    src_conn.fetch.assert_not_called()
    # Destination side: live namespace prefix stripped — mail lands in INBOX,
    # not in a folder literally named after the namespace.
    args, _kwargs = tgt_conn.append.call_args
    assert args[0] == '"INBOX"', f"expected bare INBOX destination, got {args[0]!r}"


@patch("mailfallback.services.restore_worker._refresh_target_token")
@patch("mailfallback.services.restore_worker.delete_temp_imap_user")
@patch("mailfallback.services.restore_worker.create_temp_imap_user")
@patch("mailfallback.services.restore_worker.connect_imap")
@patch("mailfallback.services.restore_worker.decrypt_credentials")
def test_execute_restore_oauth2_target_uses_xoauth2(
    mock_decrypt,
    mock_connect,
    mock_create_temp,
    mock_delete_temp,
    mock_refresh,
    db_session,
    restore_job_fixtures,
):
    """Gmail/Microsoft reject an OAuth2 access token sent via plain LOGIN
    ([AUTHENTICATIONFAILED] Invalid credentials): oauth2 targets must connect
    with auth_method="xoauth2" — both the initial connection and any mid-job
    reconnect. The Dovecot source connection stays on plain login."""
    f = restore_job_fixtures
    f["target"].auth_type = AuthType.oauth2
    db_session.commit()

    mock_decrypt.return_value = '{"provider": "google", "refresh_token": "rt"}'
    mock_refresh.return_value = "ya29.fresh-token"
    mock_create_temp.return_value = ("_restore_oauth1", "random-pass")

    src_conn, tgt_conn = MagicMock(), MagicMock()
    src_conn2, tgt_conn2 = MagicMock(), MagicMock()
    mock_connect.side_effect = [src_conn, tgt_conn, src_conn2, tgt_conn2]

    for conn in (src_conn, src_conn2):
        conn.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "INBOX"'])
        conn.select.return_value = ("OK", [b"2"])
        conn.search.return_value = ("OK", [b"1 2"])

    # First FETCH drops the connection mid-job to force the reconnect path.
    src_conn.fetch.side_effect = imaplib.IMAP4.abort("connection dropped")
    src_conn2.fetch.side_effect = [
        ("OK", [(b"1 (RFC822 {100}", b"From: a@b.com\r\nSubject: T1\r\n\r\nB1"), b")"]),
        ("OK", [(b"2 (RFC822 {100}", b"From: c@d.com\r\nSubject: T2\r\n\r\nB2"), b")"]),
    ]
    for conn in (tgt_conn, tgt_conn2):
        conn.append.return_value = ("OK", [b"APPEND completed"])

    execute_restore_job(db_session, f["job"].id)

    db_session.refresh(f["job"])
    assert f["job"].status == JobStatus.completed, f"job error: {f['job'].error}"
    assert f["job"].restored_messages == 2
    mock_refresh.assert_called_once()

    assert mock_connect.call_count == 4, mock_connect.call_args_list
    calls = mock_connect.call_args_list

    def _password_of(call):
        return call.args[4] if len(call.args) > 4 else call.kwargs.get("password")

    # Calls 0 and 2: Dovecot source (initial + reconnect) — plain login.
    for i in (0, 2):
        assert calls[i].kwargs.get("auth_method", "login") == "login", (
            f"source connection {i} must keep plain login: {calls[i]}"
        )
    # Calls 1 and 3: oauth2 target (initial + reconnect) — XOAUTH2 with the
    # freshly refreshed access token.
    for i in (1, 3):
        assert calls[i].kwargs.get("auth_method") == "xoauth2", (
            f"target connection {i} must use XOAUTH2: {calls[i]}"
        )
        assert _password_of(calls[i]) == "ya29.fresh-token", calls[i]


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
