import json
import logging
import os
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.db import SessionLocal
from mailfallback.models import Account, JobStatus, SyncJob, SyncState
from mailfallback.security import decrypt_credentials
from mailfallback.services import index_service
from mailfallback.services.mbsync_config import generate_mbsyncrc
from mailfallback.services.sync_progress import parse_mbsync_lines

logger = logging.getLogger(__name__)

_sync_executor: ThreadPoolExecutor | None = None
_running_procs: dict[str, subprocess.Popen] = {}
_running_logs: dict[str, list[str]] = {}
_killed_signals: dict[str, str] = {}


def get_live_log(job_id: str) -> str | None:
    lines = _running_logs.get(job_id)
    if lines is not None:
        return "\n".join(lines)
    return None


# Sentinel error message: set on the account when the OAuth refresh token is
# rejected, matched by the UI to surface the re-authenticate flow.
TOKEN_REFRESH_FAILED = "Failed to refresh OAuth2 token"


def get_sync_executor() -> ThreadPoolExecutor:
    """Return the module-level sync executor, creating it on first use."""
    global _sync_executor
    if _sync_executor is None:
        _sync_executor = ThreadPoolExecutor(
            max_workers=settings.sync_max_workers,
            thread_name_prefix="sync-worker",
        )
        logger.info("Sync executor started with %d workers", settings.sync_max_workers)
    return _sync_executor


def submit_sync_job(job_id: str) -> None:
    """Submit a sync job to the bounded thread pool."""

    def _run():
        db = SessionLocal()
        try:
            execute_sync_job(db, job_id)
        finally:
            db.close()

    get_sync_executor().submit(_run)


def shutdown_sync_executor() -> None:
    """Shut down the sync executor, waiting for running jobs to finish."""
    global _sync_executor
    if _sync_executor is not None:
        logger.info("Shutting down sync executor...")
        _sync_executor.shutdown(wait=True)
        _sync_executor = None


def stop_sync_job(job_id: str) -> bool:
    proc = _running_procs.get(job_id)
    if not proc:
        return False
    _killed_signals[job_id] = "SIGTERM"
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _killed_signals[job_id] = "SIGKILL"
        proc.kill()
    return True


def _refresh_oauth_token(creds_json: str, db: Session, account: "Account") -> str | None:
    """Refresh OAuth2 token and update stored credentials."""
    import asyncio
    import json

    try:
        token_data = json.loads(creds_json)
    except json.JSONDecodeError:
        return None

    refresh_token = token_data.get("refresh_token", "")
    if not refresh_token:
        return None

    provider = token_data.get("provider", "google")
    try:
        from mailfallback.services.oauth2 import refresh_google_token, refresh_microsoft_token

        refresh_fn = {
            "microsoft": refresh_microsoft_token,
        }.get(provider, refresh_google_token)
        access_token = asyncio.run(refresh_fn(refresh_token))

        token_data["access_token"] = access_token
        from mailfallback.security import encrypt_credentials

        account.credentials = encrypt_credentials(json.dumps(token_data), settings.secret_key)
        db.commit()
        return access_token
    except Exception:
        logger.exception("Failed to refresh OAuth2 token for %s", account.name)
        return None


