# tests/test_models.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mailfallback.db import Base
from mailfallback.models import (
    Account,
    AuditLog,
    AuthType,
    JobStatus,
    MailStore,
    SyncJob,
    SyncState,
    User,
    UserRole,
)


def make_session():
    from sqlalchemy import event

    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        # mail_index schema needs an attached DB on SQLite (no native schemas)
        cursor.execute("ATTACH DATABASE ':memory:' AS mail_index")
        cursor.close()

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


def test_user_preferences_default(db_session, default_store):
    user = User(username="prefuser", role=UserRole.user, store_id=default_store.id)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    assert user.preferences == {}


def test_user_preferences_stores_theme(db_session, default_store):
    user = User(
        username="themeuser",
        role=UserRole.user,
        store_id=default_store.id,
        preferences={"theme": "dark"},
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    assert user.preferences["theme"] == "dark"


def test_audit_log_creation(db_session, default_store):
    user = User(username="auditor", role=UserRole.admin, store_id=default_store.id)
    db_session.add(user)
    db_session.commit()
    log = AuditLog(
        user_id=user.id,
        username=user.username,
        action="user.create",
        resource_type="user",
        resource_id="some-id",
        resource_name="testuser",
        ip_address="127.0.0.1",
    )
    db_session.add(log)
    db_session.commit()
    db_session.refresh(log)
    assert log.action == "user.create"
    assert log.username == "auditor"
    assert log.timestamp is not None


def test_audit_log_survives_user_deletion(db_session, default_store):
    user = User(username="deleteme", role=UserRole.user, store_id=default_store.id)
    db_session.add(user)
    db_session.commit()
    log = AuditLog(
        user_id=user.id,
        username="deleteme",
        action="test.action",
        resource_type="test",
    )
    db_session.add(log)
    db_session.commit()
    db_session.delete(user)
    db_session.commit()
    db_session.refresh(log)
    assert log.user_id is None
    assert log.username == "deleteme"


def test_recovery_defaults_to_persistent(db_session, default_store):
    from mailfallback.models import Account, Recovery, RecoveryKind, RecoveryStatus, Repository

    repo = Repository(
        name="test", backend_type="local", local_path="/tmp/test", restic_password="x"
    )
    db_session.add(repo)
    acct = Account(
        name="a",
        imap_host="imap.example.com",
        store=default_store,
        maildir_path="/data/mailboxes/a",
    )
    db_session.add(acct)
    db_session.commit()

    r = Recovery(
        account_id=acct.id,
        repository_id=repo.id,
        snapshot_id="abc123",
        restore_path="/tmp/r",
        status=RecoveryStatus.ready,
    )
    db_session.add(r)
    db_session.commit()
    db_session.refresh(r)

    assert r.kind == RecoveryKind.persistent
    assert r.ttl_minutes is None
    assert r.last_accessed_at is not None


def test_recovery_can_be_ephemeral_with_ttl(db_session, default_store):
    from mailfallback.models import Account, Recovery, RecoveryKind, RecoveryStatus, Repository

    repo = Repository(
        name="test", backend_type="local", local_path="/tmp/test", restic_password="x"
    )
    db_session.add(repo)
    acct = Account(
        name="a",
        imap_host="imap.example.com",
        store=default_store,
        maildir_path="/data/mailboxes/a",
    )
    db_session.add(acct)
    db_session.commit()

    r = Recovery(
        account_id=acct.id,
        repository_id=repo.id,
        snapshot_id="abc",
        restore_path="/tmp/r",
        status=RecoveryStatus.ready,
        kind=RecoveryKind.ephemeral,
        ttl_minutes=30,
    )
    db_session.add(r)
    db_session.commit()
    db_session.refresh(r)

    assert r.kind == RecoveryKind.ephemeral
    assert r.ttl_minutes == 30


def test_mail_index_message_round_trip(db_session, default_store):
    from datetime import UTC, datetime

    from mailfallback.models import Account, MailIndexMessage

    acct = Account(
        name="a",
        store=default_store,
        maildir_path="/data/mailboxes/a",
        imap_host="imap.example.com",
    )
    db_session.add(acct)
    db_session.commit()

    msg = MailIndexMessage(
        account_id=acct.id,
        message_id_hash=b"\x00" * 20,
        message_id="<abc@host>",
        date_sent=datetime(2026, 5, 11, 12, 0, tzinfo=UTC),
        from_addr="alice@example.com",
        from_name="Alice",
        subject="Hello",
        to_addrs=["bob@example.com"],
        folder_path="INBOX",
        maildir_filename="1234.M567.host:2,S",
        size_bytes=1024,
    )
    db_session.add(msg)
    db_session.commit()
    db_session.refresh(msg)

    assert msg.deleted_at is None
    assert msg.first_seen_at is not None
    assert msg.last_seen_at is not None
    assert msg.to_addrs == ["bob@example.com"]


def test_snapshot_message_round_trip(db_session, default_store):
    from mailfallback.models import Account, MailIndexMessage, SnapshotMessage

    acct = Account(
        name="a",
        store=default_store,
        maildir_path="/data/mailboxes/a",
        imap_host="imap.example.com",
    )
    db_session.add(acct)
    db_session.commit()

    msg = MailIndexMessage(
        account_id=acct.id,
        message_id_hash=b"\x01" * 20,
        message_id="<def@host>",
        folder_path="INBOX",
        maildir_filename="2345.host:2,",
    )
    db_session.add(msg)
    db_session.commit()

    snap = SnapshotMessage(
        snapshot_id="abc12345",
        account_id=acct.id,
        message_id_hash=b"\x01" * 20,
    )
    db_session.add(snap)
    db_session.commit()
    db_session.refresh(snap)

    assert snap.snapshot_id == "abc12345"


def test_rebuild_status_defaults(db_session, default_store):
    from mailfallback.models import Account, MailIndexRebuildStatus

    acct = Account(
        name="a",
        store=default_store,
        maildir_path="/data/mailboxes/a",
        imap_host="imap.example.com",
    )
    db_session.add(acct)
    db_session.commit()

    rs = MailIndexRebuildStatus(account_id=acct.id, state="idle")
    db_session.add(rs)
    db_session.commit()
    db_session.refresh(rs)

    assert rs.state == "idle"
    assert rs.last_indexed_at is None
