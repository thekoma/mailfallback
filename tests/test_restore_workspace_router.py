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


@patch("mailfallback.routers.restore.mount_service")
@patch("mailfallback.routers.restore._connect_dovecot_for_account")
@patch("mailfallback.routers.restore.restic_service")
def test_workspace_search_dedup_by_message_id(
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
    mock_connect.return_value = fake_conn

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
    assert "live" in result["sources"]
    assert "snap1" in result["sources"]
