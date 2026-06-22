import contextlib
import fnmatch
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.db import SessionLocal
from mailfallback.models import Account, JobStatus, SyncJob, SyncState
from mailfallback.security import decrypt_credentials
from mailfallback.services import index_service, sync_budget, sync_failures
from mailfallback.services.mbsync_config import (
    channel_name,
    excluded_folder_names,
    generate_mbsyncrc,
)
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


def get_live_progress_for_account(account_id: str) -> dict | None:
    """Last-known sampler progress for an ACCOUNT — no DB lookup needed:
    the eviction policy keeps at most one entry per account (the running
    job's, or the last finished one until the next job starts)."""
    for prog in list(_live_progress.values()):
        if prog.get("account_id") == account_id:
            return prog
    return None


def _budget_headroom_today(account: "Account") -> bool:
    """True when today's ledger leaves budget headroom. A stale traffic_date
    means the ledger belongs to a previous UTC day — fresh budget."""
    budget = sync_budget.daily_budget_bytes(account)
    if budget is None:
        return True
    if account.traffic_date != datetime.now(UTC).date():
        return True
    return (account.bytes_synced_today or 0) < budget


def _redrive_or_clear(account: "Account") -> None:
    """After an interrupted job: an INCOMPLETE initial sync with budget
    headroom gets an already-expired pause so _run_pause_expiry_tick
    re-enqueues it in-process (one re-drive path, no boot-time race). A
    COMPLETED initial sync just clears — its own cron resumes it."""
    if account.initial_sync_completed_at is None and _budget_headroom_today(account):
        account.sync_paused_until = datetime.now(UTC) - timedelta(seconds=1)
        account.pause_reason = "interrupted"
    else:
        account.sync_paused_until = None
        account.pause_reason = None


def recover_zombie_sync_jobs(db: Session) -> int:
    """Boot-time crash recovery sweep (sync-budget spec §9).

    Closes sync_jobs rows a dead process left behind:
    - ``running`` rows whose job_id is not in ``_running_procs`` (a fresh
      boot has an empty dict, so that is all of them);
    - ``pending`` rows: queueing is a DB row + an IN-MEMORY executor
      (submit_sync_job hands the id to the ThreadPoolExecutor; nothing
      re-drives pending rows from the DB on boot), so a crash orphans
      them — and an orphaned pending row blocks create_sync_job for that
      account FOREVER via the existing-job guard.

    Each zombie: status=failed, failure_kind="interrupted",
    completed_at=now, a "[recovered]" marker appended to the log. Account
    side: syncing → idle; then _redrive_or_clear() — for an incomplete
    initial sync with budget headroom, sets an already-expired pause so the
    minute-tick re-enqueues it in-process; otherwise clears both pause
    columns. The sweep itself NEVER enqueues (idempotent and
    side-effect-light by design: enqueueing from here would race the
    scheduler that starts right after in the same lifespan).

    Boot-time contract: run before the executor accepts new jobs (a
    mid-flight pending row would be wrongly closed otherwise).
    """
    zombies = (
        db.query(SyncJob).filter(SyncJob.status.in_([JobStatus.running, JobStatus.pending])).all()
    )
    now = datetime.now(UTC)
    recovered = 0
    for job in zombies:
        if job.status == JobStatus.running and job.id in _running_procs:
            continue  # genuinely alive — idempotent re-call safety
        recovered += 1
        job.status = JobStatus.failed
        job.failure_kind = "interrupted"
        job.completed_at = now
        marker = "[recovered] container restarted mid-sync — closed as interrupted"
        job.log = f"{job.log}\n{marker}" if job.log else marker
        account = db.query(Account).filter(Account.id == job.account_id).first()
        if not account:
            continue
        if account.sync_state == SyncState.syncing:
            account.sync_state = SyncState.idle
        _redrive_or_clear(account)
    if recovered:
        db.commit()
        logger.info("Recovered %d zombie sync job(s) after restart", recovered)
    return recovered


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
        # The watermark follows run_bytes DOWN too (review F10, deliberate):
        # a file vanishing mid-walk (e.g. a webmail \Seen rename) dips
        # run_bytes; following it down re-books those bytes when the file
        # reappears — bounded over-booking, the spec's safe direction. A
        # max() watermark would instead UNDER-book after true deletions.
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
            "updated_ts": time.time(),
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


