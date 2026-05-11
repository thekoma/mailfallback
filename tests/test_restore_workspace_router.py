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

    hits = _search_namespace_for_query(conn, namespace="snap-abc/", query="hi", folders=["INBOX"])

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

    hits = _search_namespace_for_query(conn, namespace="snap-abc/", query="nope", folders=["INBOX"])

    assert hits == []


def test_search_namespace_select_fails():
    conn = MagicMock()
    conn.select.return_value = ("NO", [b"folder missing"])

    hits = _search_namespace_for_query(
        conn, namespace="missing/", query="anything", folders=["INBOX"]
    )

    assert hits == []


def test_search_namespace_empty_namespace_targets_folder_only():
    """An empty namespace (live mailbox) should target just the folder."""
    conn = MagicMock()
    conn.select.return_value = ("OK", [b"1"])
    conn.uid.side_effect = [
        ("OK", [b"1"]),
        ("OK", [(b"1 (UID 1 ENVELOPE (...))", b"Subject: live\r\nFrom: x@y\r\n\r\n")]),
    ]

    hits = _search_namespace_for_query(conn, namespace="", query="live", folders=["INBOX"])

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

    _search_namespace_for_query(conn, namespace="", query="hello world", folders=["INBOX"])

    # The SEARCH should pass the query as a SINGLE quoted token
    call_args = conn.uid.call_args
    assert call_args.args[0] == "SEARCH"
    # The criteria should include the query in quotes (one way or another)
    serialised = " ".join(str(a) for a in call_args.args)
    assert '"hello world"' in serialised


def test_search_namespace_searches_subject_or_from():
    """Verify SEARCH covers both Subject and From when both are requested."""
    conn = MagicMock()
    conn.select.return_value = ("OK", [b"0"])
    conn.uid.return_value = ("OK", [b""])

    _search_namespace_for_query(
        conn,
        namespace="",
        query="alice",
        folders=["INBOX"],
        criteria_fields=["SUBJECT", "FROM"],
    )

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
    # No-op LIST so _list_namespace_folders falls back to ["INBOX"] for every namespace.
    fake_conn.list.return_value = ("OK", [])
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
    # Alpine.js component bootstrapping
    assert b'x-data="restoreWorkspace()"' in body
    # We no longer use data-preset attributes — Alpine drives state via x-data
    assert b"data-preset" not in body
    # Workspace marker
    assert b"page-restore-workspace" in body
    # Destination dropdown lists ALL owned accounts, including the one
    # without a backup policy (Fix B).
    assert acct2.id.encode() in body
    # Alpine CDN script tag is present (defer-loaded before workspace.js)
    assert b"alpinejs" in body
    # Workspace JS factory loaded
    assert b"restore_workspace.js" in body
    # flatpickr inline calendar input (replaces the old dual-handle slider).
    assert b'id="ws-calendar-input"' in body
    assert b"flatpickr" in body


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


def test_search_namespace_body_includes_body_criterion():
    """Verify SEARCH includes BODY when search_body=True (legacy compat path)."""
    conn = MagicMock()
    conn.select.return_value = ("OK", [b"0"])
    conn.uid.return_value = ("OK", [b""])

    _search_namespace_for_query(
        conn,
        namespace="",
        query="alice",
        folders=["INBOX"],
        criteria_fields=["SUBJECT", "FROM"],
        search_body=True,
    )

    call_args = conn.uid.call_args
    serialised = " ".join(str(a) for a in call_args.args)
    assert "BODY" in serialised
    assert "SUBJECT" in serialised
    assert "FROM" in serialised


def test_search_namespace_uses_type_filter_and_multiple_criteria():
    """Verify SEARCH builds the right OR chain with type prefix."""
    conn = MagicMock()
    conn.select.return_value = ("OK", [b"0"])
    conn.uid.return_value = ("OK", [b""])

    _search_namespace_for_query(
        conn,
        namespace="",
        query="bob",
        folders=["INBOX"],
        criteria_fields=["SUBJECT", "FROM", "TO"],
        type_filter="unseen",
    )

    call_args = conn.uid.call_args
    args = call_args.args
    # Expected: SEARCH UNSEEN OR OR SUBJECT "bob" FROM "bob" TO "bob"
    assert args[0] == "SEARCH"
    assert args[1] == "UNSEEN"
    assert args[2] == "OR"
    assert args[3] == "OR"
    assert "SUBJECT" in args
    assert "FROM" in args
    assert "TO" in args


