import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from mailfallback.models import Account, MailStore, User, UserRole
from mailfallback.services.stats_service import collect_account_stats


def _make_store(db):
    store = MailStore(name="default", path="/data/mailboxes")
    db.add(store)
    db.commit()
    db.refresh(store)
    return store


def _make_user_with_account(db, store, username, account_name, email, created_at=None):
    user = User(
        username=username,
        password_hash="x",
        role=UserRole.user,
        enabled=True,
        store_id=store.id,
    )
    db.add(user)
    db.flush()

    account = Account(
        name=account_name,
        email_address=email,
        imap_host="imap.example.com",
        maildir_path=f"/data/mailboxes/{account_name.lower()}",
        store_id=store.id,
        created_at=created_at or datetime.now(UTC),
    )
    db.add(account)
    db.flush()
    user.accounts.append(account)
    db.commit()
    return user, account


def _prefix(account):
    return f"{account.name} ({account.email_address}) [{account.id[-4:]}]"


def test_single_account_stats(db_session):
    store = _make_store(db_session)
    _user, account = _make_user_with_account(db_session, store, "alice", "Gmail", "alice@gmail.com")
    p = _prefix(account)

    dovecot_response = [
        {"mailbox": f"{p}/INBOX", "messages": 3, "unseen": 1, "vsize": 4096},
        {"mailbox": f"{p}/Sent", "messages": 10, "unseen": 0, "vsize": 8192},
        {"mailbox": f"{p}/Archive", "messages": 200, "unseen": 0, "vsize": 102400},
    ]

    with patch(
        "mailfallback.services.dovecot_manager.get_mailbox_stats",
        return_value=dovecot_response,
    ):
        collect_account_stats(db_session, account)

    assert account.total_messages == 213
    assert account.unread_messages == 1
    assert account.maildir_size_bytes == 114688

    folders = json.loads(account.folder_stats)
    folder_map = {f["name"]: f for f in folders}
    assert folder_map["INBOX"]["messages"] == 3
    assert folder_map["Sent"]["messages"] == 10
    assert folder_map["Archive"]["messages"] == 200


def test_two_accounts_first(db_session):
    store = _make_store(db_session)
    now = datetime.now(UTC)

    user = User(
        username="alice", password_hash="x", role=UserRole.user, enabled=True, store_id=store.id
    )
    db_session.add(user)
    db_session.flush()

    acct1 = Account(
        name="Gmail",
        email_address="alice@gmail.com",
        imap_host="imap.gmail.com",
        maildir_path="/data/mailboxes/uuid1",
        store_id=store.id,
        created_at=now,
    )
    acct2 = Account(
        name="Work",
        email_address="alice@work.com",
        imap_host="imap.work.com",
        maildir_path="/data/mailboxes/uuid2",
        store_id=store.id,
        created_at=now + timedelta(seconds=1),
    )
    db_session.add_all([acct1, acct2])
    db_session.flush()
    user.accounts.extend([acct1, acct2])
    db_session.commit()

    p1 = _prefix(acct1)
    p2 = _prefix(acct2)
    dovecot_response = [
        {"mailbox": f"{p1}/INBOX", "messages": 5, "unseen": 2, "vsize": 1000},
        {"mailbox": f"{p1}/Sent", "messages": 3, "unseen": 0, "vsize": 500},
        {"mailbox": f"{p2}/INBOX", "messages": 50, "unseen": 10, "vsize": 9999},
        {"mailbox": f"{p2}/Sent", "messages": 20, "unseen": 0, "vsize": 5000},
    ]

    with patch(
        "mailfallback.services.dovecot_manager.get_mailbox_stats", return_value=dovecot_response
    ):
        collect_account_stats(db_session, acct1)

    assert acct1.total_messages == 8
    assert acct1.unread_messages == 2
    folders = json.loads(acct1.folder_stats)
    assert len(folders) == 2


def test_two_accounts_second(db_session):
    store = _make_store(db_session)
    now = datetime.now(UTC)

    user = User(
        username="alice", password_hash="x", role=UserRole.user, enabled=True, store_id=store.id
    )
    db_session.add(user)
    db_session.flush()

    acct1 = Account(
        name="Gmail",
        email_address="alice@gmail.com",
        imap_host="imap.gmail.com",
        maildir_path="/data/mailboxes/uuid1",
        store_id=store.id,
        created_at=now,
    )
    acct2 = Account(
        name="Work",
        email_address="alice@work.com",
        imap_host="imap.work.com",
        maildir_path="/data/mailboxes/uuid2",
        store_id=store.id,
        created_at=now + timedelta(seconds=1),
    )
    db_session.add_all([acct1, acct2])
    db_session.flush()
    user.accounts.extend([acct1, acct2])
    db_session.commit()

    p1 = _prefix(acct1)
    p2 = _prefix(acct2)
    dovecot_response = [
        {"mailbox": f"{p1}/INBOX", "messages": 5, "unseen": 2, "vsize": 1000},
        {"mailbox": f"{p1}/Sent", "messages": 3, "unseen": 0, "vsize": 500},
        {"mailbox": f"{p2}/INBOX", "messages": 50, "unseen": 10, "vsize": 9999},
        {"mailbox": f"{p2}/Sent", "messages": 20, "unseen": 0, "vsize": 5000},
    ]

    with patch(
        "mailfallback.services.dovecot_manager.get_mailbox_stats", return_value=dovecot_response
    ):
        collect_account_stats(db_session, acct2)

    assert acct2.total_messages == 70
    assert acct2.unread_messages == 10
    folders = json.loads(acct2.folder_stats)
    folder_map = {f["name"]: f for f in folders}
    assert "INBOX" in folder_map
    assert "Sent" in folder_map


def test_collect_stats_no_owners(db_session):
    store = _make_store(db_session)
    account = Account(
        name="Orphan",
        imap_host="imap.test.com",
        maildir_path="/data/mailboxes/orphan",
        store_id=store.id,
    )
    db_session.add(account)
    db_session.commit()

    collect_account_stats(db_session, account)
    assert account.total_messages == 0
    assert account.folder_stats is None


def test_collect_stats_dovecot_unavailable(db_session):
    store = _make_store(db_session)
    _user, account = _make_user_with_account(db_session, store, "bob", "Work", "bob@work.com")

    with patch("mailfallback.services.dovecot_manager.get_mailbox_stats", return_value=None):
        collect_account_stats(db_session, account)

    assert account.total_messages == 0
    assert account.folder_stats is None
