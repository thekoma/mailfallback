# src/mailfallback/services/sync_service.py
from sqlalchemy.orm import Session

from mailfallback.models import JobStatus, SyncJob


def create_sync_job(db: Session, account_id: str, source: str = "api") -> SyncJob | None:
    existing = (
        db.query(SyncJob)
        .filter(
            SyncJob.account_id == account_id,
            SyncJob.status.in_([JobStatus.pending, JobStatus.running]),
        )
        .first()
    )
    if existing:
        return None

    job = SyncJob(account_id=account_id, source=source)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def get_job(db: Session, job_id: str) -> SyncJob | None:
    return db.query(SyncJob).filter(SyncJob.id == job_id).first()


def list_jobs_for_account(db: Session, account_id: str, limit: int = 50) -> list[SyncJob]:
    return (
        db.query(SyncJob)
        .filter(SyncJob.account_id == account_id)
        .order_by(SyncJob.requested_at.desc())
        .limit(limit)
        .all()
    )
