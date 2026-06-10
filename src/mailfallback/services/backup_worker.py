"""Backup worker — executes restic backups in a thread pool."""

import contextlib
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from mailfallback.db import SessionLocal
from mailfallback.models import Account, BackupPolicy, BackupStatus
from mailfallback.services import index_service, restic_service
from mailfallback.services.restic_service import account_tags

logger = logging.getLogger(__name__)

_backup_executor: ThreadPoolExecutor | None = None
_backup_progress: dict[str, dict] = {}


def get_backup_executor() -> ThreadPoolExecutor:
    """Return the module-level backup executor, creating it on first use."""
    global _backup_executor
    if _backup_executor is None:
        _backup_executor = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="backup-worker",
        )
        logger.info("Backup executor started with 2 workers")
    return _backup_executor


def shutdown_backup_executor() -> None:
    """Shut down the backup executor, waiting for running jobs to finish."""
    global _backup_executor
    if _backup_executor is not None:
        logger.info("Shutting down backup executor...")
        _backup_executor.shutdown(wait=True)
        _backup_executor = None


def get_backup_progress(backup_id: str) -> dict | None:
    """Read current progress for a running backup."""
    return _backup_progress.get(backup_id)


def execute_backup(db: Session, account_backup_id: str) -> None:
    """Main backup function: init repo, run backup, apply retention, update DB."""
    backup = db.query(BackupPolicy).filter(BackupPolicy.id == account_backup_id).first()
    if not backup:
        logger.error("BackupPolicy %s not found", account_backup_id)
        return

    account = db.query(Account).filter(Account.id == backup.account_id).first()
    if not account:
        backup.last_status = BackupStatus.failed
        backup.last_error = "Account not found"
        db.commit()
        return

    now = datetime.now(UTC)
    backup.last_status = BackupStatus.running
    backup.last_error = None
    backup.last_run_at = now
    db.commit()

    _backup_progress[account_backup_id] = {"phase": "starting"}

    try:
        # Phase 1: init repo
        _backup_progress[account_backup_id] = {"phase": "init"}
        if not restic_service.init_repo(backup.destination, account.id):
            raise RuntimeError("Failed to initialize restic repository")

        # Phase 2: run backup
        _backup_progress[account_backup_id] = {"phase": "backup"}
        tags = account_tags(account)
        summary = restic_service.run_backup(
            backup.destination, account.id, account.maildir_path, tags=tags
        )
        _backup_progress[account_backup_id] = {"phase": "backup", "summary": summary}

        # Index hook: record_snapshot for the freshly-created snapshot.
        snapshot_id = summary.get("snapshot_id") if isinstance(summary, dict) else None
        if snapshot_id:
            try:
                index_service.record_snapshot(db, backup.account_id, snapshot_id)
            except Exception:
                logger.warning(
                    "record_snapshot failed for %s/%s",
                    backup.account_id,
                    snapshot_id,
                    exc_info=True,
                )

        # Phase 3: apply retention
        _backup_progress[account_backup_id] = {"phase": "retention"}
        retention_result = restic_service.apply_retention(
            backup.destination,
            account.id,
            backup.retention_preset.value,
            backup.keep_daily,
            backup.keep_weekly,
            backup.keep_monthly,
        )

        # Index hook: prune_snapshot for each snapshot restic forget removed.
        for removed_id in retention_result.get("removed_snapshot_ids", []) or []:
            try:
                index_service.prune_snapshot(db, removed_id)
            except Exception:
                logger.warning("prune_snapshot failed for %s", removed_id, exc_info=True)

        # Success — also cache snapshot count + most-recent snapshot timestamp so the
        # chain widget (Wave 4) can render without shelling restic on every page load.
        success_at = datetime.now(UTC)
        backup.last_status = BackupStatus.completed
        backup.last_backup_at = success_at
        backup.last_successful_run_at = success_at
        backup.last_error = None
        try:
            snapshots = restic_service.list_snapshots(backup.destination, account.id)
            backup.last_snapshot_count = len(snapshots)
            if snapshots:
                ts = snapshots[0].get("time", "").replace("Z", "+00:00")
                with contextlib.suppress(ValueError):
                    backup.last_snapshot_at = datetime.fromisoformat(ts)
        except Exception as snap_exc:
            logger.warning("Snapshot count refresh failed for account %s: %s", account.id, snap_exc)
        logger.info("Backup completed for account %s", account.name)

    except Exception as e:
        backup.last_status = BackupStatus.failed
        backup.last_error = str(e)
        logger.error("Backup failed for account %s: %s", account.id, e)

    finally:
        _backup_progress.pop(account_backup_id, None)
        db.commit()


def submit_backup(account_backup_id: str) -> None:
    """Submit a backup job to the bounded thread pool."""

    def _run():
        db = SessionLocal()
        try:
            execute_backup(db, account_backup_id)
        finally:
            db.close()

    get_backup_executor().submit(_run)