def test_search_namespace_single_field_no_or_chain():
    """A single criterion should NOT produce any OR token."""
    conn = MagicMock()
    conn.select.return_value = ("OK", [b"0"])
    conn.uid.return_value = ("OK", [b""])

    _search_namespace_for_query(
        conn,
        namespace="",
        query="hi",
        folders=["INBOX"],
        criteria_fields=["SUBJECT"],
    )

    args = conn.uid.call_args.args
    assert args == ("SEARCH", "SUBJECT", '"hi"')


def test_search_namespace_default_is_subject_only():
    """Calling without criteria_fields must default to SUBJECT only."""
    conn = MagicMock()
    conn.select.return_value = ("OK", [b"0"])
    conn.uid.return_value = ("OK", [b""])

    _search_namespace_for_query(conn, namespace="", query="hi", folders=["INBOX"])

    args = conn.uid.call_args.args
    assert args == ("SEARCH", "SUBJECT", '"hi"')


def test_search_namespace_iterates_all_folders():
    """G1 regression: search must hit ALL folders in the namespace, not just INBOX."""
    conn = MagicMock()
    # LIST returns 3 folders under namespace "ns/"
    conn.list.return_value = (
        "OK",
        [
            b'(\\HasNoChildren) "/" "ns/INBOX"',
            b'(\\HasNoChildren) "/" "ns/Sent"',
            b'(\\HasNoChildren) "/" "ns/Archive"',
        ],
    )
    # Each SELECT returns OK, each SEARCH returns 0 hits
    conn.select.return_value = ("OK", [b"0"])
    conn.uid.return_value = ("OK", [b""])

    hits = _search_namespace_for_query(conn, namespace="ns/", query="x")

    assert hits == []
    # Verify SELECT was called for each of the 3 folders
    targets = [call.args[0] for call in conn.select.call_args_list]
    assert '"ns/INBOX"' in targets
    assert '"ns/Sent"' in targets
    assert '"ns/Archive"' in targets


def test_list_namespace_folders_skips_noselect():
    """LIST should skip \\Noselect placeholders (e.g. parent of [Gmail])."""
    from mailfallback.routers.restore import _list_namespace_folders

    conn = MagicMock()
    conn.list.return_value = (
        "OK",
        [
            b'(\\HasNoChildren) "/" "ns/INBOX"',
            b'(\\HasChildren \\Noselect) "/" "ns/[Gmail]"',
            b'(\\HasNoChildren) "/" "ns/[Gmail]/All Mail"',
        ],
    )

    folders = _list_namespace_folders(conn, "ns/")

    assert "INBOX" in folders
    assert "[Gmail]/All Mail" in folders
    assert "[Gmail]" not in folders


def test_list_namespace_folders_falls_back_when_list_fails():
    """If LIST fails, fall back to ['INBOX'] so search still runs."""
    from mailfallback.routers.restore import _list_namespace_folders

    conn = MagicMock()
    conn.list.return_value = ("NO", [])

    folders = _list_namespace_folders(conn, "ns/")

    assert folders == ["INBOX"]


