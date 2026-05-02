# tests/test_sync_service.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mailfallback.db import Base
from mailfallback.models import Account, JobStatus, MailStore
from mailfallback.services.sync_service import create_sync_job, get_job, list_jobs_for_account


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


def _make_account(session):
    store = _make_store(session)
    account = Account(
        name="Test",
        imap_host="imap.test.com",
        maildir_path="/data/test",
        store_id=store.id,
    )
    session.add(account)
    session.commit()
    return account


def test_create_job():
    session = make_session()
    account = _make_account(session)
    job = create_sync_job(session, account.id, source="api")
    assert job.status == JobStatus.pending
    assert job.source == "api"
    assert job.account_id == account.id


def test_dedup_pending_job():
    session = make_session()
    account = _make_account(session)
    create_sync_job(session, account.id, source="api")
    job2 = create_sync_job(session, account.id, source="scheduler")
    assert job2 is None


def test_dedup_running_job():
    session = make_session()
    account = _make_account(session)
    job1 = create_sync_job(session, account.id, source="api")
    job1.status = JobStatus.running
    session.commit()
    job2 = create_sync_job(session, account.id, source="api")
    assert job2 is None


def test_allows_job_after_completed():
    session = make_session()
    account = _make_account(session)
    job1 = create_sync_job(session, account.id, source="api")
    job1.status = JobStatus.completed
    session.commit()
    job2 = create_sync_job(session, account.id, source="api")
    assert job2 is not None
    assert job2.id != job1.id


def test_get_job():
    session = make_session()
    account = _make_account(session)
    job = create_sync_job(session, account.id, source="api")
    fetched = get_job(session, job.id)
    assert fetched is not None
    assert fetched.id == job.id


def test_list_jobs_for_account():
    session = make_session()
    account = _make_account(session)
    create_sync_job(session, account.id, source="api")
    jobs = list_jobs_for_account(session, account.id)
    assert len(jobs) == 1
