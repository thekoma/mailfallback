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


def list_jobs_for_account(db: Session, account_id: str, limit: int = 150) -> list[SyncJob]:
    return (
        db.query(SyncJob)
        .filter(SyncJob.account_id == account_id)
        .order_by(SyncJob.requested_at.desc())
        .limit(limit)
        .all()
    )


def cleanup_old_jobs(db: Session, account_id: str, keep: int = 150) -> int:
    jobs = (
        db.query(SyncJob.id)
        .filter(SyncJob.account_id == account_id)
        .order_by(SyncJob.requested_at.desc())
        .offset(keep)
        .all()
    )
    if not jobs:
        return 0
    ids = [j[0] for j in jobs]
    deleted = db.query(SyncJob).filter(SyncJob.id.in_(ids)).delete(synchronize_session=False)
    db.commit()
    return deleted
