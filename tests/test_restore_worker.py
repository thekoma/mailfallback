import hashlib
import imaplib
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from mailfallback.models import (
    Account,
    AuthType,
    JobStatus,
    MailStore,
    RestoreJob,
    RestoreMode,
    StagingArea,
    StagingMessage,
    User,
)
from mailfallback.services import staging_service
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


# ---------------------------------------------------------------------------
# staging_push mode — push staged files from the requester's staging Maildir
# to the target upstream (no Dovecot source connection, no temp user).
# ---------------------------------------------------------------------------


@pytest.fixture
def staging_push_fixtures(db_session, tmp_path):
    """User on a REAL on-disk store (staging_dir writes under it) + a target
    account with credentials. Staged rows/files are built per test."""
    store = MailStore(name="push-store", path=str(tmp_path / "store"))
    db_session.add(store)
    db_session.flush()
    user = User(username="pusher", password_hash="x", store_id=store.id)
    db_session.add(user)
    db_session.flush()
    acc = Account(
        name="origin",
        email_address="orig@example.com",
        imap_host="imap.orig.com",
        imap_port=993,
        maildir_path=str(tmp_path / "mail-origin"),
        store_id=store.id,
        credentials="encrypted",
    )
    db_session.add(acc)
    db_session.commit()
    return {"user": user, "account": acc, "store": store}


def _stage_file(db_session, user, account, msgid, fname, folder="INBOX", disk_fname=None):
    """Write a staged message file + its StagingMessage row (area on demand).

    disk_fname lets a test simulate a Dovecot flag rename: the row (and any
    manifest built from it) keeps `fname` while the file on disk has flags."""
    sdir = staging_service.staging_dir(user)
    os.makedirs(os.path.join(sdir, "cur"), exist_ok=True)
    raw = f"Message-ID: {msgid}\r\nFrom: a@b.c\r\nSubject: staged\r\n\r\nbody".encode()
    with open(os.path.join(sdir, "cur", disk_fname or fname), "wb") as f:
        f.write(raw)
    area = db_session.query(StagingArea).filter_by(user_id=user.id).first()
    if area is None:
        area = StagingArea(user_id=user.id, expires_at=datetime.now(UTC) + timedelta(hours=1))
        db_session.add(area)
        db_session.flush()
    db_session.add(
        StagingMessage(
            staging_id=area.id,
            source_account_id=account.id,
            message_id_hash=hashlib.sha1(msgid.encode(), usedforsecurity=False).digest(),
            original_folder=folder,
            staged_filename=fname,
            size_bytes=len(raw),
        )
    )
    area.bytes_used += len(raw)
    db_session.commit()
    return raw


