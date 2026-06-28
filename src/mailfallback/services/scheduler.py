# src/mailfallback/services/scheduler.py
import logging
from datetime import UTC, datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.db import SessionLocal
from mailfallback.models import Account, SyncState
from mailfallback.services.sync_service import create_sync_job
from mailfallback.services.sync_worker import submit_sync_job

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()


def _aware_utc(dt: datetime) -> datetime:
    """SQLite hands DateTime(timezone=True) back naive, PostgreSQL aware —
    normalize for comparisons (stored values are UTC by construction)."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


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
        if account.sync_state == SyncState.needs_reauth:
            logger.info("Skipping sync for %s — needs re-authorization", account.name)
            return
        # Self-recovering pause gate (sync-budget spec §6): ANY non-null
        # future pause (budget|throttle|transient) blocks the PERIODIC path
        # only — manual syncs override in the API layer. An EXPIRED pause
        # does not gate; clearing it is the expiry tick's job.
        if account.sync_paused_until and _aware_utc(account.sync_paused_until) > datetime.now(UTC):
            logger.debug(
                "Skipping sync for %s — paused (%s) until %s",
                account.name,
                account.pause_reason,
                account.sync_paused_until,
            )
            return
        job = create_sync_job(db, account_id, source="scheduler")
        if job:
            submit_sync_job(job.id)
    finally:
        db.close()


def _run_pause_expiry_tick() -> None:
    """Lift expired sync pauses (sync-budget spec §6), every minute.

    Initial sync incomplete → enqueue immediately ONCE (the budget/backoff
    window just opened; waiting for the next cron slot would waste it) and
    clear the pause columns on enqueue. Initial sync complete → just clear;
    the account's own cron resumes it naturally (a routine incremental sync
    has no urgency). Idempotent and race-benign: clearing removes the
    account from the next tick's query, and create_sync_job's existing-job
    guard dedupes against a manual sync that got there first.
    """
    db = SessionLocal()
    try:
        now = datetime.now(UTC)
        paused = db.query(Account).filter(Account.sync_paused_until.isnot(None)).all()
        for account in paused:
            if _aware_utc(account.sync_paused_until) > now:
                continue
            reason = account.pause_reason
            account.sync_paused_until = None
            account.pause_reason = None
            db.commit()
            eligible = (
                not account.suspended
                and account.is_authenticated
                and account.sync_state != SyncState.needs_reauth
                and not account.migrating
                and all(not o.migrating for o in account.owners)
            )
            if account.initial_sync_completed_at is None and eligible:
                logger.info(
                    "Sync pause (%s) expired for %s — resuming the initial sync now",
                    reason,
                    account.name,
                )
                job = create_sync_job(db, account.id, source="scheduler")
                if job:
                    submit_sync_job(job.id)
            else:
                logger.info(
                    "Sync pause (%s) expired for %s — cron resumes it",
                    reason,
                    account.name,
                )
    except Exception:
        logger.exception("Pause expiry tick failed")
    finally:
        db.close()


def _run_mount_cleanup() -> None:
    db = SessionLocal()
    try:
        from mailfallback.services import mount_service

        mount_service.cleanup_idle_mounts(db)
    except Exception:
        logger.exception("mount cleanup failed")
    finally:
        db.close()


def _run_staging_cleanup() -> None:
    from mailfallback.services import staging_service

    db = SessionLocal()
    try:
        n = staging_service.cleanup_expired(db)
        if n:
            logger.info("Staging cleanup: purged %d expired area(s)", n)
    except Exception:
        logger.exception("Staging cleanup failed")
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


def _run_scheduled_config_backup(repository_id: str) -> None:
    from mailfallback.models import Repository
    from mailfallback.services.config_backup_service import run_config_backup

    db = SessionLocal()
    try:
        repo = db.query(Repository).filter(Repository.id == repository_id).first()
        if repo and repo.config_backup_enabled:
            run_config_backup(db, repo)
    except Exception:
        logger.exception("Scheduled config backup failed for %s", repository_id)
    finally:
        db.close()


def config_backup_scheduler_jobs(db: Session) -> None:
    from mailfallback.models import Repository

    existing_job_ids = {j.id for j in scheduler.get_jobs()}
    enabled = db.query(Repository).filter(Repository.config_backup_enabled.is_(True)).all()
    trigger = CronTrigger(hour=3, minute=0)

    for repo in enabled:
        job_id = f"config-backup-{repo.id}"
        if job_id in existing_job_ids:
            scheduler.reschedule_job(job_id, trigger=trigger)
        else:
            scheduler.add_job(
                _run_scheduled_config_backup,
                trigger,
                args=[repo.id],
                id=job_id,
                replace_existing=True,
            )

    active_ids = {f"config-backup-{r.id}" for r in enabled}
    for job_id in existing_job_ids:
        if job_id.startswith("config-backup-") and job_id not in active_ids:
            scheduler.remove_job(job_id)


def start_scheduler(db: Session) -> None:
    sync_scheduler_jobs(db)
    backup_scheduler_jobs(db)
    config_backup_scheduler_jobs(db)
    if not any(j.id == "mount-cleanup" for j in scheduler.get_jobs()):
        scheduler.add_job(
            _run_mount_cleanup,
            CronTrigger(minute=0),  # every hour at :00
            id="mount-cleanup",
            replace_existing=True,
        )
    if not any(j.id == "staging-cleanup" for j in scheduler.get_jobs()):
        scheduler.add_job(
            _run_staging_cleanup,
            CronTrigger(minute="*/15"),
            id="staging-cleanup",
            replace_existing=True,
        )
    if not any(j.id == "pause-expiry" for j in scheduler.get_jobs()):
        scheduler.add_job(
            _run_pause_expiry_tick,
            # Every minute: pause resumes carry minute-level jitter, and an
            # initial sync should grab its budget window as soon as it opens.
            CronTrigger(minute="*"),
            id="pause-expiry",
            replace_existing=True,
        )
    if not any(j.id == "sync-watchdog" for j in scheduler.get_jobs()):
        from mailfallback.services.sync_worker import recover_stalled_sync_jobs

        def _run_watchdog() -> None:
            db = SessionLocal()
            try:
                recover_stalled_sync_jobs(db)
            except Exception:
                logger.exception("Sync watchdog tick failed")
            finally:
                db.close()

        scheduler.add_job(
            _run_watchdog,
            CronTrigger(second=f"*/{max(1, settings.sync_watchdog_interval_s)}")
            if settings.sync_watchdog_interval_s < 60
            else CronTrigger(minute="*"),
            id="sync-watchdog",
            replace_existing=True,
        )
    if not any(j.id == "stale-notify" for j in scheduler.get_jobs()):

        def _run_stale_notify() -> None:
            from datetime import UTC, datetime, timedelta

            from mailfallback.models import Account
            from mailfallback.services import notification_service

            db = SessionLocal()
            try:
                cutoff = datetime.now(UTC) - timedelta(days=7)
                stale = (
                    db.query(Account)
                    .filter(
                        Account.last_sync_at.isnot(None),
                        Account.last_sync_at < cutoff,
                        Account.enabled.is_(True),
                        Account.suspended.is_(False),
                        Account.sync_state != SyncState.needs_reauth,
                        Account.pause_reason.is_(None),
                    )
                    .all()
                )
                for a in stale:
                    notification_service.notify_account_problem(
                        db,
                        a,
                        "stale",
                        f"{a.name}: no sync in 7+ days",
                        "MailFallBack has not synced this account in over a week.",
                    )
                    db.commit()
            except Exception:
                logger.exception("stale-notify tick failed")
            finally:
                db.close()

        scheduler.add_job(
            _run_stale_notify, CronTrigger(minute=0), id="stale-notify", replace_existing=True
        )
    if not scheduler.running:
        scheduler.start()


def refresh_scheduler() -> None:
    if not scheduler.running:
        return
    db = SessionLocal()
    try:
        sync_scheduler_jobs(db)
        backup_scheduler_jobs(db)
        config_backup_scheduler_jobs(db)
    finally:
        db.close()


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
