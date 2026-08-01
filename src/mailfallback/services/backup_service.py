"""Query and retention helpers for BackupJob rows.

Execution and crash recovery live in backup_worker; this module is the read
side, mirroring the sync_service / sync_worker split.
"""

from sqlalchemy.orm import Session

from mailfallback.models import BackupJob


def get_job(db: Session, job_id: str) -> BackupJob | None:
    return db.query(BackupJob).filter(BackupJob.id == job_id).first()


def list_jobs_for_account(db: Session, account_id: str, limit: int = 150) -> list[BackupJob]:
    return (
        db.query(BackupJob)
        .filter(BackupJob.account_id == account_id)
        .order_by(BackupJob.requested_at.desc())
        .limit(limit)
        .all()
    )


def cleanup_old_backup_jobs(db: Session, account_id: str, keep: int = 150) -> int:
    """Drop all but the newest ``keep`` runs for an account.

    At one backup a day, keep=150 is roughly five months of history.
    """
    rows = (
        db.query(BackupJob.id)
        .filter(BackupJob.account_id == account_id)
        .order_by(BackupJob.requested_at.desc())
        .offset(keep)
        .all()
    )
    if not rows:
        return 0
    ids = [r[0] for r in rows]
    deleted = db.query(BackupJob).filter(BackupJob.id.in_(ids)).delete(synchronize_session=False)
    db.commit()
    return deleted
