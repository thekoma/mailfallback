# tests/test_models.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mailfallback.db import Base
from mailfallback.models import (
    Account,
    AuthType,
    JobStatus,
    MailStore,
    SyncJob,
    SyncState,
    User,
    UserRole,
)


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _make_store(session):
    store = MailStore(name="default", path="/data/mailboxes")
    session.add(store)
    session.commit()
    session.refresh(store)
    return store


def test_create_user():
    session = make_session()
    store = _make_store(session)
    user = User(username="admin", role=UserRole.admin, store_id=store.id)
    session.add(user)
    session.commit()
    assert user.id is not None
    assert user.username == "admin"
    assert user.role == UserRole.admin


def test_create_account_with_owner():
    session = make_session()
    store = _make_store(session)
    user = User(username="testuser", store_id=store.id)
    account = Account(
        name="Gmail",
        imap_host="imap.gmail.com",
        imap_port=993,
        maildir_path="/data/mailboxes/gmail",
        store_id=store.id,
    )
    account.owners.append(user)
    session.add(account)
    session.commit()
    assert account.id is not None
    assert len(account.owners) == 1
    assert account.owners[0].username == "testuser"
    assert len(user.accounts) == 1


def test_create_sync_job():
    session = make_session()
    store = _make_store(session)
    account = Account(
        name="Work",
        imap_host="imap.work.com",
        maildir_path="/data/mailboxes/work",
        store_id=store.id,
    )
    session.add(account)
    session.commit()
    job = SyncJob(account_id=account.id, source="api")
    session.add(job)
    session.commit()
    assert job.status == JobStatus.pending
    assert job.account.name == "Work"


def test_account_defaults():
    session = make_session()
    store = _make_store(session)
    account = Account(
        name="Test",
        imap_host="imap.test.com",
        maildir_path="/data/mailboxes/test",
        store_id=store.id,
    )
    session.add(account)
    session.commit()
    assert account.auth_type == AuthType.app_password
    assert account.sync_state == SyncState.idle
    assert account.enabled is True
    assert account.imap_port == 993


def test_account_provider_default():
    session = make_session()
    store = _make_store(session)
    account = Account(
        name="Test",
        imap_host="imap.test.com",
        maildir_path="/data/mailboxes/test-provider",
        store_id=store.id,
    )
    session.add(account)
    session.commit()
    assert account.provider == "other"


def test_account_with_explicit_provider():
    session = make_session()
    store = _make_store(session)
    account = Account(
        name="Gmail",
        imap_host="imap.gmail.com",
        maildir_path="/data/mailboxes/gmail-provider",
        provider="google",
        store_id=store.id,
    )
    session.add(account)
    session.commit()
    assert account.provider == "google"


def test_account_stats_defaults():
    session = make_session()
    store = _make_store(session)
    account = Account(
        name="Test",
        imap_host="imap.test.com",
        maildir_path="/data/mailboxes/test-stats",
        store_id=store.id,
    )
    session.add(account)
    session.commit()
    assert account.total_messages == 0
    assert account.unread_messages == 0
    assert account.maildir_size_bytes == 0
    assert account.folder_stats is None


def test_account_is_authenticated_oauth_no_creds():
    session = make_session()
    store = _make_store(session)
    account = Account(
        name="Test",
        imap_host="imap.gmail.com",
        imap_port=993,
        auth_type=AuthType.oauth2,
        credentials=None,
        store_id=store.id,
        maildir_path="/data/mailboxes/test-oauth",
    )
    session.add(account)
    session.commit()
    assert account.is_authenticated is False


def test_account_is_authenticated_oauth_with_creds():
    session = make_session()
    store = _make_store(session)
    account = Account(
        name="Test",
        imap_host="imap.gmail.com",
        imap_port=993,
        auth_type=AuthType.oauth2,
        credentials="encrypted-token-data",
        store_id=store.id,
        maildir_path="/data/mailboxes/test-oauth2",
    )
    session.add(account)
    session.commit()
    assert account.is_authenticated is True


def test_account_is_authenticated_password():
    session = make_session()
    store = _make_store(session)
    account = Account(
        name="Test",
        imap_host="imap.example.com",
        imap_port=993,
        auth_type=AuthType.app_password,
        credentials=None,
        store_id=store.id,
        maildir_path="/data/mailboxes/test-pass",
    )
    session.add(account)
    session.commit()
    assert account.is_authenticated is True


def test_user_allowed_stores_relationship():
    session = make_session()
    s1 = MailStore(name="s1", path="/tmp/s1")
    s2 = MailStore(name="s2", path="/tmp/s2")
    session.add_all([s1, s2])
    session.commit()

    user = User(username="storeuser", role=UserRole.user, store_id=s1.id)
    session.add(user)
    session.commit()

    user.allowed_stores.append(s1)
    user.allowed_stores.append(s2)
    session.commit()
    session.refresh(user)

    assert len(user.allowed_stores) == 2
    assert s1 in user.allowed_stores
    assert s2 in user.allowed_stores


def test_group_relationships():
    session = make_session()
    store = _make_store(session)

    user1 = User(username="alice", role=UserRole.user, store_id=store.id)
    user2 = User(username="bob", role=UserRole.user, store_id=store.id)
    session.add_all([user1, user2])
    session.commit()

    account = Account(
        name="Shared",
        imap_host="imap.example.com",
        maildir_path="/data/mailboxes/shared-uuid",
        store_id=store.id,
    )
    session.add(account)
    session.commit()

    from mailfallback.models import Group

    group = Group(name="team", owner_id=user1.id)
    session.add(group)
    session.commit()

    group.members.append(user1)
    group.members.append(user2)
    group.accounts.append(account)
    session.commit()
    session.refresh(group)

    assert len(group.members) == 2
    assert len(group.accounts) == 1
    assert group in user1.groups
    assert account in group.accounts
    assert group.owner.username == "alice"
    assert group.sso_sync is False
