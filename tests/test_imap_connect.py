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
