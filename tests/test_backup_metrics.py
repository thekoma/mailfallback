"""Backup scrape contracts: persisted state, identity and bounded collection."""

from datetime import UTC, datetime, timedelta

from prometheus_client.parser import text_string_to_metric_families
from sqlalchemy import event

from mailfallback.models import (
    Account,
    BackendType,
    BackupJob,
    BackupPolicy,
    BackupStatus,
    JobStatus,
    Repository,
)

NOW = datetime(2026, 1, 2, tzinfo=UTC)


def _policy(db, store, suffix="1"):
    account = Account(
        name="duplicate",
        imap_host="imap.example.org",
        maildir_path=f"/data/{suffix}",
        store_id=store.id,
    )
    repo = Repository(name="duplicate", backend_type=BackendType.local, restic_password="encrypted")
    db.add_all([account, repo])
    db.flush()
    policy = BackupPolicy(account_id=account.id, destination_id=repo.id)
    db.add(policy)
    db.commit()
    return policy, repo


def _samples(client):
    response = client.get("/metrics", headers={"Authorization": "Bearer test-key"})
    assert response.status_code == 200
    return {
        (s.name, tuple(sorted(s.labels.items()))): s.value
        for f in text_string_to_metric_families(response.text)
        for s in f.samples
    }


def _value(samples, name, **labels):
    return samples[("mailfallback_" + name, tuple(sorted(labels.items())))]


def test_policy_state_identity_and_latest_completed_duration(client, db_session, default_store):
    p, _ = _policy(db_session, default_store)
    other, _ = _policy(db_session, default_store, "2")
    p.last_successful_run_at = NOW
    p.last_run_at = NOW + timedelta(hours=1)
    p.last_status = BackupStatus.failed
    p.last_snapshot_count = 7
    other.enabled = False
    for status, end, seconds in [
        (JobStatus.completed, NOW, 9),
        (JobStatus.completed, NOW + timedelta(minutes=1), 12),
        (JobStatus.failed, NOW + timedelta(minutes=2), 30),
    ]:
        db_session.add(
            BackupJob(
                policy_id=p.id,
                account_id=p.account_id,
                status=status,
                started_at=end - timedelta(seconds=seconds),
                completed_at=end,
            )
        )
    db_session.commit()
    samples = _samples(client)
    labels = {"policy_id": p.id, "account_id": p.account_id}
    assert _value(samples, "backup_last_success_timestamp", **labels) == 1767312000
    assert _value(samples, "backup_last_run_timestamp", **labels) == 1767315600
    assert _value(samples, "backup_snapshot_count", **labels) == 7
    assert _value(samples, "backup_last_duration_seconds", **labels) == 12
    assert _value(samples, "backup_last_status", status="failed", **labels) == 1
    assert _value(samples, "backup_last_status", status="completed", **labels) == 0
    assert (
        _value(samples, "backup_policy_enabled", policy_id=other.id, account_id=other.account_id)
        == 0
    )
    assert (
        _value(
            samples,
            "backup_last_success_timestamp",
            policy_id=other.id,
            account_id=other.account_id,
        )
        == 0
    )
    assert (
        "mailfallback_backup_last_duration_seconds",
        tuple(sorted({"policy_id": other.id, "account_id": other.account_id}.items())),
    ) not in samples
    assert not any(
        "destination" in dict(labels) or "account" in dict(labels)
        for name, labels in samples
        if name.startswith("mailfallback_backup_")
    )


def test_config_unknown_disabled_and_no_policy_series(client, db_session):
    repo = Repository(name="unused", backend_type=BackendType.local, restic_password="encrypted")
    db_session.add(repo)
    db_session.commit()
    samples = _samples(client)
    assert _value(samples, "config_backup_last_success_timestamp", repository_id=repo.id) == 0
    assert _value(samples, "config_backup_enabled", repository_id=repo.id) == 0
    assert (
        _value(samples, "config_backup_last_status", repository_id=repo.id, status="unknown") == 1
    )
    assert not any(name.startswith("mailfallback_backup_") for name, _ in samples)


def test_deleted_policy_does_not_leave_stale_series(client, db_session, default_store):
    p, _ = _policy(db_session, default_store)
    assert any(name.startswith("mailfallback_backup_") for name, _ in _samples(client))
    db_session.delete(p)
    db_session.commit()
    assert not any(name.startswith("mailfallback_backup_") for name, _ in _samples(client))


def test_backup_query_count_does_not_grow_with_policies(client, db_session, default_store):
    _policy(db_session, default_store)

    def count():
        statements = []

        def record(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("SELECT") and any(
                t in statement for t in ("account_backups", "backup_jobs", "backup_destinations")
            ):
                statements.append(statement)

        engine = db_session.get_bind()
        event.listen(engine, "before_cursor_execute", record)
        try:
            _samples(client)
        finally:
            event.remove(engine, "before_cursor_execute", record)
        return len(statements)

    initial = count()
    assert initial > 0
    for i in range(2, 6):
        _policy(db_session, default_store, str(i))
    assert count() == initial


def test_config_success_failure_status_and_deleted_repository(client, db_session):
    repo = Repository(
        name="private name",
        backend_type=BackendType.local,
        restic_password="encrypted",
        config_backup_enabled=True,
        last_config_backup_success_at=NOW,
        last_config_backup_status="failed",
    )
    db_session.add(repo)
    db_session.commit()
    samples = _samples(client)
    assert (
        _value(samples, "config_backup_last_success_timestamp", repository_id=repo.id) == 1767312000
    )
    assert _value(samples, "config_backup_enabled", repository_id=repo.id) == 1
    assert _value(samples, "config_backup_last_status", repository_id=repo.id, status="failed") == 1
    assert _value(samples, "config_backup_last_status", repository_id=repo.id, status="ok") == 0
    db_session.delete(repo)
    db_session.commit()
    assert not any(name.startswith("mailfallback_config_backup_") for name, _ in _samples(client))


def test_repointed_policy_retains_identity_without_destination_claim(
    client, db_session, default_store
):
    p, _ = _policy(db_session, default_store)
    p.last_successful_run_at = NOW
    p.last_snapshot_count = 5
    db_session.commit()
    before = {
        key: value
        for key, value in _samples(client).items()
        if key[0].startswith("mailfallback_backup_")
    }
    repo = Repository(
        name="new empty destination", backend_type=BackendType.local, restic_password="encrypted"
    )
    db_session.add(repo)
    db_session.flush()
    p.destination_id = repo.id
    db_session.commit()
    after = {
        key: value
        for key, value in _samples(client).items()
        if key[0].startswith("mailfallback_backup_")
    }
    assert before == after


def test_timestamp_naive_values_are_utc_and_offsets_are_respected():
    from datetime import timezone

    from mailfallback.services.backup_metrics import _timestamp

    assert _timestamp(datetime(2026, 1, 2)) == 1767312000
    assert _timestamp(datetime(2026, 1, 2, 2, tzinfo=timezone(timedelta(hours=2)))) == 1767312000


def test_bad_key_cannot_read_backup_state(client, db_session, default_store):
    _policy(db_session, default_store)
    response = client.get("/metrics", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401
    assert "policy_id" not in response.text