@patch("mailfallback.routers.restore.mount_service")
@patch("mailfallback.routers.restore._connect_dovecot_for_account")
@patch("mailfallback.routers.restore.restic_service")
def test_workspace_search_passes_ttl_override(
    mock_restic, mock_connect, mock_mount, client, db_session, default_store, login_user
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

    now = datetime.now(UTC)
    mock_restic.list_snapshots.return_value = [
        {"short_id": "snap1", "time": (now - timedelta(days=2)).isoformat()},
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
    fake_conn.list.return_value = ("OK", [])
    fake_conn.select.return_value = ("OK", [b"0"])
    fake_conn.uid.return_value = ("OK", [b""])
    mock_connect.return_value = (fake_conn, "_restore_test")

    login_resp = client.post("/api/auth/login", json={"username": "koma", "password": "x"})
    assert login_resp.status_code in (200, 303)

    resp = client.post(
        "/api/restore/workspace/search",
        json={
            "account_id": acct.id,
            "query": "x",
            "range_start": (now - timedelta(days=7)).isoformat(),
            "range_end": now.isoformat(),
            "include_live": True,
            "include_snapshots": True,
            "ttl_minutes": 60,
        },
    )
    assert resp.status_code == 200
    # ensure_mounted should have been called with ttl_minutes=60
    call_kwargs = mock_mount.ensure_mounted.call_args.kwargs
    assert call_kwargs.get("ttl_minutes") == 60


@patch("mailfallback.routers.restore.restic_service")
def test_workspace_snapshot_count(mock_restic, client, db_session, default_store, login_user):
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

    now = datetime.now(UTC)
    mock_restic.list_snapshots.return_value = [
        {
            "short_id": "old",
            "time": (now - timedelta(days=30)).isoformat(),
            "summary": {"total_bytes_processed": 1_000_000},
        },
        {
            "short_id": "mid",
            "time": (now - timedelta(days=3)).isoformat(),
            "summary": {"total_bytes_processed": 2_000_000},
        },
        {
            "short_id": "now",
            "time": now.isoformat(),
            "summary": {"total_bytes_processed": 3_000_000},
        },
    ]

    login_resp = client.post("/api/auth/login", json={"username": "koma", "password": "x"})
    assert login_resp.status_code in (200, 303)

    resp = client.post(
        "/api/restore/workspace/snapshot-count",
        json={
            "account_id": acct.id,
            "range_start": (now - timedelta(days=7)).isoformat(),
            "range_end": now.isoformat(),
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2  # mid + now within last 7 days
    assert body["size_bytes"] == 5_000_000  # 2_000_000 + 3_000_000


@patch("mailfallback.routers.restore.mount_service")
@patch("mailfallback.routers.restore._connect_dovecot_for_account")
@patch("mailfallback.routers.restore.restic_service")
def test_workspace_search_uses_account_namespace_for_live_select(
    mock_restic, mock_connect, mock_mount, client, db_session, default_store, login_user
):
    """B5 regression: live SELECT must use the account's full namespace prefix,
    not bare 'INBOX'. Otherwise the dovecot SELECT fails silently and search
    returns 0 results even when matches exist."""
    repo = Repository(name="r", backend_type="local", local_path="/tmp/r", restic_password="x")
    db_session.add(repo)
    acct = Account(
        name="Andrea",
        store=default_store,
        maildir_path="/data/mailboxes/a",
        imap_host="imap.example.com",
        email_address="andrea@example.com",
    )
    db_session.add(acct)
    db_session.flush()
    acct.owners.append(login_user)
    db_session.add(BackupPolicy(account_id=acct.id, destination_id=repo.id))
    db_session.commit()

    mock_restic.list_snapshots.return_value = []  # no snapshots — only live

    fake_conn = MagicMock()
    fake_conn.list.return_value = ("OK", [])
    fake_conn.select.return_value = ("OK", [b"0"])
    fake_conn.uid.return_value = ("OK", [b""])
    mock_connect.return_value = (fake_conn, "_restore_test")

    login_resp = client.post("/api/auth/login", json={"username": "koma", "password": "x"})
    assert login_resp.status_code in (200, 303)

    resp = client.post(
        "/api/restore/workspace/search",
        json={
            "account_id": acct.id,
            "query": "test",
            "range_start": "2026-01-01T00:00:00Z",
            "range_end": "2026-12-31T23:59:59Z",
            "include_live": True,
            "include_snapshots": False,
        },
    )
    assert resp.status_code == 200

    # The SELECT must target the prefixed live namespace, not bare "INBOX".
    short_id = acct.id[-4:]
    expected_target = f'"Andrea (andrea@example.com) [{short_id}]/INBOX"'
    actual_targets = [call.args[0] for call in fake_conn.select.call_args_list]
    assert expected_target in actual_targets, f"Expected {expected_target} in {actual_targets}"


@patch("mailfallback.routers.restore.restic_service")
def test_workspace_snapshot_dates(mock_restic, client, db_session, default_store, login_user):
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

    mock_restic.list_snapshots.return_value = [
        {"short_id": "a", "time": "2026-05-01T10:00:00Z"},
        {"short_id": "b", "time": "2026-05-01T22:00:00Z"},  # same day → dedupe
        {"short_id": "c", "time": "2026-05-08T15:00:00Z"},
    ]

    login_resp = client.post("/api/auth/login", json={"username": "koma", "password": "x"})
    assert login_resp.status_code in (200, 303)

    resp = client.post(
        "/api/restore/workspace/snapshot-dates",
        json={"account_id": acct.id},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["dates"] == ["2026-05-01", "2026-05-08"]  # sorted, deduped
