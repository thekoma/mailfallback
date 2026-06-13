# tests/test_models.py
import pytest
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


def test_account_sync_budget_columns_defaults():
    """Migration-021 columns: the daily byte ledger starts at 0 (NOT NULL,
    server default) and every budget/pause/initial-sync marker starts NULL —
    a fresh account is in the initial-sync regime with no pause and the
    provider-default budget."""
    session = make_session()
    store = _make_store(session)
    account = Account(
        name="Budget",
        imap_host="imap.example.com",
        maildir_path="/data/mailboxes/budget",
        store_id=store.id,
    )
    session.add(account)
    session.commit()
    session.refresh(account)
    assert account.bytes_synced_today == 0
    assert account.traffic_date is None
    assert account.daily_sync_budget_mb is None
    assert account.sync_paused_until is None
    assert account.pause_reason is None
    assert account.initial_sync_completed_at is None
    assert account.initial_sync_total_messages is None


def test_sync_job_failure_kind_plain_string():
    """failure_kind is a PLAIN string (no enum — new kinds must not need a
    migration): NULL by default, classifier values persist as-is."""
    session = make_session()
    store = _make_store(session)
    account = Account(
        name="FK",
        imap_host="imap.example.com",
        maildir_path="/data/mailboxes/fk",
        store_id=store.id,
    )
    session.add(account)
    session.commit()
    job = SyncJob(account_id=account.id)
    session.add(job)
    session.commit()
    session.refresh(job)
    assert job.failure_kind is None
    job.failure_kind = "budget_paused"
    session.commit()
    session.refresh(job)
    assert job.failure_kind == "budget_paused"
    # The plain-string PROOF: a value outside the documented vocabulary
    # round-trips too — an Enum column would reject it at flush time.
    job.failure_kind = "future_kind"
    session.commit()
    session.refresh(job)
    assert job.failure_kind == "future_kind"


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


