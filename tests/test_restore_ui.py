from unittest.mock import MagicMock, patch

from mailfallback.models import Account, User, UserRole
from mailfallback.services.user_service import create_user


def test_restore_page_redirects_unauthenticated(client):
    resp = client.get("/restore", follow_redirects=False)
    assert resp.status_code == 307


def test_restore_page_renders(client, db_session, default_store):
    create_user(db_session, "uitest", "pass", UserRole.admin, store_id=default_store.id)
    client.post("/api/auth/login", json={"username": "uitest", "password": "pass"})
    resp = client.get("/restore")
    assert resp.status_code == 200
    assert "Restore" in resp.text


def test_restore_mailbox_select_lists_accounts_without_backup_policy(
    client, db_session, default_store
):
    """The Mailbox search dropdown must list every owned account, not just the
    ones with a BackupPolicy — otherwise the workspace search silently posts
    account_id="" and always returns zero results."""
    acct = _setup_separator_test(db_session, default_store, client)
    resp = client.get("/restore")
    assert resp.status_code == 200
    # The account id must appear in BOTH the Mailbox and Destination selects.
    assert resp.text.count(f'value="{acct.id}"') == 2


def _setup_separator_test(db_session, default_store, client):
    """Create a user and target account, login, and return the account."""
    user = User(
        username="sepuser",
        password_hash="x",
        store_id=default_store.id,
        role=UserRole.admin,
    )
    db_session.add(user)
    db_session.flush()
    acct = Account(
        name="target",
        email_address="tgt@example.com",
        imap_host="imap.example.com",
        imap_port=993,
        maildir_path="/data/mailboxes/tgt",
        store_id=default_store.id,
        credentials="encrypted-creds",
    )
    db_session.add(acct)
    db_session.commit()
    db_session.refresh(user)
    db_session.refresh(acct)
    acct.owners.append(user)
    db_session.commit()
    with patch("mailfallback.routers.auth.authenticate_user", return_value=user):
        client.post("/api/auth/login", json={"username": "sepuser", "password": "x"})
    return acct


def test_separator_warning_no_target(client, db_session, default_store):
    create_user(db_session, "sepuser2", "pass", UserRole.admin, store_id=default_store.id)
    client.post("/api/auth/login", json={"username": "sepuser2", "password": "pass"})
    resp = client.get("/restore/partials/separator-warning?target_account_id=")
    assert resp.status_code == 200
    assert "hidden" in resp.text


def test_separator_warning_dot_separator(client, db_session, default_store):
    acct = _setup_separator_test(db_session, default_store, client)
    mock_conn = MagicMock()
    mock_conn.list.return_value = ("OK", [b'(\\NoSelect) "." ""'])
    mock_conn.logout.return_value = None

    with (
        patch(
            "mailfallback.routers.ui_restore.decrypt_credentials",
            return_value="plainpass",
        ),
        patch(
            "mailfallback.services.imap_check.connect_imap",
            return_value=mock_conn,
        ),
    ):
        resp = client.get(f"/restore/partials/separator-warning?target_account_id={acct.id}")
    assert resp.status_code == 200
    assert "Dot separator detected" in resp.text
    assert "warning-box" in resp.text
    assert "My.Archive" in resp.text
    assert "My_Archive" in resp.text


def test_separator_warning_slash_separator(client, db_session, default_store):
    acct = _setup_separator_test(db_session, default_store, client)
    mock_conn = MagicMock()
    mock_conn.list.return_value = ("OK", [b'(\\NoSelect) "/" ""'])
    mock_conn.logout.return_value = None

    with (
        patch(
            "mailfallback.routers.ui_restore.decrypt_credentials",
            return_value="plainpass",
        ),
        patch(
            "mailfallback.services.imap_check.connect_imap",
            return_value=mock_conn,
        ),
    ):
        resp = client.get(f"/restore/partials/separator-warning?target_account_id={acct.id}")
    assert resp.status_code == 200
    assert "hidden" in resp.text
    assert "warning-box" not in resp.text


def test_separator_warning_connection_error(client, db_session, default_store):
    acct = _setup_separator_test(db_session, default_store, client)

    with (
        patch(
            "mailfallback.routers.ui_restore.decrypt_credentials",
            return_value="plainpass",
        ),
        patch(
            "mailfallback.services.imap_check.connect_imap",
            side_effect=OSError("Connection refused"),
        ),
    ):
        resp = client.get(f"/restore/partials/separator-warning?target_account_id={acct.id}")
    assert resp.status_code == 200
    assert "Could not connect" in resp.text
    assert "info-box" in resp.text
