"""API tests for POST /api/restore/resolve-uids (restore-to-origin resolution).

Contract verified end-to-end against services/restore_worker.py: `resolved`
keys are namespace-prefixed IMAP paths ready to SELECT on the temp Dovecot
user (``account_namespace_prefix(account) + folder_path``) and values are
real IMAP UIDs. The worker consumes the keys verbatim (_resolve_folders) and
strips the prefix again for the destination folder (_map_folder).
"""

import os
from datetime import UTC, datetime
from email.message import EmailMessage
from unittest.mock import MagicMock, patch

from mailfallback.models import Account, AuditLog, MailIndexMessage, User, UserRole
from mailfallback.routers.dovecot import account_namespace_prefix
from mailfallback.security import hash_password
from mailfallback.services import index_service

CONNECT_PATCH = "mailfallback.routers.restore._connect_dovecot_for_account"
DELETE_PATCH = "mailfallback.routers.restore.delete_temp_imap_user"


def _write_maildir_message(maildir_root, filename, msg):
    cur = os.path.join(maildir_root, "cur")
    os.makedirs(cur, exist_ok=True)
    with open(os.path.join(cur, filename), "wb") as f:
        f.write(msg.as_bytes())


def _msg(msgid, subject="hello"):
    msg = EmailMessage()
    msg["Message-Id"] = msgid
    msg["From"] = "Mittente <sender@example.com>"
    msg["To"] = "dest@example.com"
    msg["Subject"] = subject
    msg["Date"] = "Thu, 11 Jun 2026 10:00:00 +0200"
    msg.set_content("body text")
    return msg


def _mk_user(db_session, default_store, username, role=UserRole.user):
    u = User(
        username=username,
        password_hash=hash_password("x"),
        role=role,
        enabled=True,
        store_id=default_store.id,
    )
    db_session.add(u)
    db_session.commit()
    return u


def _mk_account(db_session, default_store, tmp_path, owner=None):
    acc = Account(
        name="acc1",
        email_address="acc1@example.com",
        imap_host="h",
        maildir_path=str(tmp_path / "mail"),
        store_id=default_store.id,
    )
    db_session.add(acc)
    db_session.flush()
    if owner is not None:
        acc.owners.append(owner)
    db_session.commit()
    return acc


def _login(client, username):
    resp = client.post("/api/auth/login", json={"username": username, "password": "x"})
    assert resp.status_code == 200, resp.text


@patch(DELETE_PATCH)
@patch(CONNECT_PATCH)
def test_owner_resolves_uid_grouped_by_namespaced_folder_key(
    mock_connect, mock_delete, client, db_session, default_store, tmp_path
):
    owner = _mk_user(db_session, default_store, "mario")
    acc = _mk_account(db_session, default_store, tmp_path, owner=owner)
    _write_maildir_message(acc.maildir_path, "1.m1.host:2,S", _msg("<p1@x>"))
    index_service.upsert_message_set(db_session, acc.id)
    _login(client, "mario")

    conn = MagicMock()
    conn.select.return_value = ("OK", [b"1"])
    conn.uid.return_value = ("OK", [b"7"])
    mock_connect.return_value = (conn, "_restore_tmp1")

    resp = client.post(
        "/api/restore/resolve-uids",
        json={"account_id": acc.id, "message_ids": ["<p1@x>"]},
    )

    assert resp.status_code == 200, resp.text
    ns = account_namespace_prefix(acc)
    assert resp.json() == {"resolved": {f"{ns}INBOX": ["7"]}, "missing": []}
    # SELECT must target the namespaced path on the temp-user connection,
    # read-only — exactly the string the restore worker will SELECT later.
    conn.select.assert_called_once_with(f'"{ns}INBOX"', readonly=True)
    conn.uid.assert_called_once_with("SEARCH", "HEADER", "Message-Id", '"<p1@x>"')
    conn.logout.assert_called_once()
    mock_delete.assert_called_once_with(db_session, "_restore_tmp1")


