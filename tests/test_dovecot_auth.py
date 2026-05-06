from mailfallback.models import Account, User, account_owners
from mailfallback.security import verify_password
from mailfallback.services.dovecot_auth import (
    TEMP_USER_PREFIX,
    cleanup_temp_imap_users,
    create_temp_imap_user,
    delete_temp_imap_user,
)


def test_create_temp_imap_user(db_session, default_store):
    acct = Account(
        name="test",
        imap_host="imap.test.com",
        imap_port=993,
        maildir_path="/data/mailboxes/temp-test",
        store_id=default_store.id,
    )
    db_session.add(acct)
    db_session.commit()

    username, password = create_temp_imap_user(db_session, [acct.id])

    assert username.startswith(TEMP_USER_PREFIX)
    assert len(password) > 20

    user = db_session.query(User).filter(User.username == username).first()
    assert user is not None
    assert user.enabled is True
    assert verify_password(password, user.password_hash)

    ownership = (
        db_session.query(account_owners)
        .filter(account_owners.c.user_id == user.id, account_owners.c.account_id == acct.id)
        .first()
    )
    assert ownership is not None


def test_delete_temp_imap_user(db_session, default_store):
    acct = Account(
        name="deltest",
        imap_host="imap.test.com",
        imap_port=993,
        maildir_path="/data/mailboxes/del-test",
        store_id=default_store.id,
    )
    db_session.add(acct)
    db_session.commit()

    username, _ = create_temp_imap_user(db_session, [acct.id])
    user = db_session.query(User).filter(User.username == username).first()
    assert user is not None

    delete_temp_imap_user(db_session, username)

    assert db_session.query(User).filter(User.username == username).first() is None
    assert (
        db_session.query(account_owners).filter(account_owners.c.user_id == user.id).first()
    ) is None


def test_cleanup_temp_imap_users(db_session, default_store):
    acct = Account(
        name="cleanup",
        imap_host="imap.test.com",
        imap_port=993,
        maildir_path="/data/mailboxes/cleanup-test",
        store_id=default_store.id,
    )
    db_session.add(acct)
    db_session.commit()

    create_temp_imap_user(db_session, [acct.id])
    create_temp_imap_user(db_session, [acct.id])

    count = cleanup_temp_imap_users(db_session)
    assert count == 2

    remaining = db_session.query(User).filter(User.username.like(f"{TEMP_USER_PREFIX}%")).count()
    assert remaining == 0


def test_cleanup_does_not_affect_real_users(db_session, default_store):
    real_user = User(username="realuser", password_hash="x", store_id=default_store.id)
    db_session.add(real_user)
    db_session.commit()

    acct = Account(
        name="real",
        imap_host="imap.test.com",
        imap_port=993,
        maildir_path="/data/mailboxes/real-test",
        store_id=default_store.id,
    )
    db_session.add(acct)
    db_session.commit()

    create_temp_imap_user(db_session, [acct.id])
    cleanup_temp_imap_users(db_session)

    assert db_session.query(User).filter(User.username == "realuser").first() is not None
