# tests/test_connection_test.py
import imaplib
from unittest.mock import MagicMock, patch

from mailfallback.services.imap_check import check_imap_credentials


def _mock_conn(login_ok=True, login_error="bad credentials"):
    conn = MagicMock()
    conn.welcome = b"* OK IMAP server ready"
    conn.capability.return_value = ("OK", [b"IMAP4rev1 AUTH=PLAIN AUTH=LOGIN"])
    if login_ok:
        conn.login.return_value = ("OK", [b"LOGIN completed"])
    else:
        conn.login.side_effect = imaplib.IMAP4.error(login_error)
    return conn


def test_connection_ok_no_credentials():
    conn = _mock_conn()
    with patch("mailfallback.services.imap_check.imaplib.IMAP4_SSL", return_value=conn):
        result = check_imap_credentials("imap.example.com", 993)
    assert result["ok"] is True
    assert result["login_ok"] is None
    assert "LOGIN" in result["auth_mechs"]


def test_connection_login_success():
    conn = _mock_conn(login_ok=True)
    with patch("mailfallback.services.imap_check.imaplib.IMAP4_SSL", return_value=conn):
        result = check_imap_credentials(
            "imap.example.com", 993, username="u@x.com", password="pass"
        )
    assert result["ok"] is True
    assert result["login_ok"] is True
    conn.login.assert_called_once_with("u@x.com", "pass")


def test_connection_login_failure():
    conn = _mock_conn(login_ok=False, login_error="AUTHENTICATIONFAILED")
    with patch("mailfallback.services.imap_check.imaplib.IMAP4_SSL", return_value=conn):
        result = check_imap_credentials(
            "imap.example.com", 993, username="u@x.com", password="wrong"
        )
    assert result["ok"] is True
    assert result["login_ok"] is False
    assert "AUTHENTICATIONFAILED" in result["login_message"]


def test_connection_refused():
    with patch(
        "mailfallback.services.imap_check.imaplib.IMAP4_SSL",
        side_effect=OSError("Connection refused"),
    ):
        result = check_imap_credentials("bad.host", 993)
    assert result["ok"] is False
    assert "Connection refused" in result["message"]


def test_connection_starttls():
    conn = _mock_conn()
    with patch("mailfallback.services.imap_check.imaplib.IMAP4", return_value=conn):
        result = check_imap_credentials("imap.example.com", 143, tls_type="STARTTLS")
    assert result["ok"] is True
    conn.starttls.assert_called_once()