@patch(DELETE_PATCH)
@patch(CONNECT_PATCH)
def test_non_owner_gets_404(mock_connect, mock_delete, client, db_session, default_store, tmp_path):
    owner = _mk_user(db_session, default_store, "mario")
    _mk_user(db_session, default_store, "luigi")  # owns nothing
    acc = _mk_account(db_session, default_store, tmp_path, owner=owner)
    _write_maildir_message(acc.maildir_path, "1.m1.host:2,S", _msg("<p1@x>"))
    index_service.upsert_message_set(db_session, acc.id)
    _login(client, "luigi")

    resp = client.post(
        "/api/restore/resolve-uids",
        json={"account_id": acc.id, "message_ids": ["<p1@x>"]},
    )

    assert resp.status_code == 404
    mock_connect.assert_not_called()


@patch(DELETE_PATCH)
@patch(CONNECT_PATCH)
def test_admin_include_all_resolves_foreign_account(
    mock_connect, mock_delete, client, db_session, default_store, tmp_path
):
    """The support scenario: with include_all an admin resolves UIDs in a
    mailbox they don't own (the restore that follows is audited by
    restore.start)."""
    owner = _mk_user(db_session, default_store, "mario")
    _mk_user(db_session, default_store, "root", role=UserRole.admin)
    acc = _mk_account(db_session, default_store, tmp_path, owner=owner)
    _write_maildir_message(acc.maildir_path, "1.m1.host:2,S", _msg("<p1@x>"))
    index_service.upsert_message_set(db_session, acc.id)
    _login(client, "root")

    conn = MagicMock()
    conn.select.return_value = ("OK", [b"1"])
    conn.uid.return_value = ("OK", [b"7"])
    mock_connect.return_value = (conn, "_restore_tmpA")

    resp = client.post(
        "/api/restore/resolve-uids",
        json={"account_id": acc.id, "message_ids": ["<p1@x>"], "include_all": True},
    )

    assert resp.status_code == 200, resp.text
    ns = account_namespace_prefix(acc)
    assert resp.json() == {"resolved": {f"{ns}INBOX": ["7"]}, "missing": []}
    # Deliberately NOT audited: resolve-uids is a lookup step — the restore
    # that consumes the mapping logs restore.start. Pin that decision.
    assert db_session.query(AuditLog).filter(AuditLog.action.like("restore.%")).count() == 0


@patch(DELETE_PATCH)
@patch(CONNECT_PATCH)
def test_admin_without_include_all_gets_404_on_foreign_account(
    mock_connect, mock_delete, client, db_session, default_store, tmp_path
):
    """Privacy default: no implicit admin access in the workspace flow — the
    audited include_all escalation is the only way in."""
    owner = _mk_user(db_session, default_store, "mario")
    _mk_user(db_session, default_store, "root", role=UserRole.admin)
    acc = _mk_account(db_session, default_store, tmp_path, owner=owner)
    _write_maildir_message(acc.maildir_path, "1.m1.host:2,S", _msg("<p1@x>"))
    index_service.upsert_message_set(db_session, acc.id)
    _login(client, "root")

    resp = client.post(
        "/api/restore/resolve-uids",
        json={"account_id": acc.id, "message_ids": ["<p1@x>"]},
    )

    assert resp.status_code == 404
    mock_connect.assert_not_called()


@patch(DELETE_PATCH)
@patch(CONNECT_PATCH)
def test_non_admin_include_all_gets_404(
    mock_connect, mock_delete, client, db_session, default_store, tmp_path
):
    owner = _mk_user(db_session, default_store, "mario")
    _mk_user(db_session, default_store, "luigi")  # owns nothing
    acc = _mk_account(db_session, default_store, tmp_path, owner=owner)
    _write_maildir_message(acc.maildir_path, "1.m1.host:2,S", _msg("<p1@x>"))
    index_service.upsert_message_set(db_session, acc.id)
    _login(client, "luigi")

    resp = client.post(
        "/api/restore/resolve-uids",
        json={"account_id": acc.id, "message_ids": ["<p1@x>"], "include_all": True},
    )

    assert resp.status_code == 404
    mock_connect.assert_not_called()


@patch(DELETE_PATCH)
@patch(CONNECT_PATCH)
def test_deleted_message_lands_in_missing_without_imap(
    mock_connect, mock_delete, client, db_session, default_store, tmp_path
):
    owner = _mk_user(db_session, default_store, "mario")
    acc = _mk_account(db_session, default_store, tmp_path, owner=owner)
    _write_maildir_message(acc.maildir_path, "1.m1.host:2,S", _msg("<p1@x>"))
    index_service.upsert_message_set(db_session, acc.id)
    row = db_session.query(MailIndexMessage).filter_by(account_id=acc.id).one()
    row.deleted_at = datetime.now(UTC)
    db_session.commit()
    _login(client, "mario")

    resp = client.post(
        "/api/restore/resolve-uids",
        json={"account_id": acc.id, "message_ids": ["<p1@x>"]},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"resolved": {}, "missing": ["<p1@x>"]}
    mock_connect.assert_not_called()


