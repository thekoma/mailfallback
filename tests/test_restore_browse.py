from unittest.mock import MagicMock, patch

import pytest

from mailfallback.models import Account, User, UserRole


@pytest.fixture
def browse_fixtures(db_session, default_store):
    user = User(
        username="browser",
        password_hash="x",
        store_id=default_store.id,
        role=UserRole.admin,
    )
    db_session.add(user)
    db_session.flush()
    acct = Account(
        name="browseacct",
        email_address="browse@example.com",
        imap_host="imap.example.com",
        imap_port=993,
        maildir_path="/data/mailboxes/browse",
        store_id=default_store.id,
        credentials="enc",
    )
    db_session.add(acct)
    db_session.commit()
    db_session.refresh(user)
    db_session.refresh(acct)
    acct.owners.append(user)
    db_session.commit()
    return {"user": user, "account": acct}


def _login(client, db_session, username="browser"):
    """Bypass authentication by injecting user_id directly into the session."""
    from mailfallback.models import User

    user = db_session.query(User).filter(User.username == username).first()
    with patch("mailfallback.routers.auth.authenticate_user", return_value=user):
        client.post("/api/auth/login", json={"username": username, "password": "x"})


@patch("mailfallback.routers.restore.connect_imap")
def test_list_mailboxes(mock_connect, client, db_session, browse_fixtures):
    f = browse_fixtures
    _login(client, db_session)
    mock_conn = MagicMock()
    mock_connect.return_value = mock_conn
    prefix = f"browseacct (browse@example.com) [{f['account'].id[:8]}]"
    mock_conn.list.return_value = (
        "OK",
        [
            f'(\\HasNoChildren) "/" "{prefix}/INBOX"'.encode(),
            f'(\\HasNoChildren) "/" "{prefix}/Sent"'.encode(),
        ],
    )
    mock_conn.status.side_effect = [
        ("OK", [f'"{prefix}/INBOX" (MESSAGES 42)'.encode()]),
        ("OK", [f'"{prefix}/Sent" (MESSAGES 10)'.encode()]),
    ]

    resp = client.get(f"/api/accounts/{f['account'].id}/mailboxes")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["name"] == "INBOX"
    assert data[0]["messages"] == 42
    mock_conn.logout.assert_called_once()


@patch("mailfallback.routers.restore.connect_imap")
def test_list_messages(mock_connect, client, db_session, browse_fixtures):
    f = browse_fixtures
    _login(client, db_session)
    mock_conn = MagicMock()
    mock_connect.return_value = mock_conn

    mock_conn.select.return_value = ("OK", [b"2"])
    mock_conn.search.return_value = ("OK", [b"1 2"])
    env1 = (
        b'1 (ENVELOPE ("Mon, 01 Jan 2024 00:00:00 +0000" "Subject1"'
        b' (("From" NIL "user" "example.com")) NIL NIL NIL NIL NIL'
        b' NIL "<msg1@example.com>") FLAGS (\\Seen))'
    )
    env2 = (
        b'2 (ENVELOPE ("Tue, 02 Jan 2024 00:00:00 +0000" "Subject2"'
        b' (("From2" NIL "user2" "example.com")) NIL NIL NIL NIL NIL'
        b' NIL "<msg2@example.com>") FLAGS ())'
    )
    mock_conn.fetch.return_value = (
        "OK",
        [(env1, b""), (env2, b"")],
    )

    resp = client.get(f"/api/accounts/{f['account'].id}/mailboxes/INBOX/messages")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1


@patch("mailfallback.routers.restore.connect_imap")
def test_search_messages(mock_connect, client, db_session, browse_fixtures):
    f = browse_fixtures
    _login(client, db_session)
    mock_conn = MagicMock()
    mock_connect.return_value = mock_conn

    mock_conn.select.return_value = ("OK", [b"1"])
    mock_conn.search.return_value = ("OK", [b"1"])
    env_found = (
        b'1 (ENVELOPE ("Mon, 01 Jan 2024 00:00:00 +0000" "Found It"'
        b' (("Sender" NIL "s" "example.com")) NIL NIL NIL NIL NIL'
        b' NIL "<found@example.com>") FLAGS (\\Seen))'
    )
    mock_conn.fetch.return_value = (
        "OK",
        [(env_found, b"")],
    )

    resp = client.get(f"/api/accounts/{f['account'].id}/mailboxes/INBOX/search?q=test")
    assert resp.status_code == 200