def _refresh_oauth_token(
    creds_json: str, db: Session, account: "Account"
) -> tuple[str | None, bool]:
    """Refresh OAuth2 token and update stored credentials.

    Returns ``(access_token, terminal)`` where ``terminal=True`` means the
    failure is confirmed non-recoverable (e.g. ``invalid_grant`` — token
    revoked/expired, user must re-auth).  ``access_token`` is ``None`` on any
    failure.
    """
    import asyncio
    import json

    try:
        token_data = json.loads(creds_json)
    except json.JSONDecodeError:
        return None, True  # malformed creds — terminal, nothing to retry

    refresh_token = token_data.get("refresh_token", "")
    if not refresh_token:
        return None, True  # no refresh token — terminal, user must re-auth

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
        return access_token, False
    except Exception as exc:
        logger.exception("Failed to refresh OAuth2 token for %s", account.name)
        # invalid_grant = the refresh token is revoked/expired → terminal,
        # needs user re-auth.  Network/5xx blips are NOT terminal (keep retrying).
        terminal = "invalid_grant" in str(getattr(exc, "error", "") or exc).lower()
        return None, terminal


_STATUS_MESSAGES_RE = re.compile(r"MESSAGES\s+(\d+)")
_LIST_NAME_RE = re.compile(r'"([^"]+)"\s*$')


def _folder_excluded(name: str, patterns: list[str]) -> bool:
    """mbsync pattern semantics: literal names plus * and ? wildcards ONLY —
    fnmatch's [..] character classes must NOT fire ("[Gmail]/All Mail" is a
    literal bracket, not a class of one char among G,m,a,i,l)."""
    for pattern in patterns:
        if name == pattern:
            return True
        if ("*" in pattern or "?" in pattern) and fnmatch.fnmatchcase(
            name, pattern.replace("[", "[[]")
        ):
            return True
    return False


def _count_upstream_messages(
    account: "Account", password: str | None, access_token: str | None
) -> int | None:
    """Upstream STATUS pass — the initial-sync progress denominator.

    LIST every folder on the provider, drop the channel's pattern
    !-exclusions (fnmatch — exact names and globs) and \\Noselect
    placeholders, STATUS (MESSAGES) the rest, sum. Raises on connection
    trouble — the CALLER treats any failure as non-fatal (the ETA degrades
    gracefully without a total). One cheap pass per job, before mbsync.
    """
    from mailfallback.services.imap_check import connect_imap

    extra = json.loads(account.extra_config) if account.extra_config else {}
    excludes = excluded_folder_names(extra.get("patterns", "*"))
    username = account.imap_user or account.email_address or account.name
    conn = connect_imap(
        account.imap_host,
        account.imap_port,
        account.tls_type or "IMAPS",
        username,
        access_token or password,
        auth_method="xoauth2" if access_token else "login",
    )
    try:
        typ, data = conn.list()
        if typ != "OK" or not data:
            return None
        total = 0
        for line in data:
            if not line:
                continue
            if isinstance(line, tuple):
                # Literal-encoded LIST entry (review F4a): imaplib yields
                # (prefix_with_flags, name_bytes) for folder names that
                # need a literal (e.g. non-ASCII) — str(tuple) would
                # garble the name and silently drop the folder.
                decoded = line[0].decode() if isinstance(line[0], bytes) else str(line[0])
                raw_name = line[1]
                name = raw_name.decode() if isinstance(raw_name, bytes) else str(raw_name)
            else:
                decoded = line.decode() if isinstance(line, bytes) else str(line)
                match = _LIST_NAME_RE.search(decoded)
                name = match.group(1) if match else decoded.rsplit(" ", 1)[-1]
            if "\\Noselect" in decoded:
                continue
            if _folder_excluded(name, excludes):
                continue
            st, st_data = conn.status(f'"{name}"', "(MESSAGES)")
            if st != "OK" or not st_data:
                continue
            raw = st_data[0].decode() if isinstance(st_data[0], bytes) else str(st_data[0])
            counted = _STATUS_MESSAGES_RE.search(raw)
            if counted:
                total += int(counted.group(1))
        return total
    finally:
        with contextlib.suppress(Exception):
            conn.logout()


def _attempt_today(db: Session, account_id: str, kind: str) -> int:
    """Backoff attempt number: today's UTC jobs with the same failure_kind
    + 1 (the current job's kind is not yet written when this counts). No
    new column — the job history IS the counter."""
    day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    prior = (
        db.query(SyncJob)
        .filter(
            SyncJob.account_id == account_id,
            SyncJob.failure_kind == kind,
            SyncJob.started_at >= day_start,
        )
        .count()
    )
    return prior + 1


