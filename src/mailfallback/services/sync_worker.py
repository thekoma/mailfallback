import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.db import SessionLocal
from mailfallback.models import Account, JobStatus, SyncJob, SyncState
from mailfallback.security import decrypt_credentials
from mailfallback.services import index_service, sync_budget
from mailfallback.services.mbsync_config import generate_mbsyncrc
from mailfallback.services.sync_progress import parse_mbsync_lines

logger = logging.getLogger(__name__)

_sync_executor: ThreadPoolExecutor | None = None
_running_procs: dict[str, subprocess.Popen] = {}
_running_logs: dict[str, list[str]] = {}
_killed_signals: dict[str, str] = {}

# === Byte meter / sampler (sync-budget spec §2/§3/§5) ===
# Sampling cadence of the maildir walk. Module constant so tests shrink it.
SAMPLE_INTERVAL = 30.0
# job_id → last-known progress dict. Lifecycle: entries OUTLIVE their job
# (the UI wants last-known %/ETA briefly after completion); the next job
# START for the same account evicts the stale entry. Never grows unbounded:
# at most one live + one last-known entry per account.
_live_progress: dict[str, dict] = {}
# job_ids the budget enforcer stopped. The worker translates the marker into
# failure_kind=budget_paused and REMOVES it when consumed (Task 5).
_budget_stops: set[str] = set()
# EMA smoothing for the live msgs/s rate.
_RATE_EMA_ALPHA = 0.3


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


def get_live_progress(job_id: str) -> dict | None:
    """Last-known sampler progress for a job (may outlive the job briefly)."""
    return _live_progress.get(job_id)


def _sample_maildir(path: str, since_ts: float) -> tuple[int, int, int, int]:
    """One walk over the ACCOUNT maildir: (total_msgs, total_bytes,
    run_msgs, run_bytes).

    Cumulative totals count every message file under cur/ and new/ —
    restart-proof by construction (re-derived from disk, never accumulated).
    The run delta counts files with mtime >= since_ts: with
    ``CopyArrivalDate no`` (the generator's setting) mbsync stamps files
    with the WRITE time, so mtime identifies this run's downloads. If
    CopyArrivalDate is ever enabled, mtime becomes the message's arrival
    date and this must switch to st_ctime.

    Skipped: tmp/ staging files (parent dir is not cur/new), dotfiles and
    dovecot metadata inside cur/new, and nested .dovecot-home trees (only
    the account's own mail counts).
    """
    total_msgs = total_bytes = run_msgs = run_bytes = 0
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if d != ".dovecot-home"]
        if os.path.basename(dirpath) not in ("cur", "new"):
            continue
        for fname in filenames:
            if fname.startswith((".", "dovecot")):
                continue
            try:
                st = os.stat(os.path.join(dirpath, fname))
            except OSError:
                continue  # delivered/expunged mid-walk
            total_msgs += 1
            total_bytes += st.st_size
            if st.st_mtime >= since_ts:
                run_msgs += 1
                run_bytes += st.st_size
    return total_msgs, total_bytes, run_msgs, run_bytes


def _new_sampler_state(run_start_ts: float) -> dict:
    """Per-run sampler bookkeeping: the run-bytes watermark (ledger booking
    is delta-based within the run) + EMA rate inputs."""
    return {
        "run_start_ts": run_start_ts,
        "last_run_bytes": 0,
        "last_run_msgs": 0,
        "last_tick_monotonic": time.monotonic(),
        "rate": 0.0,
        "had_tick": False,
    }


