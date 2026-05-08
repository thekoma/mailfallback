import pytest

from mailfallback.models import Account, JobStatus, RestoreMode, User
from mailfallback.services.restore_service import (
    cancel_restore_job,
    create_restore_job,
    get_restore_job,
    list_restore_jobs,
)


@pytest.fixture
def restore_fixtures(db_session, default_store):
    user = User(username="restorer", password_hash="x", store_id=default_store.id)
    db_session.add(user)
    db_session.flush()
    src = Account(
        name="source",
        imap_host="imap.src.com",
        imap_port=993,
        maildir_path="/data/mailboxes/src",
        store_id=default_store.id,
        credentials="encrypted-creds",
    )
    tgt = Account(
        name="target",
        imap_host="imap.tgt.com",
        imap_port=993,
        maildir_path="/data/mailboxes/tgt",
        store_id=default_store.id,
        credentials="encrypted-creds",
    )
    db_session.add_all([src, tgt])
    db_session.commit()
    db_session.refresh(user)
    db_session.refresh(src)
    db_session.refresh(tgt)
    return {"user": user, "source": src, "target": tgt}


def test_create_restore_job(db_session, restore_fixtures):
    f = restore_fixtures
    job = create_restore_job(
        db_session,
        source_account_id=f["source"].id,
        target_account_id=f["target"].id,
        restore_mode="full",
        requested_by=f["user"].id,
    )
    assert job is not None
    assert job.status == JobStatus.pending
    assert job.restore_mode == RestoreMode.full
    assert job.source_account_id == f["source"].id
    assert job.target_account_id == f["target"].id


def test_create_restore_job_rejects_suspended_source(db_session, restore_fixtures):
    f = restore_fixtures
    f["source"].suspended = True
    db_session.commit()
    job = create_restore_job(
        db_session,
        source_account_id=f["source"].id,
        target_account_id=f["target"].id,
        restore_mode="full",
        requested_by=f["user"].id,
    )
    assert job is None


def test_create_restore_job_rejects_suspended_target(db_session, restore_fixtures):
    f = restore_fixtures
    f["target"].suspended = True
    db_session.commit()
    job = create_restore_job(
        db_session,
        source_account_id=f["source"].id,
        target_account_id=f["target"].id,
        restore_mode="full",
        requested_by=f["user"].id,
    )
    assert job is None


def test_create_restore_job_rejects_migrating(db_session, restore_fixtures):
    f = restore_fixtures
    f["target"].migrating = True
    db_session.commit()
    job = create_restore_job(
        db_session,
        source_account_id=f["source"].id,
        target_account_id=f["target"].id,
        restore_mode="full",
        requested_by=f["user"].id,
    )
    assert job is None


def test_create_restore_job_rejects_no_credentials(db_session, restore_fixtures):
    f = restore_fixtures
    f["target"].credentials = None
    db_session.commit()
    job = create_restore_job(
        db_session,
        source_account_id=f["source"].id,
        target_account_id=f["target"].id,
        restore_mode="full",
        requested_by=f["user"].id,
    )
    assert job is None


def test_create_restore_job_rejects_duplicate(db_session, restore_fixtures):
    f = restore_fixtures
    create_restore_job(
        db_session,
        source_account_id=f["source"].id,
        target_account_id=f["target"].id,
        restore_mode="full",
        requested_by=f["user"].id,
    )
    second = create_restore_job(
        db_session,
        source_account_id=f["source"].id,
        target_account_id=f["target"].id,
        restore_mode="full",
        requested_by=f["user"].id,
    )
    assert second is None


def test_get_restore_job(db_session, restore_fixtures):
    f = restore_fixtures
    job = create_restore_job(
        db_session,
        source_account_id=f["source"].id,
        target_account_id=f["target"].id,
        restore_mode="folder",
        requested_by=f["user"].id,
        selected_folders=["INBOX", "Sent"],
    )
    fetched = get_restore_job(db_session, job.id)
    assert fetched.id == job.id
    assert fetched.selected_folders == ["INBOX", "Sent"]


def test_list_restore_jobs(db_session, restore_fixtures):
    f = restore_fixtures
    create_restore_job(
        db_session,
        source_account_id=f["source"].id,
        target_account_id=f["target"].id,
        restore_mode="full",
        requested_by=f["user"].id,
    )
    jobs = list_restore_jobs(db_session, f["source"].id)
    assert len(jobs) == 1


def test_cancel_restore_job(db_session, restore_fixtures):
    f = restore_fixtures
    job = create_restore_job(
        db_session,
        source_account_id=f["source"].id,
        target_account_id=f["target"].id,
        restore_mode="full",
        requested_by=f["user"].id,
    )
    job.status = JobStatus.running
    db_session.commit()
    result = cancel_restore_job(db_session, job.id)
    assert result is True
    db_session.refresh(job)
    assert job.status == JobStatus.cancelled
    assert job.error == "Cancelled by user"
