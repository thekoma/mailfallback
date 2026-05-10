# src/mailfallback/services/scheduler.py
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

from mailfallback.db import SessionLocal
from mailfallback.models import Account
from mailfallback.services.sync_service import create_sync_job
from mailfallback.services.sync_worker import submit_sync_job

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()


def _run_scheduled_sync(account_id: str) -> None:
    db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            return
        if account.suspended:
            logger.info("Skipping sync for %s — account is suspended", account.name)
            return
        if not account.is_authenticated:
            logger.info("Skipping sync for %s — account not authenticated", account.name)
            return
        if account.migrating:
            logger.info("Skipping sync for %s — account is migrating", account.name)
            return
        for owner in account.owners:
            if owner.migrating:
                logger.info(
                    "Skipping sync for %s — user %s is migrating",
                    account.name,
                    owner.username,
                )
                return
        job = create_sync_job(db, account_id, source="scheduler")
        if job:
            submit_sync_job(job.id)
    finally:
        db.close()


def sync_scheduler_jobs(db: Session) -> None:
    existing_job_ids = {j.id for j in scheduler.get_jobs()}

    accounts = db.query(Account).filter(Account.suspended.is_(False)).all()
    active_job_ids = set()

    for account in accounts:
        job_id = f"sync_{account.id}"
        active_job_ids.add(job_id)

        if not account.is_authenticated:
            continue

        if not account.sync_schedule:
            continue

        parts = account.sync_schedule.split()
        if len(parts) != 5:
            logger.warning("Invalid cron for account %s: %s", account.name, account.sync_schedule)
            continue

        trigger = CronTrigger(
            minute=parts[0],
            hour=parts[1],
            day=parts[2],
            month=parts[3],
            day_of_week=parts[4],
        )

        if job_id in existing_job_ids:
            scheduler.reschedule_job(job_id, trigger=trigger)
        else:
            scheduler.add_job(
                _run_scheduled_sync,
                trigger=trigger,
                id=job_id,
                args=[account.id],
                replace_existing=True,
            )

    for job_id in existing_job_ids - active_job_ids:
        if job_id.startswith("sync_"):
            scheduler.remove_job(job_id)


def _run_scheduled_backup(account_backup_id: str) -> None:
    from mailfallback.services.backup_worker import submit_backup

    submit_backup(account_backup_id)


def backup_scheduler_jobs(db: Session) -> None:
    from mailfallback.models import BackupPolicy

    existing_job_ids = {j.id for j in scheduler.get_jobs()}
    active_backups = db.query(BackupPolicy).filter(BackupPolicy.enabled.is_(True)).all()

    for ab in active_backups:
        job_id = f"backup-{ab.id}"
        try:
            trigger = CronTrigger.from_crontab(ab.schedule)
        except Exception:
            logger.warning("Invalid backup cron for %s: %s", ab.id, ab.schedule)
            continue
        if job_id in existing_job_ids:
            scheduler.reschedule_job(job_id, trigger=trigger)
        else:
            scheduler.add_job(
                _run_scheduled_backup,
                trigger,
                args=[ab.id],
                id=job_id,
                replace_existing=True,
            )

    active_ids = {f"backup-{ab.id}" for ab in active_backups}
    for job_id in existing_job_ids:
        if job_id.startswith("backup-") and job_id not in active_ids:
            scheduler.remove_job(job_id)


def start_scheduler(db: Session) -> None:
    sync_scheduler_jobs(db)
    backup_scheduler_jobs(db)
    if not scheduler.running:
        scheduler.start()


def refresh_scheduler() -> None:
    if not scheduler.running:
        return
    db = SessionLocal()
    try:
        sync_scheduler_jobs(db)
        backup_scheduler_jobs(db)
    finally:
        db.close()


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
