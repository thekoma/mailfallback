from unittest.mock import MagicMock, patch

from mailfallback.services.imap_check import connect_imap


@patch("mailfallback.services.imap_check.imaplib.IMAP4_SSL")
def test_connect_imap_ssl(mock_ssl):
    mock_conn = MagicMock()
    mock_ssl.return_value = mock_conn

    conn = connect_imap("imap.example.com", 993, "IMAPS", "user@example.com", "pass123")

    mock_ssl.assert_called_once_with("imap.example.com", 993, timeout=30)
    mock_conn.login.assert_called_once_with("user@example.com", "pass123")
    assert conn is mock_conn


@patch("mailfallback.services.imap_check.imaplib.IMAP4")
def test_connect_imap_starttls(mock_imap):
    mock_conn = MagicMock()
    mock_imap.return_value = mock_conn

    conn = connect_imap("imap.example.com", 143, "STARTTLS", "user", "pass")

    mock_imap.assert_called_once_with("imap.example.com", 143, timeout=30)
    mock_conn.starttls.assert_called_once()
    mock_conn.login.assert_called_once_with("user", "pass")
    assert conn is mock_conn


@patch("mailfallback.services.imap_check.imaplib.IMAP4")
def test_connect_imap_plain(mock_imap):
    mock_conn = MagicMock()
    mock_imap.return_value = mock_conn

    conn = connect_imap("imap.example.com", 143, "NONE", "user", "pass")

    mock_imap.assert_called_once_with("imap.example.com", 143, timeout=30)
    mock_conn.login.assert_called_once_with("user", "pass")
    assert conn is mock_conn


@patch("mailfallback.services.imap_check.imaplib.IMAP4_SSL")
def test_connect_imap_xoauth2(mock_ssl):
    """OAuth2 access tokens are rejected by plain LOGIN (Gmail/Microsoft:
    [AUTHENTICATIONFAILED] Invalid credentials) — auth_method="xoauth2" must
    issue AUTHENTICATE XOAUTH2 with the SASL initial response instead."""
    mock_conn = MagicMock()
    mock_ssl.return_value = mock_conn

    conn = connect_imap(
        "imap.gmail.com",
        993,
        "IMAPS",
        "user@example.com",
        "ya29.access-token",
        auth_method="xoauth2",
    )

    mock_ssl.assert_called_once_with("imap.gmail.com", 993, timeout=30)
    mock_conn.login.assert_not_called()
    mock_conn.authenticate.assert_called_once()
    mech, authobject = mock_conn.authenticate.call_args.args
    assert mech == "XOAUTH2"
    assert callable(authobject)
    expected_sasl = b"user=user@example.com\x01auth=Bearer ya29.access-token\x01\x01"
    assert authobject(None) == expected_sasl
    # Server may re-challenge (e.g. base64 error blob) — keep answering with
    # the same SASL string so imaplib can finish the exchange.
    assert authobject(b"challenge") == expected_sasl
    assert conn is mock_conn


@patch("mailfallback.services.imap_check.imaplib.IMAP4_SSL")
def test_connect_imap_default_auth_is_plain_login(mock_ssl):
    """Without auth_method, connect_imap must keep using LOGIN — password
    accounts and the Dovecot source connection rely on it."""
    mock_conn = MagicMock()
    mock_ssl.return_value = mock_conn

    connect_imap("imap.example.com", 993, "IMAPS", "user@example.com", "pass123")

    mock_conn.login.assert_called_once_with("user@example.com", "pass123")
    mock_conn.authenticate.assert_not_called()