@patch(DELETE_PATCH)
@patch(CONNECT_PATCH)
def test_message_ids_beyond_cap_are_ignored(
    mock_connect, mock_delete, client, db_session, default_store, tmp_path
):
    owner = _mk_user(db_session, default_store, "mario")
    acc = _mk_account(db_session, default_store, tmp_path, owner=owner)
    _write_maildir_message(acc.maildir_path, "1.m1.host:2,S", _msg("<p1@x>"))
    index_service.upsert_message_set(db_session, acc.id)
    _login(client, "mario")

    ids = [f"<bogus-{i}@x>" for i in range(200)] + ["<p1@x>"]
    resp = client.post(
        "/api/restore/resolve-uids",
        json={"account_id": acc.id, "message_ids": ids},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["resolved"] == {}
    # The 201st id (the only real one) is ignored entirely: not resolved,
    # not reported missing. The first 200 (all bogus) are missing.
    assert "<p1@x>" not in body["missing"]
    assert len(body["missing"]) == 200
    mock_connect.assert_not_called()


@patch(DELETE_PATCH)
@patch(CONNECT_PATCH)
def test_multi_folder_grouping_and_partial_missing(
    mock_connect, mock_delete, client, db_session, default_store, tmp_path
):
    owner = _mk_user(db_session, default_store, "mario")
    acc = _mk_account(db_session, default_store, tmp_path, owner=owner)
    _write_maildir_message(acc.maildir_path, "1.m1.host:2,S", _msg("<p1@x>"))
    _write_maildir_message(os.path.join(acc.maildir_path, "Sent"), "2.m2.host:2,S", _msg("<p2@x>"))
    index_service.upsert_message_set(db_session, acc.id)
    _login(client, "mario")

    conn = MagicMock()
    conn.select.return_value = ("OK", [b"1"])

    def uid_side_effect(*args):
        crit = " ".join(str(a) for a in args)
        if "p1@x" in crit:
            return ("OK", [b"7"])
        if "p2@x" in crit:
            return ("OK", [b"9"])
        return ("OK", [b""])

    conn.uid.side_effect = uid_side_effect
    mock_connect.return_value = (conn, "_restore_tmp2")

    resp = client.post(
        "/api/restore/resolve-uids",
        json={"account_id": acc.id, "message_ids": ["<p1@x>", "<p2@x>", "<ghost@x>"]},
    )

    assert resp.status_code == 200, resp.text
    ns = account_namespace_prefix(acc)
    body = resp.json()
    assert body["resolved"] == {f"{ns}INBOX": ["7"], f"{ns}Sent": ["9"]}
    assert body["missing"] == ["<ghost@x>"]


@patch(DELETE_PATCH)
@patch(CONNECT_PATCH)
def test_select_failure_puts_messages_in_missing_and_cleans_up(
    mock_connect, mock_delete, client, db_session, default_store, tmp_path, caplog
):
    owner = _mk_user(db_session, default_store, "mario")
    acc = _mk_account(db_session, default_store, tmp_path, owner=owner)
    _write_maildir_message(acc.maildir_path, "1.m1.host:2,S", _msg("<p1@x>"))
    index_service.upsert_message_set(db_session, acc.id)
    _login(client, "mario")

    conn = MagicMock()
    conn.select.return_value = ("NO", [b"Mailbox doesn't exist"])
    mock_connect.return_value = (conn, "_restore_tmp3")

    with caplog.at_level("WARNING", logger="mailfallback.routers.restore"):
        resp = client.post(
            "/api/restore/resolve-uids",
            json={"account_id": acc.id, "message_ids": ["<p1@x>"]},
        )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"resolved": {}, "missing": ["<p1@x>"]}
    conn.uid.assert_not_called()
    conn.logout.assert_called_once()
    mock_delete.assert_called_once()
    # A failed SELECT must be diagnosable in production (B5 drift class).
    assert any("SELECT" in r.getMessage() and acc.id in r.getMessage() for r in caplog.records)