def _pause_account(account: "Account", job: "SyncJob", kind: str, resume_at: datetime) -> None:
    """A self-recovering pause: NOT an error. State idle, last_error clear,
    the scheduler gate set (Task 7 reads sync_paused_until/pause_reason)."""
    reason = {"budget_paused": "budget", "throttled": "throttle"}.get(kind, "transient")
    job.status = JobStatus.failed
    job.failure_kind = kind
    account.sync_state = SyncState.idle
    account.last_error = None
    account.pause_reason = reason
    account.sync_paused_until = resume_at


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
    status_access_token = None
    if account.credentials:
        creds = decrypt_credentials(account.credentials, settings.secret_key)
        if account.auth_type.value == "oauth2":
            access_token, terminal = _refresh_oauth_token(creds, db, account)
            if not access_token:
                job.status = JobStatus.failed
                job.log = TOKEN_REFRESH_FAILED
                job.completed_at = datetime.now(UTC)
                account.sync_state = SyncState.needs_reauth if terminal else SyncState.error
                account.last_error = TOKEN_REFRESH_FAILED
                account.sync_paused_until = None
                account.pause_reason = None
                db.commit()
                return
            status_access_token = access_token
            token_file = os.path.join(tempfile.gettempdir(), f"mfb_token_{account.id}")
            fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(access_token)
            token_command = f"cat {token_file}"
        else:
            password = creds

    # === Initial-sync regime (sync-budget spec §7) ===
    initial_sync = account.initial_sync_completed_at is None
    runtime_extra_config = account.extra_config
    if initial_sync:
        # Upstream STATUS pass: the progress denominator. Once per job,
        # before mbsync starts (cheap, but it still counts as traffic).
        # NON-FATAL by contract — without a total the ETA degrades.
        try:
            total = _count_upstream_messages(account, password, status_access_token)
            if total is not None:
                account.initial_sync_total_messages = total
                db.commit()
        except Exception:
            logger.warning(
                "Upstream STATUS pass failed for %s — proceeding without total",
                account.name,
                exc_info=True,
            )
        # Gentle first sync: PipelineDepth 1, injected at RUNTIME only —
        # never persisted into account.extra_config. setdefault: an
        # explicit per-account override wins over the regime default.
        extra_dict = json.loads(account.extra_config) if account.extra_config else {}
        extra_dict.setdefault("pipeline_depth", "1")
        runtime_extra_config = json.dumps(extra_dict)

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
        extra_config=runtime_extra_config,
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

        # Priority pass while the initial sync is incomplete: INBOX first
        # (the mail the user is waiting for), then the full channel. One job
        # row, sequential subprocesses, the sampler spans both. After the
        # initial sync: single full invocation, exactly as before.
        if initial_sync:
            channel = channel_name(account.name)
            invocations = []
            # A command-line box-spec supersedes the channel Patterns — if
            # the user's patterns EXCLUDE INBOX, the priority pass must not
            # resurrect (or, depending on the mbsync build, error on) it.
            patterns_extra = json.loads(account.extra_config) if account.extra_config else {}
            inbox_excluded = _folder_excluded(
                "INBOX", excluded_folder_names(patterns_extra.get("patterns", "*"))
            )
            if not inbox_excluded:
                invocations.append([settings.mbsync_binary, "-c", config_path, f"{channel}:INBOX"])
            invocations.append([settings.mbsync_binary, "-c", config_path, channel])
        else:
            invocations = [[settings.mbsync_binary, "-c", config_path, "-a"]]
        if settings.debug:
            for inv in invocations:
                inv.insert(1, "-Dm")

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
        result_code = 1
        try:
            if log_file_path:
                log_fd = os.open(log_file_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                log_file = os.fdopen(log_fd, "w")
            for idx, cmd in enumerate(invocations):
                if idx and job_id in _budget_stops:
                    # The budget tripped between invocations (the stop found
                    # an already-finished proc) — don't start the full pass.
                    break
                if idx:
                    marker = f"--- mbsync invocation {idx + 1}/{len(invocations)}: full pass ---"
                    _running_logs[job_id].append(marker)
                    if log_file:
                        log_file.write(marker + "\n")
                        log_file.flush()
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                # CURRENT subprocess — stop_sync_job must kill the right one.
                _running_procs[job_id] = proc
                # Re-arm the budget enforcer (review F1): if the sampler
                # tripped in the window between the marker check above and
                # this registration, its stop hit the already-reaped previous
                # proc (a no-op) and the once-guard would never fire again —
                # the new pass would run unbounded past the budget. The
                # marker is set BEFORE the sampler's stop call, so seeing it
                # here closes the race completely.
                if job_id in _budget_stops:
                    stop_sync_job(job_id)
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
                if result_code != 0:
                    break  # an INBOX-pass failure skips the full pass
            result_output = "\n".join(_running_logs.get(job_id, []))
        finally:
            # Stop the sampler BEFORE dropping the proc handle: its final
            # flush makes the job-end ledger/progress state accurate.
            sampler_stop.set()
            sampler_thread.join(timeout=15)
            if sampler_thread.is_alive():
                # Final walk still running (large maildir on a slow volume).
                # The orphan only re-publishes a stale progress entry and may
                # over-book bytes into the OLD run window — the conservative
                # direction — but it must be visible in the logs.
                logger.warning(
                    "Sync sampler for job %s did not finish within 15s — orphaned", job_id
                )
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

        # Budget stop FIRST — it SIGTERMs the proc, so the marker must beat
        # both the exit code and the signal interpretation. Consume it.
        budget_stopped = job_id in _budget_stops
        _budget_stops.discard(job_id)
        # Known edge (review F3): a budget crossing in the FINAL sampler
        # flush of a clean exit-0 run lands here as budget_paused — the
        # 100%-done sync re-labels as paused and completes as a no-op rerun
        # after the resume. Accepted: the alternative (trusting exit 0 over
        # the marker) would mislabel real mid-run stops.

        if result_code == 0 and not budget_stopped:
            job.status = JobStatus.completed
            account.sync_state = SyncState.idle
            account.last_sync_at = datetime.now(UTC)
            account.last_error = None
            if account.initial_sync_completed_at is None:
                # A clean FULL pass (the loop only reaches exit 0 after the
                # last invocation) ends the initial-sync regime.
                account.initial_sync_completed_at = datetime.now(UTC)
            account.sync_paused_until = None
            account.pause_reason = None
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
        elif budget_stopped:
            _pause_account(
                account, job, "budget_paused", sync_budget.next_budget_resume(datetime.now(UTC))
            )
            logger.info(
                "Sync paused for %s: daily budget reached, resumes %s",
                account.name,
                account.sync_paused_until,
            )
        elif job_signal:
            # User-initiated stop: today's behavior (a budget stop also
            # SIGTERMs — consumed above, never reaches here). Pause columns
            # CLEAR (review F2): an error state with a live pause would be
            # skipped by the scheduler AND hidden by the dashboard's
            # self-recovering-pause exclusion — contradictory.
            job.status = JobStatus.failed
            account.sync_state = SyncState.error
            account.last_error = job.log
            account.sync_paused_until = None
            account.pause_reason = None
            logger.warning("Sync stopped for %s (%s)", account.name, job_signal)
        else:
            # Classify the tail: throttles and network blips are
            # self-recovering pauses, not red errors (sync-budget spec §1).
            now = datetime.now(UTC)
            kind = sync_failures.classify_failure(result_output[-4096:], account.provider)
            if kind == "throttled":
                attempt = _attempt_today(db, account.id, "throttled")
                _pause_account(
                    account, job, "throttled", sync_budget.next_throttle_resume(now, attempt)
                )
                logger.info(
                    "Sync throttled for %s (attempt %d today), resumes %s",
                    account.name,
                    attempt,
                    account.sync_paused_until,
                )
            elif kind == "transient":
                attempt = _attempt_today(db, account.id, "transient")
                _pause_account(
                    account, job, "transient", sync_budget.next_transient_resume(now, attempt)
                )
                logger.info(
                    "Sync hit a transient failure for %s (attempt %d today), resumes %s",
                    account.name,
                    attempt,
                    account.sync_paused_until,
                )
            else:
                job.status = JobStatus.failed
                job.failure_kind = "error"
                account.sync_state = SyncState.error
                account.last_error = job.log
                # Review F2: a real error must not keep a stale pause — the
                # scheduler would skip it and the dashboard would hide it.
                account.sync_paused_until = None
                account.pause_reason = None
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
        # Review F11: exception exits skip the straight-line marker consume —
        # discard here too (idempotent) so a leaked marker can't relabel a
        # FUTURE job of the same id (impossible) or sit forever in the set.
        _budget_stops.discard(job_id)
        if config_path and not settings.debug:
            os.unlink(config_path)
        if token_file and os.path.exists(token_file):
            os.unlink(token_file)
        db.commit()