def _mk_push_job(db_session, user, account, manifest, skip_duplicates=False):
    job = RestoreJob(
        source_account_id=account.id,
        target_account_id=account.id,
        restore_mode=RestoreMode.staging_push,
        selected_uids=manifest,
        skip_duplicates=skip_duplicates,
        requested_by=user.id,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def _push_conn():
    conn = MagicMock()
    conn.list.return_value = ("OK", [b'(\\Noselect) "/" ""'])  # separator "/"
    conn.select.return_value = ("OK", [b"0"])
    conn.append.return_value = ("OK", [b"APPEND completed"])
    return conn


@patch("mailfallback.services.restore_worker.delete_temp_imap_user")
@patch("mailfallback.services.restore_worker.create_temp_imap_user")
@patch("mailfallback.services.restore_worker.connect_imap")
@patch("mailfallback.services.restore_worker.decrypt_credentials")
def test_execute_staging_push_appends_and_cleans_up(
    mock_decrypt,
    mock_connect,
    mock_create_temp,
    mock_delete_temp,
    db_session,
    staging_push_fixtures,
):
    """selected_uids is the file manifest {destination_folder: [filename]}:
    each staged file is APPENDed into its manifest folder on the target with
    NO source IMAP connection. Pushed rows + files are removed, bytes_used
    drops, and the now-empty area dies together with its directory."""
    f = staging_push_fixtures
    mock_decrypt.return_value = "plaintext-pass"
    raw1 = _stage_file(db_session, f["user"], f["account"], "<p1@x>", "100.aaaa.h:2,")
    raw2 = _stage_file(db_session, f["user"], f["account"], "<p2@x>", "101.bbbb.h:2,")
    manifest = {"INBOX": ["100.aaaa.h:2,"], "Restored/2026-06-11": ["101.bbbb.h:2,"]}
    job = _mk_push_job(db_session, f["user"], f["account"], manifest)

    tgt_conn = _push_conn()
    mock_connect.side_effect = [tgt_conn]  # a 2nd connect would StopIteration

    execute_restore_job(db_session, job.id)

    db_session.refresh(job)
    assert job.status == JobStatus.completed, f"job error: {job.error}"
    assert job.total_messages == 2
    assert job.restored_messages == 2
    assert job.skipped_messages == 0
    assert job.failed_messages == 0
    # One APPEND per file, each into its manifest folder, body = staged bytes.
    by_folder = {c.args[0]: c.args[3] for c in tgt_conn.append.call_args_list}
    assert by_folder == {'"INBOX"': raw1, '"Restored/2026-06-11"': raw2}
    # No source-side plumbing: a single IMAP connection (the target) and no
    # temp Dovecot user. skip_duplicates=False must not scan the folder.
    assert mock_connect.call_count == 1
    assert mock_connect.call_args.args[0] == "imap.orig.com"
    mock_create_temp.assert_not_called()
    mock_delete_temp.assert_not_called()
    tgt_conn.search.assert_not_called()
    # Pushed rows + files gone; the empty area died with its directory.
    assert db_session.query(StagingMessage).count() == 0
    assert db_session.query(StagingArea).count() == 0
    assert not os.path.isdir(staging_service.staging_dir(f["user"]))


@patch("mailfallback.services.restore_worker.delete_temp_imap_user")
@patch("mailfallback.services.restore_worker.create_temp_imap_user")
@patch("mailfallback.services.restore_worker.connect_imap")
@patch("mailfallback.services.restore_worker.decrypt_credentials")
def test_staging_push_partial_manifest_leaves_area_alive(
    mock_decrypt,
    mock_connect,
    mock_create_temp,
    mock_delete_temp,
    db_session,
    staging_push_fixtures,
):
    """Only manifest files are pushed and cleaned: rows staged after the push
    click survive, bytes_used shrinks by exactly the pushed sizes, and the
    area (still holding messages) must NOT be deleted."""
    f = staging_push_fixtures
    mock_decrypt.return_value = "plaintext-pass"
    _stage_file(db_session, f["user"], f["account"], "<q1@x>", "100.aaaa.h:2,")
    raw2 = _stage_file(db_session, f["user"], f["account"], "<q2@x>", "101.bbbb.h:2,")
    job = _mk_push_job(db_session, f["user"], f["account"], {"INBOX": ["100.aaaa.h:2,"]})

    mock_connect.side_effect = [_push_conn()]

    execute_restore_job(db_session, job.id)

    db_session.refresh(job)
    assert job.status == JobStatus.completed, f"job error: {job.error}"
    assert job.restored_messages == 1
    remaining = db_session.query(StagingMessage).one()
    assert remaining.staged_filename == "101.bbbb.h:2,"
    area = db_session.query(StagingArea).one()
    assert area.bytes_used == len(raw2)
    cur = os.path.join(staging_service.staging_dir(f["user"]), "cur")
    assert sorted(os.listdir(cur)) == ["101.bbbb.h:2,"]


@patch("mailfallback.services.restore_worker.delete_temp_imap_user")
@patch("mailfallback.services.restore_worker.create_temp_imap_user")
@patch("mailfallback.services.restore_worker.connect_imap")
@patch("mailfallback.services.restore_worker.decrypt_credentials")
def test_staging_push_skip_duplicates(
    mock_decrypt,
    mock_connect,
    mock_create_temp,
    mock_delete_temp,
    db_session,
    staging_push_fixtures,
):
    """skip_duplicates reuses the existing Message-Id mechanism (folder scan
    via _get_existing_message_ids + header membership). A duplicate counts as
    done: skipped counter, file + row cleaned up like a delivered one."""
    f = staging_push_fixtures
    mock_decrypt.return_value = "plaintext-pass"
    _stage_file(db_session, f["user"], f["account"], "<dup@x>", "100.aaaa.h:2,")
    raw_new = _stage_file(db_session, f["user"], f["account"], "<new@x>", "101.bbbb.h:2,")
    manifest = {"INBOX": ["100.aaaa.h:2,", "101.bbbb.h:2,"]}
    job = _mk_push_job(db_session, f["user"], f["account"], manifest, skip_duplicates=True)

    tgt_conn = _push_conn()
    # The target INBOX already holds <dup@x> — the existing-ids scan finds it.
    tgt_conn.search.return_value = ("OK", [b"1"])
    tgt_conn.fetch.return_value = (
        "OK",
        [(b"1 (BODY[HEADER.FIELDS (MESSAGE-ID)] {30}", b"Message-ID: <dup@x>\r\n\r\n"), b")"],
    )
    mock_connect.side_effect = [tgt_conn]

    execute_restore_job(db_session, job.id)

    db_session.refresh(job)
    assert job.status == JobStatus.completed, f"job error: {job.error}"
    assert job.restored_messages == 1
    assert job.skipped_messages == 1
    assert job.failed_messages == 0
    # Only the new message was APPENDed.
    assert tgt_conn.append.call_count == 1
    assert tgt_conn.append.call_args.args[3] == raw_new
    # Both files count as pushed (delivered-or-duplicate): everything cleaned.
    assert db_session.query(StagingMessage).count() == 0
    assert db_session.query(StagingArea).count() == 0
    assert not os.path.isdir(staging_service.staging_dir(f["user"]))


@patch("time.sleep")
@patch("mailfallback.services.restore_worker.delete_temp_imap_user")
@patch("mailfallback.services.restore_worker.create_temp_imap_user")
@patch("mailfallback.services.restore_worker.connect_imap")
@patch("mailfallback.services.restore_worker.decrypt_credentials")
def test_staging_push_failure_keeps_staged(
    mock_decrypt,
    mock_connect,
    mock_create_temp,
    mock_delete_temp,
    _mock_sleep,
    db_session,
    staging_push_fixtures,
):
    """Persistent APPEND errors fail the job and leave rows + files + area
    INTACT — the staging area is the retry buffer."""
    f = staging_push_fixtures
    mock_decrypt.return_value = "plaintext-pass"
    _stage_file(db_session, f["user"], f["account"], "<f1@x>", "100.aaaa.h:2,")
    _stage_file(db_session, f["user"], f["account"], "<f2@x>", "101.bbbb.h:2,")
    manifest = {"INBOX": ["100.aaaa.h:2,", "101.bbbb.h:2,"]}
    job = _mk_push_job(db_session, f["user"], f["account"], manifest)

    tgt_conn = _push_conn()
    tgt_conn.append.side_effect = imaplib.IMAP4.error("APPEND rejected")
    mock_connect.side_effect = [tgt_conn]

    execute_restore_job(db_session, job.id)

    db_session.refresh(job)
    assert job.status == JobStatus.failed
    assert job.restored_messages == 0
    assert job.failed_messages == 2
    assert db_session.query(StagingMessage).count() == 2
    assert db_session.query(StagingArea).count() == 1
    cur = os.path.join(staging_service.staging_dir(f["user"]), "cur")
    assert sorted(os.listdir(cur)) == ["100.aaaa.h:2,", "101.bbbb.h:2,"]


@patch("mailfallback.services.restore_worker._refresh_target_token")
@patch("mailfallback.services.restore_worker.delete_temp_imap_user")
@patch("mailfallback.services.restore_worker.create_temp_imap_user")
@patch("mailfallback.services.restore_worker.connect_imap")
@patch("mailfallback.services.restore_worker.decrypt_credentials")
def test_staging_push_oauth2_target_uses_xoauth2(
    mock_decrypt,
    mock_connect,
    mock_create_temp,
    mock_delete_temp,
    mock_refresh,
    db_session,
    staging_push_fixtures,
):
    """An oauth2 target must be connected with auth_method="xoauth2" and the
    freshly refreshed access token — same contract as the selection path."""
    f = staging_push_fixtures
    f["account"].auth_type = AuthType.oauth2
    db_session.commit()
    mock_decrypt.return_value = '{"provider": "google", "refresh_token": "rt"}'
    mock_refresh.return_value = "ya29.fresh-token"
    _stage_file(db_session, f["user"], f["account"], "<o1@x>", "100.aaaa.h:2,")
    job = _mk_push_job(db_session, f["user"], f["account"], {"INBOX": ["100.aaaa.h:2,"]})

    mock_connect.side_effect = [_push_conn()]

    execute_restore_job(db_session, job.id)

    db_session.refresh(job)
    assert job.status == JobStatus.completed, f"job error: {job.error}"
    assert job.restored_messages == 1
    mock_refresh.assert_called_once()
    assert mock_connect.call_count == 1
    call = mock_connect.call_args
    assert call.kwargs.get("auth_method") == "xoauth2", call
    password = call.args[4] if len(call.args) > 4 else call.kwargs.get("password")
    assert password == "ya29.fresh-token"


@patch("mailfallback.services.restore_worker.delete_temp_imap_user")
@patch("mailfallback.services.restore_worker.create_temp_imap_user")
@patch("mailfallback.services.restore_worker.connect_imap")
@patch("mailfallback.services.restore_worker.decrypt_credentials")
def test_staging_push_finds_flag_renamed_file(
    mock_decrypt,
    mock_connect,
    mock_create_temp,
    mock_delete_temp,
    db_session,
    staging_push_fixtures,
):
    """A webmail read between push click and job run renames the file (flag
    suffix): the manifest name goes stale but the stable prefix still matches.
    The file must be pushed AND removed (by its actual name) afterwards."""
    f = staging_push_fixtures
    mock_decrypt.return_value = "plaintext-pass"
    raw = _stage_file(
        db_session,
        f["user"],
        f["account"],
        "<r1@x>",
        "100.cccc.h:2,",
        disk_fname="100.cccc.h:2,S",
    )
    job = _mk_push_job(db_session, f["user"], f["account"], {"INBOX": ["100.cccc.h:2,"]})

    tgt_conn = _push_conn()
    mock_connect.side_effect = [tgt_conn]

    execute_restore_job(db_session, job.id)

    db_session.refresh(job)
    assert job.status == JobStatus.completed, f"job error: {job.error}"
    assert job.restored_messages == 1
    assert job.skipped_messages == 0
    assert tgt_conn.append.call_args.args[3] == raw
    # The renamed file was cleaned up too — no orphan left behind.
    assert db_session.query(StagingMessage).count() == 0
    assert db_session.query(StagingArea).count() == 0
    assert not os.path.isdir(staging_service.staging_dir(f["user"]))
