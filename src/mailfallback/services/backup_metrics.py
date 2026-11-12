"""Scrape-local gauges of persisted backup state; never access storage."""

from datetime import UTC, datetime

from prometheus_client import CollectorRegistry, Gauge, generate_latest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mailfallback.models import BackupJob, BackupPolicy, BackupStatus, JobStatus, Repository


def _timestamp(value: datetime | None) -> float:
    if value is None:
        return 0
    # SQLite and legacy naive values represent UTC, never the scraper's local zone.
    return value.replace(tzinfo=UTC).timestamp() if value.tzinfo is None else value.timestamp()


def render_backup_metrics(db: Session) -> str:
    """Lifetime-policy history has no destination labels: it is not storage verification."""
    registry = CollectorRegistry()
    policy_labels = ["policy_id", "account_id"]

    def gauge(name, description, labels):
        return Gauge("mailfallback_" + name, description, labels, registry=registry)

    success = gauge(
        "backup_last_success_timestamp",
        "Last recorded policy success, Unix seconds; 0 means unknown",
        policy_labels,
    )
    run = gauge(
        "backup_last_run_timestamp",
        "Last recorded policy run start, Unix seconds; 0 means unknown",
        policy_labels,
    )
    status = gauge(
        "backup_last_status", "Current recorded policy status (one-hot)", [*policy_labels, "status"]
    )
    snapshots = gauge(
        "backup_snapshot_count",
        "Cached lifetime-policy snapshot count, not current destination verification",
        policy_labels,
    )
    duration = gauge(
        "backup_last_duration_seconds",
        "Duration of latest successfully completed policy job",
        policy_labels,
    )
    enabled = gauge("backup_policy_enabled", "Whether the backup policy is enabled", policy_labels)

    ranked = (
        select(
            BackupJob.policy_id,
            BackupJob.started_at,
            BackupJob.completed_at,
            func.row_number()
            .over(
                partition_by=BackupJob.policy_id,
                order_by=(BackupJob.completed_at.desc(), BackupJob.id.desc()),
            )
            .label("rank"),
        )
        .where(
            BackupJob.status == JobStatus.completed,
            BackupJob.started_at.is_not(None),
            BackupJob.completed_at.is_not(None),
        )
        .subquery()
    )
    latest = {row.policy_id: row for row in db.execute(select(ranked).where(ranked.c.rank == 1))}
    for policy in db.scalars(select(BackupPolicy)):
        labels = (policy.id, policy.account_id)
        success.labels(*labels).set(_timestamp(policy.last_successful_run_at))
        run.labels(*labels).set(_timestamp(policy.last_run_at))
        enabled.labels(*labels).set(int(policy.enabled))
        snapshots.labels(*labels).set(policy.last_snapshot_count)
        for state in BackupStatus:
            status.labels(*labels, state.value).set(int(policy.last_status == state))
        job = latest.get(policy.id)
        if job is not None:
            duration.labels(*labels).set(_timestamp(job.completed_at) - _timestamp(job.started_at))

    repo_labels = ["repository_id"]
    config_success = gauge(
        "config_backup_last_success_timestamp",
        "Last recorded configuration backup success, Unix seconds; 0 means unknown",
        repo_labels,
    )
    config_enabled = gauge(
        "config_backup_enabled", "Whether configuration backup is enabled", repo_labels
    )
    config_status = gauge(
        "config_backup_last_status",
        "Last configuration backup status (one-hot)",
        [*repo_labels, "status"],
    )
    for repo in db.scalars(select(Repository)):
        config_success.labels(repo.id).set(_timestamp(repo.last_config_backup_success_at))
        config_enabled.labels(repo.id).set(int(repo.config_backup_enabled))
        current = (
            repo.last_config_backup_status
            if repo.last_config_backup_status in {"ok", "failed"}
            else "unknown"
        )
        for state in ("ok", "failed", "unknown"):
            config_status.labels(repo.id, state).set(int(current == state))
    return generate_latest(registry).decode()
