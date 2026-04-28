# tests/test_mbsync_config.py
from mailfallback.services.mbsync_config import generate_mbsyncrc


def test_generate_app_password_config():
    config = generate_mbsyncrc(
        account_name="gmail",
        imap_host="imap.gmail.com",
        imap_port=993,
        username="user@gmail.com",
        auth_type="app_password",
        password="myapppassword",
        maildir_path="/data/mailboxes/gmail",
    )
    assert "IMAPStore gmail-remote" in config
    assert "Host imap.gmail.com" in config
    assert "Port 993" in config
    assert "User user@gmail.com" in config
    assert 'Pass "myapppassword"' in config
    assert "MaildirStore gmail-local" in config
    assert "Path /data/mailboxes/gmail/" in config
    assert "Channel gmail" in config
    assert "Create Near" in config
    assert "SyncState *" in config


def test_generate_oauth2_config():
    config = generate_mbsyncrc(
        account_name="gmail",
        imap_host="imap.gmail.com",
        imap_port=993,
        username="user@gmail.com",
        auth_type="oauth2",
        token_command="/usr/local/bin/token_helper gmail",
        maildir_path="/data/mailboxes/gmail",
    )
    assert "AuthMechs XOAUTH2" in config
    assert 'PassCmd "/usr/local/bin/token_helper gmail"' in config
    assert "Pass " not in config


def test_maildir_path_gets_trailing_slash():
    config = generate_mbsyncrc(
        account_name="test",
        imap_host="imap.test.com",
        imap_port=993,
        username="u@test.com",
        auth_type="app_password",
        password="p",
        maildir_path="/data/mailboxes/test",
    )
    assert "Path /data/mailboxes/test/" in config


def test_maildir_path_no_double_slash():
    config = generate_mbsyncrc(
        account_name="test",
        imap_host="imap.test.com",
        imap_port=993,
        username="u@test.com",
        auth_type="app_password",
        password="p",
        maildir_path="/data/mailboxes/test/",
    )
    assert "Path /data/mailboxes/test/" in config
    assert "/test//" not in config
