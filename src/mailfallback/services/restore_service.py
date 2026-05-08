from sqlalchemy.orm import Session

from mailfallback.models import Account, JobStatus, RestoreJob, RestoreMode


def create_restore_job(
    db: Session,
    *,
    source_account_id: str,
    target_account_id: str,
    restore_mode: str,
    requested_by: str,
    folder_mapping: str = "original",
    skip_duplicates: bool = True,
    selected_folders: list[str] | None = None,
    selected_uids: dict | None = None,
) -> RestoreJob | None:
    source = db.query(Account).filter(Account.id == source_account_id).first()
    target = db.query(Account).filter(Account.id == target_account_id).first()
    if not source or not target:
        return None

    if source.suspended or source.migrating:
        return None
    if target.suspended or target.migrating:
        return None
    if not target.credentials:
        return None

    existing = (
        db.query(RestoreJob)
        .filter(
            RestoreJob.source_account_id == source_account_id,
            RestoreJob.target_account_id == target_account_id,
            RestoreJob.status.in_([JobStatus.pending, JobStatus.running]),
        )
        .first()
    )
    if existing:
        return None

    job = RestoreJob(
        source_account_id=source_account_id,
        target_account_id=target_account_id,
        restore_mode=RestoreMode(restore_mode),
        folder_mapping=folder_mapping,
        skip_duplicates=skip_duplicates,
        selected_folders=selected_folders,
        selected_uids=selected_uids,
        requested_by=requested_by,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def get_restore_job(db: Session, job_id: str) -> RestoreJob | None:
    return db.query(RestoreJob).filter(RestoreJob.id == job_id).first()


def list_restore_jobs(db: Session, account_id: str, limit: int = 50) -> list[RestoreJob]:
    return (
        db.query(RestoreJob)
        .filter(
            (RestoreJob.source_account_id == account_id)
            | (RestoreJob.target_account_id == account_id)
        )
        .order_by(RestoreJob.requested_at.desc())
        .limit(limit)
        .all()
    )


def list_restore_jobs_for_user(db: Session, user_id: str, limit: int = 50) -> list[RestoreJob]:
    return (
        db.query(RestoreJob)
        .filter(RestoreJob.requested_by == user_id)
        .order_by(RestoreJob.requested_at.desc())
        .limit(limit)
        .all()
    )


def cancel_restore_job(db: Session, job_id: str) -> bool:
    job = db.query(RestoreJob).filter(RestoreJob.id == job_id).first()
    if not job or job.status not in (JobStatus.pending, JobStatus.running):
        return False
    job.status = JobStatus.cancelled
    job.error = "Cancelled by user"
    db.commit()
    return True
