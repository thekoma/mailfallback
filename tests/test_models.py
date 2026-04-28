# tests/test_models.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mailfallback.db import Base
from mailfallback.models import (
    Account,
    AuthType,
    JobStatus,
    SyncJob,
    SyncState,
    User,
    UserRole,
    account_owners,
)


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_create_user():
    session = make_session()
    user = User(username="admin", role=UserRole.admin)
    session.add(user)
    session.commit()
    assert user.id is not None
    assert user.username == "admin"
    assert user.role == UserRole.admin


def test_create_account_with_owner():
    session = make_session()
    user = User(username="testuser")
    account = Account(
        name="Gmail",
        imap_host="imap.gmail.com",
        imap_port=993,
        maildir_path="/data/mailboxes/gmail",
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
    account = Account(
        name="Work",
        imap_host="imap.work.com",
        maildir_path="/data/mailboxes/work",
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
    account = Account(
        name="Test",
        imap_host="imap.test.com",
        maildir_path="/data/mailboxes/test",
    )
    session.add(account)
    session.commit()
    assert account.auth_type == AuthType.app_password
    assert account.sync_state == SyncState.idle
    assert account.enabled is True
    assert account.imap_port == 993
