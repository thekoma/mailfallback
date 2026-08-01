"""Tests for backup_service — BackupJob queries and retention."""

from datetime import UTC, datetime, timedelta

import pytest

from mailfallback.models import (
    Account,
    BackupJob,
    BackupPolicy,
    JobStatus,
    Repository,
    RetentionPreset,
)
from mailfallback.services.backup_service import (
    cleanup_old_backup_jobs,
    get_job,
    list_jobs_for_account,
)


@pytest.fixture
def policy(db_session, default_store):
    account = Account(
        name="Main gMail",
        imap_host="imap.gmail.com",
        maildir_path="/data/mailboxes/test-uuid",
        store_id=default_store.id,
    )
    repo = Repository(name="Repo01", backend_type="s3", restic_password="enc-password")
    db_session.add_all([account, repo])
    db_session.commit()
    p = BackupPolicy(
        account_id=account.id,
        destination_id=repo.id,
        retention_preset=RetentionPreset.standard,
    )
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def _mk_job(db_session, policy, *, minutes_ago: int) -> BackupJob:
    job = BackupJob(
        policy_id=policy.id,
        account_id=policy.account_id,
        status=JobStatus.completed,
        requested_at=datetime.now(UTC) - timedelta(minutes=minutes_ago),
    )
    db_session.add(job)
    db_session.commit()
    return job


def test_get_job_returns_row(db_session, policy):
    job = _mk_job(db_session, policy, minutes_ago=1)
    assert get_job(db_session, job.id).id == job.id


def test_get_job_returns_none_for_unknown_id(db_session):
    assert get_job(db_session, "no-such-id") is None


def test_list_jobs_newest_first(db_session, policy):
    old = _mk_job(db_session, policy, minutes_ago=10)
    new = _mk_job(db_session, policy, minutes_ago=1)
    ids = [j.id for j in list_jobs_for_account(db_session, policy.account_id)]
    assert ids == [new.id, old.id]


def test_cleanup_keeps_newest_and_deletes_the_rest(db_session, policy):
    for i in range(5):
        _mk_job(db_session, policy, minutes_ago=i)
    deleted = cleanup_old_backup_jobs(db_session, policy.account_id, keep=2)
    assert deleted == 3
    assert len(list_jobs_for_account(db_session, policy.account_id)) == 2


def test_cleanup_is_a_noop_below_the_keep_threshold(db_session, policy):
    _mk_job(db_session, policy, minutes_ago=1)
    assert cleanup_old_backup_jobs(db_session, policy.account_id, keep=150) == 0


def test_bytes_columns_hold_values_above_int32(db_session, policy):
    """Regression guard: migration 023 — Integer overflows past ~2.1 GB."""
    job = BackupJob(
        policy_id=policy.id,
        account_id=policy.account_id,
        status=JobStatus.completed,
        bytes_processed=14_000_000_000,
        bytes_added=3_000_000_000,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    assert job.bytes_processed == 14_000_000_000
    assert job.bytes_added == 3_000_000_000


def test_deleting_the_policy_cascades_to_its_jobs(db_session, policy):
    _mk_job(db_session, policy, minutes_ago=1)
    account_id = policy.account_id
    db_session.delete(policy)
    db_session.commit()
    assert list_jobs_for_account(db_session, account_id) == []
