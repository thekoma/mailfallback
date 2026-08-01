"""Backup worker — executes restic backups in a thread pool."""

import contextlib
import logging
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.db import SessionLocal
from mailfallback.models import Account, BackupJob, BackupPolicy, BackupStatus, JobStatus
from mailfallback.services import index_service, restic_service
from mailfallback.services.backup_service import cleanup_old_backup_jobs
from mailfallback.services.restic_service import account_tags

logger = logging.getLogger(__name__)

_backup_executor: ThreadPoolExecutor | None = None
_backup_progress: dict[str, dict] = {}
# Live restic processes by job id. A wedged backup can only be killed through
# a handle kept here; the boot sweep also uses membership to tell a genuinely
# running job from a zombie row.
_running_backup_procs: dict[str, subprocess.Popen] = {}


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


_RECOVERED_MARKER = "[recovered] container restarted mid-backup — closed as interrupted"


def _close_job(job: BackupJob, db: Session, *, failure_kind: str, marker: str) -> None:
    """Close a job row and heal its policy. Shared by both recovery sweeps."""
    job.status = JobStatus.failed
    job.failure_kind = failure_kind
    job.completed_at = datetime.now(UTC)
    job.log = f"{job.log}\n{marker}" if job.log else marker

    policy = db.query(BackupPolicy).filter(BackupPolicy.id == job.policy_id).first()
    if policy and policy.last_status == BackupStatus.running:
        # The policy is what the dashboard counts and what the account page
        # renders. Leaving it on "running" is the whole bug this exists to
        # prevent. Guarded on running so a newer successful run is never
        # clobbered by sweeping an older row.
        policy.last_status = BackupStatus.failed
        policy.last_error = marker


def recover_zombie_backup_jobs(db: Session) -> int:
    """Boot-time crash recovery for off-site backups.

    A SIGKILL skips execute_backup's ``finally``, so a crashed run leaves its
    BackupJob at running/pending and its policy at "running" forever. On a
    fresh boot ``_running_backup_procs`` is empty, so every such row is a
    zombie; the membership check only matters for an idempotent re-call.

    Boot-time contract, as for sync: run before the executor accepts new work,
    or a mid-flight row would be wrongly closed.
    """
    zombies = (
        db.query(BackupJob)
        .filter(BackupJob.status.in_([JobStatus.running, JobStatus.pending]))
        .all()
    )
    recovered = 0
    for job in zombies:
        if job.id in _running_backup_procs:
            continue  # genuinely alive
        recovered += 1
        _close_job(job, db, failure_kind="interrupted", marker=_RECOVERED_MARKER)
    if recovered:
        db.commit()
        logger.info("Recovered %d zombie backup job(s) after restart", recovered)
    return recovered


def stop_backup_job(job_id: str) -> bool:
    """SIGTERM the tracked restic process, escalating to SIGKILL. Mirrors
    stop_sync_job.

    Safe by construction: an interrupted restic backup creates no snapshot, and
    partial packs are reclaimed by the next `forget --prune`.
    """
    proc = _running_backup_procs.get(job_id)
    if proc is None:
        return False
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    return True


def recover_stalled_backup_jobs(db: Session) -> int:
    """Periodic watchdog: close running backups whose restic heartbeat died.

    NOTE the deliberate difference from recover_stalled_sync_jobs, which skips
    a job while its process is alive::

        if fresh_tick or proc_alive:   # sync
            continue

    For backups the target failure IS a live process: restic wedged on an S3
    call keeps ``poll()`` at None forever while emitting nothing. Liveness must
    therefore NOT excuse a job here — a missing heartbeat past the threshold is
    the whole signal, and we kill the process ourselves. Reusing the sync
    condition would mean the reaper never fires in the one case it exists for.
    """
    now = datetime.now(UTC)
    grace = timedelta(seconds=settings.backup_stall_grace_s)
    threshold = settings.backup_stall_threshold_s
    now_wall = time.time()
    reaped = 0

    for job in db.query(BackupJob).filter(BackupJob.status == JobStatus.running).all():
        started = job.started_at
        if started is None:
            continue
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        if now - started < grace:
            continue

        prog = _backup_progress.get(job.id)
        fresh_tick = prog is not None and (now_wall - prog.get("updated_ts", 0)) < threshold
        if fresh_tick:
            continue

        reaped += 1
        _close_job(
            job,
            db,
            failure_kind="stalled",
            marker="[reaped] no restic progress — closed as stalled",
        )
        with contextlib.suppress(Exception):
            stop_backup_job(job.id)
        _backup_progress.pop(job.id, None)
        _running_backup_procs.pop(job.id, None)

    if reaped:
        db.commit()
        logger.warning("Reaped %d stalled backup job(s)", reaped)
    return reaped