def _sampler_tick(job_id: str, account_id: str, maildir_path: str, state: dict) -> None:
    """One sample + crash-safe flush.

    Opens its OWN session (never the worker's — different thread), re-reads
    the account row fresh each tick to honor external edits, books only the
    NEW run bytes into the daily ledger (max(0, run - watermark): monotonic
    within the run; across restarts the walk re-derives from disk inside the
    new run window only), COMMITS, then enforces the budget. Commit-before-
    stop: a container death right after the stop never forgets the spend.
    """
    totals = _sample_maildir(maildir_path, state["run_start_ts"])
    total_msgs, total_bytes, run_msgs, run_bytes = totals

    now_mono = time.monotonic()
    dt = max(1e-6, now_mono - state["last_tick_monotonic"])
    inst_rate = max(0, run_msgs - state["last_run_msgs"]) / dt
    rate = (
        inst_rate
        if not state["had_tick"]
        else _RATE_EMA_ALPHA * inst_rate + (1 - _RATE_EMA_ALPHA) * state["rate"]
    )
    state.update(last_run_msgs=run_msgs, last_tick_monotonic=now_mono, rate=rate, had_tick=True)

    db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            return
        today = datetime.now(UTC).date()
        if account.traffic_date != today:
            # UTC rollover: yesterday's spend stays booked to yesterday; the
            # within-run watermark is unaffected (it tracks run bytes, and
            # pre-midnight run bytes were already booked).
            account.traffic_date = today
            account.bytes_synced_today = 0
        account.bytes_synced_today = (account.bytes_synced_today or 0) + max(
            0, run_bytes - state["last_run_bytes"]
        )
        state["last_run_bytes"] = run_bytes

        budget = sync_budget.daily_budget_bytes(account)
        pct = sync_budget.compute_progress(total_msgs, account.initial_sync_total_messages)
        eta = sync_budget.estimate_eta(
            done_msgs=total_msgs,
            total_msgs=account.initial_sync_total_messages,
            done_bytes=total_bytes,
            bytes_today=account.bytes_synced_today,
            budget_bytes=budget,
            run_rate_msgs_per_s=rate,
        )
        _live_progress[job_id] = {
            "account_id": account_id,
            "done_msgs": total_msgs,
            "done_bytes": total_bytes,
            "run_msgs": run_msgs,
            "run_bytes": run_bytes,
            "bytes_today": account.bytes_synced_today,
            "budget_bytes": budget,
            "pct": pct,
            "eta": eta,
            "rate_msgs_per_s": rate,
        }
        db.commit()  # crash-safe ledger: every tick persists (spec §2)

        over_budget = budget is not None and account.bytes_synced_today >= budget
        if over_budget and job_id not in _budget_stops:
            # Marker first: the worker must see WHY the proc died even if
            # the stop is instant. Guarded — stop at most once per job.
            _budget_stops.add(job_id)
            logger.info(
                "Daily sync budget reached for account %s (%d >= %d) — stopping job %s",
                account_id,
                account.bytes_synced_today,
                budget,
                job_id,
            )
            stop_sync_job(job_id)
    finally:
        db.close()


def _run_sampler(
    job_id: str, account_id: str, maildir_path: str, state: dict, stop_event: threading.Event
) -> None:
    """Sampler thread body: tick every SAMPLE_INTERVAL until stopped, then
    one FINAL tick so the job-end ledger/progress state is accurate. A
    sampler crash must NEVER kill the sync — every tick swallows and logs.
    """
    while not stop_event.wait(SAMPLE_INTERVAL):
        try:
            _sampler_tick(job_id, account_id, maildir_path, state)
        except Exception:
            logger.warning("Sync sampler tick failed for job %s", job_id, exc_info=True)
    try:
        _sampler_tick(job_id, account_id, maildir_path, state)
    except Exception:
        logger.warning("Sync sampler final flush failed for job %s", job_id, exc_info=True)


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

        run_start_ts = time.time()
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        _running_procs[job_id] = proc
        _running_logs[job_id] = []

        # A new run for this account evicts the PREVIOUS job's last-known
        # progress (entries deliberately outlive their job for the UI — see
        # the _live_progress lifecycle note at the top).
        for stale_id, prog in list(_live_progress.items()):
            if prog.get("account_id") == account.id and stale_id != job_id:
                _live_progress.pop(stale_id, None)
        sampler_stop = threading.Event()
        sampler_thread = threading.Thread(
            target=_run_sampler,
            args=(
                job_id,
                account.id,
                account.maildir_path,
                _new_sampler_state(run_start_ts),
                sampler_stop,
            ),
            daemon=True,
            name=f"sync-sampler-{job_id[:8]}",
        )
        sampler_thread.start()

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
            # Stop the sampler BEFORE dropping the proc handle: its final
            # flush makes the job-end ledger/progress state accurate.
            sampler_stop.set()
            sampler_thread.join(timeout=15)
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