def test_repository_attachment_unique_per_prefix(db_session):
    from sqlalchemy.exc import IntegrityError

    from mailfallback.models import Repository, RepositoryAttachment

    store = MailStore(name="s", path="/data/m")
    db_session.add(store)
    db_session.flush()
    acc = Account(name="a", imap_host="h", maildir_path="/data/m/x", store_id=store.id)
    repo = Repository(name="r", backend_type="s3", restic_password="enc")
    db_session.add_all([acc, repo])
    db_session.flush()

    db_session.add(
        RepositoryAttachment(repository_id=repo.id, account_id=acc.id, prefix="old-uuid")
    )
    db_session.commit()

    db_session.add(
        RepositoryAttachment(repository_id=repo.id, account_id=acc.id, prefix="old-uuid")
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_repository_config_backup_defaults(db_session):
    from mailfallback.models import Repository

    repo = Repository(name="r2", backend_type="local", local_path="enc", restic_password="enc")
    db_session.add(repo)
    db_session.commit()
    db_session.refresh(repo)
    assert repo.config_backup_enabled is False
    assert repo.config_backup_passphrase is None
    assert repo.last_config_backup_at is None
    assert repo.last_config_backup_status is None
    assert repo.last_config_backup_error is None


def test_delete_account_cascades_repo_attachments(db_session, default_store):
    from mailfallback.models import Repository, RepositoryAttachment

    acc = Account(
        name="a",
        imap_host="h",
        maildir_path="/data/mailboxes/a",
        store=default_store,
    )
    repo = Repository(name="r", backend_type="s3", restic_password="enc")
    db_session.add_all([acc, repo])
    db_session.flush()
    db_session.add(
        RepositoryAttachment(repository_id=repo.id, account_id=acc.id, prefix="old-uuid")
    )
    db_session.commit()

    db_session.delete(acc)
    db_session.commit()

    assert db_session.query(RepositoryAttachment).count() == 0


def test_delete_repository_cascades_repo_attachments(db_session, default_store):
    from mailfallback.models import Repository, RepositoryAttachment

    acc = Account(
        name="a",
        imap_host="h",
        maildir_path="/data/mailboxes/a",
        store=default_store,
    )
    repo = Repository(name="r", backend_type="s3", restic_password="enc")
    db_session.add_all([acc, repo])
    db_session.flush()
    db_session.add(
        RepositoryAttachment(repository_id=repo.id, account_id=acc.id, prefix="old-uuid")
    )
    db_session.commit()

    db_session.delete(repo)
    db_session.commit()

    assert db_session.query(RepositoryAttachment).count() == 0
    assert db_session.query(Account).count() == 1


def test_repository_attachment_password_nullable(db_session):
    from mailfallback.models import Repository, RepositoryAttachment

    store = MailStore(name="s16", path="/data/m16")
    db_session.add(store)
    db_session.flush()
    acc = Account(name="a16", imap_host="h", maildir_path="/data/m16/x", store_id=store.id)
    repo = Repository(name="r16", backend_type="s3", restic_password="enc")
    db_session.add_all([acc, repo])
    db_session.flush()

    att = RepositoryAttachment(repository_id=repo.id, account_id=acc.id, prefix="p16")
    db_session.add(att)
    db_session.commit()
    db_session.refresh(att)
    assert att.restic_password is None

    att.restic_password = "enc-override"  # pragma: allowlist secret
    db_session.commit()
    db_session.refresh(att)
    assert att.restic_password == "enc-override"  # pragma: allowlist secret


def test_user_allowed_repositories_relationship(db_session):
    from mailfallback.models import Repository

    store = MailStore(name="s17", path="/data/m17")
    db_session.add(store)
    db_session.flush()
    user = User(username="u17", password_hash="x", role=UserRole.user, store_id=store.id)
    repo = Repository(name="r17", backend_type="s3", restic_password="enc")
    db_session.add_all([user, repo])
    db_session.flush()

    user.allowed_repositories.append(repo)
    db_session.commit()
    db_session.refresh(user)

    assert [r.id for r in user.allowed_repositories] == [repo.id]
    # deleting the repo removes the grant row
    db_session.delete(repo)
    db_session.commit()
    db_session.refresh(user)
    assert user.allowed_repositories == []


def _make_staging(db_session, default_store, username="stager"):
    from datetime import UTC, datetime, timedelta

    from mailfallback.models import StagingArea, StagingMessage

    user = User(username=username, role=UserRole.user, store_id=default_store.id)
    acct = Account(
        name="src",
        imap_host="imap.example.com",
        maildir_path=f"/data/mailboxes/{username}-src",
        store=default_store,
    )
    db_session.add_all([user, acct])
    db_session.flush()

    area = StagingArea(
        user_id=user.id,
        expires_at=datetime.now(UTC) + timedelta(minutes=10080),
    )
    db_session.add(area)
    db_session.flush()
    msg = StagingMessage(
        staging_id=area.id,
        source_account_id=acct.id,
        message_id_hash=b"\x02" * 20,
        original_folder="INBOX",
        staged_filename="1234.M567.host:2,S",
        size_bytes=2048,
    )
    db_session.add(msg)
    db_session.commit()
    return user, area, msg


def test_staging_area_defaults_and_unique_per_user(db_session, default_store):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy.exc import IntegrityError

    from mailfallback.models import StagingArea

    user, area, msg = _make_staging(db_session, default_store)
    db_session.refresh(area)
    assert area.created_at is not None
    assert area.max_bytes == 0
    assert area.bytes_used == 0
    assert msg.staged_at is not None
    assert msg.staging.id == area.id

    db_session.add(
        StagingArea(user_id=user.id, expires_at=datetime.now(UTC) + timedelta(minutes=1))
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_delete_staging_area_cascades_messages(db_session, default_store):
    from mailfallback.models import StagingArea, StagingMessage

    _, area, _ = _make_staging(db_session, default_store)
    db_session.delete(area)
    db_session.commit()
    assert db_session.query(StagingArea).count() == 0
    assert db_session.query(StagingMessage).count() == 0


def test_delete_user_cascades_staging(db_session, default_store):
    from mailfallback.models import StagingArea, StagingMessage

    user, _, _ = _make_staging(db_session, default_store)
    db_session.delete(user)
    db_session.commit()
    assert db_session.query(StagingArea).count() == 0
    assert db_session.query(StagingMessage).count() == 0