def execute_backup(db: Session, account_backup_id: str, source: str = "schedule") -> None:
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
    job = BackupJob(
        policy_id=backup.id,
        account_id=account.id,
        status=JobStatus.running,
        source=source,
        requested_at=now,
        started_at=now,
    )
    db.add(job)

    backup.last_status = BackupStatus.running
    backup.last_error = None
    backup.last_run_at = now
    db.commit()
    db.refresh(job)
    job_id = job.id

    def _tick(event: dict) -> None:
        """Heartbeat. Every restic message refreshes updated_ts, which is what
        recover_stalled_backup_jobs reads to tell 'slow' from 'wedged'."""
        prog = _backup_progress.setdefault(job_id, {})
        prog["updated_ts"] = time.time()
        if "percent_done" in event:
            prog["percent_done"] = event["percent_done"]
        if "bytes_done" in event:
            prog["bytes_done"] = event["bytes_done"]

    def _register(proc: subprocess.Popen) -> None:
        _running_backup_procs[job_id] = proc

    _backup_progress[job_id] = {"phase": "starting", "updated_ts": time.time()}

    try:
        # Phase 1: init repo
        _backup_progress[job_id] = {"phase": "init", "updated_ts": time.time()}
        if not restic_service.init_repo(backup.destination, account.id):
            raise RuntimeError("Failed to initialize restic repository")

        # Phase 2: run backup
        _backup_progress[job_id] = {"phase": "backup", "updated_ts": time.time()}
        tags = account_tags(account)
        summary = restic_service.run_backup(
            backup.destination,
            account.id,
            account.maildir_path,
            tags=tags,
            on_event=_tick,
            register=_register,
        )
        if not isinstance(summary, dict):
            summary = {}
        _tick({})

        # Index hook: record_snapshot for the freshly-created snapshot.
        snapshot_id = summary.get("snapshot_id")
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
        _backup_progress[job_id] = {"phase": "retention", "updated_ts": time.time()}
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

        job.status = JobStatus.completed
        job.completed_at = success_at
        job.snapshot_id = snapshot_id
        job.bytes_processed = int(summary.get("total_bytes_processed") or 0)
        job.bytes_added = int(summary.get("data_added") or 0)

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
        # The watchdog may have reaped this very job a moment ago — reaping
        # kills restic, which is what raised us here. Its verdict is the more
        # informative one ("stalled", not "restic died"), so don't overwrite a
        # classification that is already recorded; just append the detail.
        with contextlib.suppress(Exception):
            db.refresh(job)
        already_closed = job.status == JobStatus.failed and job.failure_kind

        backup.last_status = BackupStatus.failed
        job.status = JobStatus.failed
        job.completed_at = job.completed_at or datetime.now(UTC)
        if already_closed:
            job.log = f"{job.log}\n{e}" if job.log else str(e)
        else:
            backup.last_error = str(e)
            job.failure_kind = "error"
            job.log = str(e)
        logger.error("Backup failed for account %s: %s", account.id, e)

    finally:
        _backup_progress.pop(job_id, None)
        _running_backup_procs.pop(job_id, None)
        db.commit()
        # Retention is best-effort: losing old history must never turn a
        # successful backup into a failed one.
        with contextlib.suppress(Exception):
            cleanup_old_backup_jobs(db, account.id)


def submit_backup(account_backup_id: str, source: str = "schedule") -> None:
    """Submit a backup job to the bounded thread pool."""

    def _run():
        db = SessionLocal()
        try:
            execute_backup(db, account_backup_id, source=source)
        finally:
            db.close()

    get_backup_executor().submit(_run)
