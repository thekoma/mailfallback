# tests/test_dovecot_config.py
from mailfallback.services.dovecot_config import generate_dovecot_userdb, generate_dovecot_passdb


def test_generate_userdb_single_account():
    accounts_by_user = {
        "user1": [
            {"name": "Gmail", "maildir_path": "/data/mailboxes/gmail"},
        ]
    }
    userdb = generate_dovecot_userdb(accounts_by_user)
    assert "user1:" in userdb
    assert "/data/mailboxes/gmail" in userdb


def test_generate_userdb_multiple_accounts():
    accounts_by_user = {
        "user1": [
            {"name": "Gmail", "maildir_path": "/data/mailboxes/gmail"},
            {"name": "Work", "maildir_path": "/data/mailboxes/work"},
        ]
    }
    userdb = generate_dovecot_userdb(accounts_by_user)
    assert "user1:" in userdb
    assert "Gmail" in userdb
    assert "Work" in userdb


def test_generate_passdb():
    users = [
        {"username": "user1", "password_hash": "{BLF-CRYPT}$2b$12$abc"},
        {"username": "user2", "password_hash": "{BLF-CRYPT}$2b$12$def"},
    ]
    passdb = generate_dovecot_passdb(users)
    assert "user1:{BLF-CRYPT}$2b$12$abc" in passdb
    assert "user2:{BLF-CRYPT}$2b$12$def" in passdb


def test_generate_userdb_empty():
    userdb = generate_dovecot_userdb({})
    assert userdb == ""