def _safe_name(name: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", name.lower().strip())


def execute_sync_job(db: Session, job_id: str) -> None:
    job = db.query(SyncJob).filter(SyncJob.id == job_id).first()
    if not job:
        return

    account = db.query(Account).filter(Account.id == job.account_id).first()
    if not account:
        job.status = JobStatus.failed
        job.log = "Account not found"
        job.completed_at = datetime.now(UTC)
        db.commit()
        return

    if account.suspended:
        job.status = JobStatus.failed
        job.log = "Sync blocked: account is suspended"
        job.completed_at = datetime.now(UTC)
        db.commit()
        return

    if not account.is_authenticated:
        job.status = JobStatus.failed
        job.log = "Sync blocked: account not authenticated"
        job.completed_at = datetime.now(UTC)
        db.commit()
        return

    if account.migrating:
        job.status = JobStatus.failed
        job.log = "Sync blocked: account migration in progress"
        job.completed_at = datetime.now(UTC)
        db.commit()
        return

    for owner in account.owners:
        if owner.migrating:
            job.status = JobStatus.failed
            job.log = "Sync blocked: user migration in progress"
            job.completed_at = datetime.now(UTC)
            db.commit()
            return

    job.status = JobStatus.running
    job.started_at = datetime.now(UTC)
    account.sync_state = SyncState.syncing
    db.commit()
    logger.info("Sync started for %s (job %s)", account.name, job_id)

    password = None
    token_command = None
    token_file = None
    if account.credentials:
        creds = decrypt_credentials(account.credentials, settings.secret_key)
        if account.auth_type.value == "oauth2":
            access_token = _refresh_oauth_token(creds, db, account)
            if not access_token:
                job.status = JobStatus.failed
                job.log = TOKEN_REFRESH_FAILED
                job.completed_at = datetime.now(UTC)
                account.sync_state = SyncState.error
                account.last_error = job.log
                db.commit()
                return
            token_file = os.path.join(tempfile.gettempdir(), f"mfb_token_{account.id}")
            fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(access_token)
            token_command = f"cat {token_file}"
        else:
            password = creds

    config_content = generate_mbsyncrc(
        account_name=account.name,
        imap_host=account.imap_host,
        imap_port=account.imap_port,
        username=account.imap_user or account.email_address or account.name,
        auth_type=account.auth_type.value,
        maildir_path=account.maildir_path,
        tls_type=account.tls_type or "IMAPS",
        password=password,
        token_command=token_command,
        extra_config=account.extra_config,
    )

    config_path = None
    try:
        if settings.debug:
            debug_dir = os.path.join(tempfile.gettempdir(), "mbsync")
            os.makedirs(debug_dir, exist_ok=True, mode=0o700)
            config_path = os.path.join(debug_dir, f"{account.id}.rc")
            fd = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(config_content)
            logger.info("mbsync config saved to %s", config_path)
        else:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".mbsyncrc", delete=False) as f:
                f.write(config_content)
                config_path = f.name

        os.makedirs(account.maildir_path, exist_ok=True)

        cmd = [settings.mbsync_binary, "-c", config_path, "-a"]
        if settings.debug:
            cmd.insert(1, "-Dm")

        log_file_path = None
        try:
            log_dir = os.path.join(settings.sync_log_dir, str(account.id))
            os.makedirs(log_dir, exist_ok=True)
            log_file_path = os.path.join(log_dir, f"{job_id}.log")
        except OSError:
            logger.warning(
                "Cannot create sync log dir %s, skipping disk log",
                settings.sync_log_dir,
            )

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        _running_procs[job_id] = proc
        _running_logs[job_id] = []
        log_file = None
        try:
            if log_file_path:
                log_fd = os.open(log_file_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                log_file = os.fdopen(log_fd, "w")
            for line in proc.stdout:
                line = line.rstrip()
                if settings.debug:
                    logger.debug("[mbsync/%s] %s", account.name, line)
                _running_logs[job_id].append(line)
                if log_file:
                    log_file.write(line + "\n")
                    log_file.flush()
            proc.wait(timeout=3600)
            result_code = proc.returncode
            result_output = "\n".join(_running_logs.get(job_id, []))
        finally:
            _running_procs.pop(job_id, None)
            _running_logs.pop(job_id, None)
            if log_file:
                log_file.close()

        job.exit_code = result_code
        job.log = result_output
        job.log_path = log_file_path
        job.completed_at = datetime.now(UTC)

        job_signal = _killed_signals.pop(job_id, None)
        if job_signal:
            job.signal = job_signal

        snap = parse_mbsync_lines(result_output.splitlines())
        job.mbsync_version = snap.mbsync_version
        try:
            from dataclasses import asdict

            job.parsed_summary = json.dumps(asdict(snap), default=str)
        except Exception:
            logger.warning("Failed to serialize parsed summary for job %s", job_id)

        if result_code == 0:
            job.status = JobStatus.completed
            account.sync_state = SyncState.idle
            account.last_sync_at = datetime.now(UTC)
            account.last_error = None
            logger.info("Sync completed for %s", account.name)
            try:
                from mailfallback.services.stats_service import collect_account_stats

                collect_account_stats(db, account)
            except Exception:
                logger.warning("Failed to collect stats for %s", account.name, exc_info=True)
            try:
                from mailfallback.services.sync_service import cleanup_old_jobs

                cleanup_old_jobs(db, account.id)
            except Exception:
                logger.warning("Failed to cleanup old jobs for %s", account.name, exc_info=True)
            try:
                index_service.upsert_message_set(db, account.id)
            except Exception:
                logger.warning("Mail index upsert failed for %s", account.name, exc_info=True)
        else:
            job.status = JobStatus.failed
            account.sync_state = SyncState.error
            account.last_error = job.log
            logger.warning("Sync failed for %s (exit %d)", account.name, result_code)

    except subprocess.TimeoutExpired:
        job.status = JobStatus.failed
        job.log = "Sync timed out after 3600 seconds"
        job.completed_at = datetime.now(UTC)
        account.sync_state = SyncState.error
        account.last_error = job.log

    except Exception as e:
        job.status = JobStatus.failed
        job.log = str(e)
        job.completed_at = datetime.now(UTC)
        account.sync_state = SyncState.error
        account.last_error = str(e)

    finally:
        if config_path and not settings.debug:
            os.unlink(config_path)
        if token_file and os.path.exists(token_file):
            os.unlink(token_file)
        db.commit()
