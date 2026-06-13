import json

from mailfallback.services.mbsync_config import generate_mbsyncrc


def test_generate_app_password_config():
    config = generate_mbsyncrc(
        account_name="gmail",
        imap_host="imap.gmail.com",
        imap_port=993,
        username="user@gmail.com",
        auth_type="app_password",
        password="myapppassword",
        maildir_path="/data/mailboxes/mario/mario_gmail_com",
    )
    assert "IMAPAccount gmail" in config
    assert "Host imap.gmail.com" in config
    assert "Port 993" in config
    assert "User user@gmail.com" in config
    assert 'Pass "myapppassword"' in config
    assert "IMAPStore gmail-remote" in config
    assert "Account gmail" in config
    assert "MaildirStore gmail-local" in config
    assert "Path /data/mailboxes/mario/mario_gmail_com/" in config
    assert "Inbox /data/mailboxes/mario/mario_gmail_com/INBOX" in config
    assert "SubFolders Verbatim" in config
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
        maildir_path="/data/mailboxes/mario/mario_gmail_com",
    )
    assert "AuthMechs XOAUTH2" in config
    assert 'PassCmd "/usr/local/bin/token_helper gmail"' in config
    assert "Pass " not in config


def test_inbox_no_trailing_slash():
    config = generate_mbsyncrc(
        account_name="test",
        imap_host="imap.test.com",
        imap_port=993,
        username="u@test.com",
        auth_type="app_password",
        password="p",
        maildir_path="/data/mailboxes/mario/test_com/",
    )
    assert "Path /data/mailboxes/mario/test_com/\n" in config
    assert "Inbox /data/mailboxes/mario/test_com/INBOX\n" in config


def test_extra_config():
    extra = json.dumps(
        {
            "sync": "Pull",
            "create": "Both",
            "expunge": "Near",
            "patterns": '* ![Gmail]* "[Gmail]/Sent Mail"',
            "max_messages": "1000",
            "timeout": "30",
            "pipeline_depth": "50",
            "copy_arrival_date": True,
        }
    )
    config = generate_mbsyncrc(
        account_name="full",
        imap_host="imap.example.com",
        imap_port=993,
        username="user@example.com",
        auth_type="app_password",
        password="pass",
        maildir_path="/data/mailboxes/mario/user_example_com",
        tls_type="STARTTLS",
        extra_config=extra,
    )
    assert "TLSType STARTTLS" in config
    assert "Sync Pull" in config
    assert "Create Both" in config
    assert "Expunge Near" in config
    assert "MaxMessages 1000" in config
    assert "SubFolders Verbatim" in config
    assert "Timeout 30" in config
    assert "PipelineDepth 50" in config
    assert "CopyArrivalDate yes" in config
    assert "![Gmail]*" in config


def test_tls_none():
    config = generate_mbsyncrc(
        account_name="plain",
        imap_host="localhost",
        imap_port=143,
        username="user",
        auth_type="app_password",
        password="pass",
        maildir_path="/data/mailboxes/mario/plain_com",
        tls_type="None",
    )
    assert "TLSType" not in config


def test_verbatim_with_path_and_inbox():
    config = generate_mbsyncrc(
        account_name="test",
        imap_host="imap.test.com",
        imap_port=993,
        username="u@test.com",
        auth_type="app_password",
        password="p",
        maildir_path="/data/mailboxes/user/account",
    )
    assert "SubFolders Verbatim" in config
    lines = config.splitlines()
    path_line = next(ln for ln in lines if ln.startswith("Path "))
    inbox_line = next(ln for ln in lines if ln.startswith("Inbox "))
    assert path_line == "Path /data/mailboxes/user/account/"
    assert inbox_line == "Inbox /data/mailboxes/user/account/INBOX"


# ---------------------------------------------------------------------------
# Pattern exclusions + channel name (worker seams, sync-budget Task 5)
# ---------------------------------------------------------------------------


def test_excluded_folder_names_real_gmail_patterns():
    """The REAL gmail patterns string the app generates — quoted negations."""
    from mailfallback.services.mbsync_config import excluded_folder_names

    patterns = '* !"[Gmail]/All Mail" !"[Gmail]/Spam" !"[Gmail]/Trash"'
    assert excluded_folder_names(patterns) == [
        "[Gmail]/All Mail",
        "[Gmail]/Spam",
        "[Gmail]/Trash",
    ]


def test_excluded_folder_names_bare_and_empty():
    from mailfallback.services.mbsync_config import excluded_folder_names

    assert excluded_folder_names("* !Spam !Trash") == ["Spam", "Trash"]
    assert excluded_folder_names("*") == []
    assert excluded_folder_names("") == []


def test_channel_name_matches_generated_channel():
    """The worker's priority pass targets `<channel>:INBOX` — the helper must
    derive EXACTLY the Channel line generate_mbsyncrc writes."""
    from mailfallback.services.mbsync_config import channel_name

    config = generate_mbsyncrc(
        account_name="Main gMail",
        imap_host="imap.gmail.com",
        imap_port=993,
        username="u@test.com",
        auth_type="app_password",
        password="p",
        maildir_path="/data/mailboxes/x",
    )
    assert channel_name("Main gMail") == "main_gmail"
    assert f"Channel {channel_name('Main gMail')}" in config
