"""Tests for the restore workspace router (search across namespaces)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from mailfallback.models import (
    Account,
    BackupPolicy,
    Recovery,
    RecoveryKind,
    RecoveryStatus,
    Repository,
)
from mailfallback.routers.restore import _search_namespace_for_query


def test_search_namespace_returns_envelopes():
    conn = MagicMock()
    # SELECT
    conn.select.return_value = ("OK", [b"3"])
    # SEARCH returns three UIDs
    conn.uid.side_effect = [
        ("OK", [b"1 2 3"]),
        # FETCH for each UID — returns minimal envelope tuple
        ("OK", [(b"1 (UID 1 ENVELOPE (...))", b"Subject: hi\r\nFrom: a@b\r\n\r\n")]),
        ("OK", [(b"2 (UID 2 ENVELOPE (...))", b"Subject: hello\r\nFrom: c@d\r\n\r\n")]),
        ("OK", [(b"3 (UID 3 ENVELOPE (...))", b"Subject: bye\r\nFrom: e@f\r\n\r\n")]),
    ]

    hits = _search_namespace_for_query(conn, namespace="snap-abc/", query="hi")

    assert len(hits) == 3
    assert all(h["namespace"] == "snap-abc/" for h in hits)
    assert all(h["folder"] == "INBOX" for h in hits)
    subjects = {h["subject"] for h in hits}
    assert subjects == {"hi", "hello", "bye"}
    uids = {h["uid"] for h in hits}
    assert uids == {"1", "2", "3"}


def test_search_namespace_empty_search_result():
    conn = MagicMock()
    conn.select.return_value = ("OK", [b"0"])
    conn.uid.return_value = ("OK", [b""])

    hits = _search_namespace_for_query(conn, namespace="snap-abc/", query="nope")

    assert hits == []


def test_search_namespace_select_fails():
    conn = MagicMock()
    conn.select.return_value = ("NO", [b"folder missing"])

    hits = _search_namespace_for_query(conn, namespace="missing/", query="anything")

    assert hits == []


def test_search_namespace_empty_namespace_targets_folder_only():
    """An empty namespace (live mailbox) should target just the folder."""
    conn = MagicMock()
    conn.select.return_value = ("OK", [b"1"])
    conn.uid.side_effect = [
        ("OK", [b"1"]),
        ("OK", [(b"1 (UID 1 ENVELOPE (...))", b"Subject: live\r\nFrom: x@y\r\n\r\n")]),
    ]

    hits = _search_namespace_for_query(conn, namespace="", query="live")

    assert len(hits) == 1
    assert hits[0]["namespace"] == ""
    assert hits[0]["folder"] == "INBOX"
    # SELECT should have been called with just "INBOX"
    conn.select.assert_called_once_with('"INBOX"', readonly=True)


def test_search_namespace_quotes_multi_word_query():
    """Verify SUBJECT criteria is properly quoted for multi-word queries."""
    conn = MagicMock()
    conn.select.return_value = ("OK", [b"0"])
    conn.uid.return_value = ("OK", [b""])

    _search_namespace_for_query(conn, namespace="", query="hello world")

    # The SEARCH should pass the query as a SINGLE quoted token
    call_args = conn.uid.call_args
    assert call_args.args[0] == "SEARCH"
    # The criteria should include the query in quotes (one way or another)
    serialised = " ".join(str(a) for a in call_args.args)
    assert '"hello world"' in serialised


def test_search_namespace_searches_subject_or_from():
    """Verify SEARCH covers both Subject and From per spec."""
    conn = MagicMock()
    conn.select.return_value = ("OK", [b"0"])
    conn.uid.return_value = ("OK", [b""])

    _search_namespace_for_query(conn, namespace="", query="alice")

    call_args = conn.uid.call_args
    serialised = " ".join(str(a) for a in call_args.args)
    # Either OR SUBJECT FROM or two separate criteria — must mention FROM
    assert "FROM" in serialised
    assert "SUBJECT" in serialised


@patch("mailfallback.routers.restore.delete_temp_imap_user")
@patch("mailfallback.routers.restore.mount_service")
@patch("mailfallback.routers.restore._connect_dovecot_for_account")
@patch("mailfallback.routers.restore.restic_service")
def test_workspace_search_dedup_by_message_id(
    mock_restic,
    mock_connect,
    mock_mount,
    mock_delete_user,
    client,
    db_session,
    default_store,
    login_user,
):
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
    acct.owners.append(login_user)
    db_session.add(BackupPolicy(account_id=acct.id, destination_id=repo.id))
    db_session.commit()

    # Authenticate as login_user (created by the fixture as koma/x)
    login_resp = client.post("/api/auth/login", json={"username": "koma", "password": "x"})
    assert login_resp.status_code == 200, login_resp.text

    mock_restic.list_snapshots.return_value = [
        {"short_id": "snap1", "time": (datetime.now(UTC) - timedelta(days=2)).isoformat()},
    ]

    fake_recovery = Recovery(
        account_id=acct.id,
        snapshot_id="snap1",
        restore_path="/tmp/r",
        status=RecoveryStatus.ready,
        kind=RecoveryKind.ephemeral,
    )
    db_session.add(fake_recovery)
    db_session.commit()
    mock_mount.ensure_mounted.return_value = fake_recovery

    fake_conn = MagicMock()
    fake_conn.select.return_value = ("OK", [b"1"])
    fake_conn.uid.side_effect = [
        # live SEARCH
        ("OK", [b"10"]),
        ("OK", [(b"...", b"Subject: x\r\nMessage-Id: <abc@host>\r\n\r\n")]),
        # snap SEARCH — same Message-Id
        ("OK", [b"99"]),
        ("OK", [(b"...", b"Subject: x\r\nMessage-Id: <abc@host>\r\n\r\n")]),
    ]
    mock_connect.return_value = (fake_conn, "_restore_testuser")

    resp = client.post(
        "/api/restore/workspace/search",
        json={
            "account_id": acct.id,
            "query": "x",
            "range_start": (datetime.now(UTC) - timedelta(days=7)).isoformat(),
            "range_end": datetime.now(UTC).isoformat(),
            "include_live": True,
            "include_snapshots": True,
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["results"]) == 1
    result = body["results"][0]
    locs = {loc["source"]: loc for loc in result["locations"]}
    assert "live" in locs and "snap1" in locs
    assert locs["live"]["uid"] == "10"
    assert locs["snap1"]["uid"] == "99"
    mock_delete_user.assert_called_once_with(db_session, "_restore_testuser")


def test_restore_workspace_renders(client, db_session, default_store, login_user):
    from mailfallback.models import Account, BackupPolicy, Repository

    repo = Repository(name="r", backend_type="local", local_path="/tmp/r", restic_password="x")
    db_session.add(repo)
    acct = Account(
        name="koma",
        store=default_store,
        maildir_path="/data/mailboxes/k",
        imap_host="imap.example.com",
    )
    db_session.add(acct)
    db_session.flush()
    acct.owners.append(login_user)
    db_session.add(BackupPolicy(account_id=acct.id, destination_id=repo.id))

    # Second account WITHOUT a backup policy — should still appear in the
    # Destination dropdown (e.g. a fresh mailbox being seeded from a snapshot).
    acct2 = Account(
        name="seed",
        store=default_store,
        maildir_path="/data/mailboxes/seed",
        imap_host="imap.example.com",
    )
    db_session.add(acct2)
    db_session.flush()
    acct2.owners.append(login_user)
    db_session.commit()

    # Authenticate (mirror the auth pattern from test_workspace_search_dedup_by_message_id).
    login_resp = client.post(
        "/api/auth/login",
        json={"username": "koma", "password": "x"},
    )
    assert login_resp.status_code in (200, 303)

    resp = client.get("/restore")
    assert resp.status_code == 200
    body = resp.content
    # Preset chips visible
    assert b'data-preset="single-mail"' in body
    assert b'data-preset="folder"' in body
    assert b'data-preset="full"' in body
    # Workspace marker
    assert b"workspace" in body.lower() or b"page-restore-workspace" in body
    # Destination dropdown lists ALL owned accounts, including the one
    # without a backup policy (Fix B).
    assert acct2.id.encode() in body


@patch("mailfallback.routers.restore.submit_restore_job")
@patch("mailfallback.routers.restore.create_restore_job")
def test_workspace_restore_post_to_existing_engine(
    mock_create_job, mock_submit, client, db_session, default_store, login_user
):
    repo = Repository(name="r", backend_type="local", local_path="/tmp/r", restic_password="x")
    db_session.add(repo)
    src = Account(
        name="src",
        store=default_store,
        maildir_path="/data/mailboxes/s",
        imap_host="imap.example.com",
    )
    dst = Account(
        name="dst",
        store=default_store,
        maildir_path="/data/mailboxes/d",
        imap_host="imap.example.com",
    )
    db_session.add_all([src, dst])
    db_session.flush()
    src.owners.append(login_user)
    dst.owners.append(login_user)
    db_session.add(BackupPolicy(account_id=src.id, destination_id=repo.id))
    db_session.commit()

    fake_job = MagicMock()
    fake_job.id = "j-1"
    fake_job.status.value = "pending"
    fake_job.source_account_id = src.id
    fake_job.target_account_id = dst.id
    fake_job.restore_mode.value = "selection"
    mock_create_job.return_value = fake_job

    # Authenticate
    login_resp = client.post("/api/auth/login", json={"username": "koma", "password": "x"})
    assert login_resp.status_code in (200, 303)

    resp = client.post(
        "/api/restore",
        json={
            "source_account_id": src.id,
            "target_account_id": dst.id,
            "restore_mode": "selection",
            "selected_uids": {"INBOX": ["10"]},
        },
    )
    assert resp.status_code == 200
    mock_create_job.assert_called_once()
    mock_submit.assert_called_once_with("j-1")
