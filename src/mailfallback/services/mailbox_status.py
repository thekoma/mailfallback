"""The single answer to "is this mailbox OK?".

Every surface that shows a mailbox's health — dashboard stat, chain stages,
Needs Attention, accounts table chip, detail hero and Health box, admin status
strip, agent API / MCP ``status`` — reads it from here. None of them derives
health from ``sync_state`` or the pause columns on its own: when they did, they
contradicted each other (a budget pause red on one screen and blue on another).

Two layers:

- ``resolve_mailbox_status`` / ``resolve_job_outcome`` are pure: no DB, no I/O.
- ``resolve_many`` is what surfaces call. It loads the
  latest *finished* job per account and the sampler's live progress once, so
  every surface resolves from identical inputs.

Tone rules: only ``error`` is red. ``attention`` (amber) means the owner has to
act but nothing is broken yet. ``active`` (blue) is working or self-recovering.
``muted`` is deliberately off, stopped, or not started.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from functools import lru_cache

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from mailfallback.constants import TOKEN_REFRESH_FAILED
from mailfallback.models import (
    Account,
    AuthType,
    JobStatus,
    SyncJob,
    SyncState,
    User,
    account_owners,
)


class Tone(StrEnum):
    ok = "ok"  # current
    active = "active"  # working or self-recovering
    attention = "attention"  # owner must act; nothing is broken yet
    error = "error"  # real failure — the ONLY red tone
    muted = "muted"  # deliberately off, stopped, or not started


class MailboxState(StrEnum):
    migrating = "migrating"
    suspended = "suspended"
    sign_in_needed = "sign_in_needed"
    first_sync = "first_sync"  # sync running, initial sync never completed
    syncing = "syncing"  # sync running, initial sync completed
    paused = "paused"  # pause gate set (budget/throttle/transient/interrupted)
    stopped = "stopped"  # last run was stopped by a user (signal), not a failure
    error = "error"
    initial_sync = "initial_sync"  # initial sync started, idle between passes
    initial_stalled = "initial_stalled"  # initial sync started (or never) and nothing for too long
    waiting = "waiting"  # never started
    stale = "stale"  # last success older than the stale threshold
    current = "current"


class NextAction(StrEnum):
    reconnect = "reconnect"  # OAuth sign-in: /auth/{provider}/start
    # app password rejected: /accounts/{id}#admin-edit
    update_password = "update_password"  # pragma: allowlist secret
    retry = "retry"  # real error: POST /api/sync/{id}
    sync_now = "sync_now"  # stale / stalled: POST /api/sync/{id}


# The MINIMUM staleness threshold. A mailbox scheduled less often than every
# 3.5 days gets twice its schedule interval instead (see stale_threshold). The
# scheduler's stale-notify still uses a fixed 7 days — aligning it is a
# follow-up.
STALE_AFTER = timedelta(days=7)

# The one tone → stats-dot class map; passed to templates as `dot_class`.
TONE_DOT = {
    Tone.ok.value: "stats-dot-ok",
    Tone.active.value: "stats-dot-syncing",
    Tone.attention.value: "stats-dot-warning",
    Tone.error.value: "stats-dot-error",
    Tone.muted.value: "",
}

# Honest copy per pause reason — chip tooltips + panel headlines.
PAUSE_TOOLTIPS = {
    "budget": "Daily sync budget reached",
    "throttle": "Provider throttled",
    "transient": "Connection lost — retrying",
    "interrupted": "Interrupted by a restart. It resumes on its own shortly.",
}
_GENERIC_PAUSE_DETAIL = "Paused for now. It resumes on its own."

# The exact pre-flight guard messages of sync_worker._execute_sync. A job
# failed by one of them never ran; any other failed job did.
_BLOCKED_LOGS = frozenset(
    {
        "Sync blocked: account is suspended",
        "Sync blocked: account not authenticated",
        "Sync blocked: account migration in progress",
        "Sync blocked: user migration in progress",
    }
)

_FINISHED = (JobStatus.completed, JobStatus.failed, JobStatus.cancelled)

_INITIAL_DETAIL = "First full sync incomplete"


@dataclass(frozen=True)
class MailboxStatus:
    state: MailboxState
    tone: Tone
    label: str  # UI chip text
    detail: str | None  # UI-only sentence; may contain last_error. NEVER sent to the agent API.
    action: NextAction | None
    needs_attention: bool  # True only for sign_in_needed, error, stale, initial_stalled
    # Independent of precedence: False iff not is_authenticated, or needs_reauth,
    # or (error AND oauth2 AND last_error == TOKEN_REFRESH_FAILED).
    signed_in: bool
    badge: str  # CSS badge class
    icon: str  # Lucide icon name
    spin: bool  # icon gets the `spin` class
    last_success_at: datetime | None  # account.last_sync_at, tz-aware
    # sync_paused_until, only when pause_reason is set AND that time is later
    # than `now`; otherwise None.
    resumes_at: datetime | None


@dataclass(frozen=True)
class JobOutcome:
    tone: Tone
    label: str
    badge: str
    icon: str
    spin: bool


def _aware(dt: datetime | None) -> datetime | None:
    # Local copy of scheduler._aware_utc: importing services.scheduler would
    # pull APScheduler's scheduler machinery into a pure module.
    return None if dt is None else (dt if dt.tzinfo else dt.replace(tzinfo=UTC))


@lru_cache(maxsize=256)
def _cron_trigger(schedule: str):
    from apscheduler.triggers.cron import CronTrigger

    return CronTrigger.from_crontab(schedule, timezone=UTC)


def stale_threshold(schedule: str | None, now: datetime) -> timedelta:
    """``max(STALE_AFTER, 2 * schedule interval)``; 7 days for no/invalid cron.

    The interval is the gap between the next two fire times after ``now``, so a
    monthly schedule is not "out of date" three weeks into its month.
    """
    if not schedule or not schedule.strip():
        return STALE_AFTER
    try:
        trigger = _cron_trigger(schedule.strip())
        first = trigger.get_next_fire_time(None, now)
        second = trigger.get_next_fire_time(first, first) if first else None
    except Exception:
        return STALE_AFTER
    if first is None or second is None:
        return STALE_AFTER
    return max(STALE_AFTER, 2 * (second - first))


def _is(value, member) -> bool:
    # Enum columns come back as the enum; hand-built rows may carry plain strings.
    return value is not None and str(getattr(value, "value", value)) == member.value


def _sign_in_detail(account: Account, token_refresh: bool) -> str:
    # token_refresh already implies sync_state == error (so not needs_reauth).
    if token_refresh and account.is_authenticated:
        return (
            "Couldn't refresh the sign-in. It retries on the next sync; "
            "reconnect if this keeps happening."
        )
    if account.provider == "google":
        return "Reconnect your Google account to resume syncing."
    if account.provider == "microsoft":
        return "Reconnect your Microsoft account to resume syncing."
    return "Reconnect this mailbox to resume syncing."


def _is_credential_error(last_error: str | None) -> bool:
    if not last_error:
        return False
    from mailfallback.services.sync_progress import _classify_error

    return _classify_error(last_error)[0] == "auth"


def resolve_mailbox_status(
    account: Account,
    *,
    last_job: SyncJob | None = None,
    live_progress: bool = False,
    owner_migrating: bool = False,
    now: datetime | None = None,
) -> MailboxStatus:
    """Resolve one mailbox. Pure: reads only the arguments.

    ``last_job`` is the latest FINISHED job (``latest_finished_jobs_by_account``).
    ``owner_migrating`` is True when any owner of the account has
    ``User.migrating`` set (the scheduler and every trigger refuse then).
    ``live_progress`` is True when the in-memory sampler has progress for the
    account (``has_live_progress``). Surfaces call ``resolve_many`` instead.
    """
    now = _aware(now) or datetime.now(UTC)

    sync_state = account.sync_state
    is_error = _is(sync_state, SyncState.error)
    is_oauth = _is(account.auth_type, AuthType.oauth2)
    initial_done = account.initial_sync_completed_at is not None
    last_success = _aware(account.last_sync_at)
    pause_reason = account.pause_reason or None
    paused_until = _aware(account.sync_paused_until)
    # Only a pause that has yet to lift has a resume time worth reporting.
    resumes_at = paused_until if pause_reason and paused_until and paused_until > now else None

    job_started = last_job is not None and last_job.started_at is not None
    started = live_progress or account.initial_sync_total_messages is not None or job_started
    if job_started:
        last_activity = _aware(last_job.completed_at or last_job.started_at)
    else:
        last_activity = _aware(account.created_at)

    manual_only = not (account.sync_schedule or "").strip()
    threshold = stale_threshold(account.sync_schedule, now)

    def old(t: datetime | None) -> bool:
        return t is not None and t < now - threshold

    long_unsynced = not manual_only and (old(last_success) if initial_done else old(last_activity))

    token_refresh = is_error and is_oauth and account.last_error == TOKEN_REFRESH_FAILED
    signed_in = (
        account.is_authenticated
        and not _is(sync_state, SyncState.needs_reauth)
        and not token_refresh
    )

    # A user stop writes last_error = job.log (sync_worker). Requiring that
    # match keeps a later error that never started a job (the host
    # re-validation guard sets sync_state=error + last_error, no started_at,
    # so its job is not last_job) from reading as the older stop.
    stopped_job = (
        last_job is not None
        and _is(last_job.status, JobStatus.failed)
        and bool(last_job.signal)
        and last_job.failure_kind is None
        and account.last_error == last_job.log
    )
    # The UI trigger overrides a pause with a warning; the agent trigger
    # refuses one (routers/agent.py sync_now: `sync_paused_until is not None
    # or pause_reason is not None`, expired or not). `action` is agent-facing,
    # so it mirrors that predicate exactly and withholds sync_now / retry.
    pause_blocks_trigger = account.sync_paused_until is not None or account.pause_reason is not None

    def make(
        state: MailboxState,
        tone: Tone,
        label: str,
        badge: str,
        icon: str,
        *,
        spin: bool = False,
        action: NextAction | None = None,
        detail: str | None = None,
        needs_attention: bool = False,
    ) -> MailboxStatus:
        return MailboxStatus(
            state=state,
            tone=tone,
            label=label,
            detail=detail,
            action=action,
            needs_attention=needs_attention,
            signed_in=signed_in,
            badge=badge,
            icon=icon,
            spin=spin,
            last_success_at=last_success,
            resumes_at=resumes_at,
        )

    # 1-3: identity / permission problems.
    if account.migrating or owner_migrating:
        return make(MailboxState.migrating, Tone.active, "Migrating", "badge-syncing", "loader")
    if account.suspended:
        return make(
            MailboxState.suspended, Tone.muted, "Suspended", "badge-disabled", "pause-circle"
        )
    if not signed_in:
        return make(
            MailboxState.sign_in_needed,
            Tone.attention,
            "Sign-in needed",
            "badge-warning",
            "key-round",
            action=NextAction.reconnect,
            detail=_sign_in_detail(account, token_refresh),
            needs_attention=True,
        )

    # 4-5: a sync in progress.
    if _is(sync_state, SyncState.syncing):
        if not initial_done:
            return make(
                MailboxState.first_sync,
                Tone.active,
                "Initial sync",
                "badge-info",
                "loader",
                spin=True,
                detail=_INITIAL_DETAIL,
            )
        return make(MailboxState.syncing, Tone.active, "syncing", "badge-syncing", "loader")

    # 6-7: how the last run ended.
    if is_error and stopped_job and not long_unsynced:
        detail = (
            "The last sync was stopped. Start a sync when you're ready."
            if manual_only
            else "The last sync was stopped. The next scheduled sync runs normally."
        )
        return make(
            MailboxState.stopped,
            Tone.muted,
            "Stopped",
            "badge-disabled",
            "circle-slash",
            detail=detail,
        )
    if is_error and pause_reason is None and not stopped_job:
        action = (
            NextAction.update_password
            if _is(account.auth_type, AuthType.app_password)
            and _is_credential_error(account.last_error)
            else (None if pause_blocks_trigger else NextAction.retry)
        )
        return make(
            MailboxState.error,
            Tone.error,
            "error",
            "badge-error",
            "alert-circle",
            action=action,
            detail=(account.last_error or "")[:200] or "The last sync failed.",
            needs_attention=True,
        )

    # 8-9: has anything been copied lately? Beats a pause on purpose.
    if long_unsynced:
        action = None if pause_blocks_trigger else NextAction.sync_now
        if not initial_done:
            if started:
                label = "Initial sync stalled"
                detail = f"The initial sync has not progressed for over {threshold.days} days."
            else:
                label = "Never synced"
                created = _aware(account.created_at)
                days = (now - created).days if created else threshold.days
                detail = f"This mailbox has never synced (added {days} days ago)."
            return make(
                MailboxState.initial_stalled,
                Tone.attention,
                label,
                "badge-warning",
                "alert-triangle",
                action=action,
                detail=detail,
                needs_attention=True,
            )
        return make(
            MailboxState.stale,
            Tone.attention,
            "Out of date",
            "badge-warning",
            "clock",
            action=action,
            detail=f"Last sync was {(now - last_success).days} days ago.",
            needs_attention=True,
        )

    # 10: self-recovering pause (also absorbs error + pause).
    if pause_reason is not None:
        return make(
            MailboxState.paused,
            Tone.active,
            "Paused",
            "badge-info",
            "pause-circle",
            detail=PAUSE_TOOLTIPS.get(pause_reason, _GENERIC_PAUSE_DETAIL),
        )

    # 11-13: the quiet states.
    if not initial_done:
        if started:
            return make(
                MailboxState.initial_sync,
                Tone.active,
                "Initial sync",
                "badge-info",
                "download",
                detail=_INITIAL_DETAIL,
            )
        return make(
            MailboxState.waiting,
            Tone.muted,
            "Waiting for first sync",
            "badge-disabled",
            "clock",
        )
    return make(MailboxState.current, Tone.ok, "idle", "badge-idle", "check-circle")


_PAUSE_KINDS = {
    "budget_paused": "paused (daily limit)",
    "throttled": "paused (provider throttling)",
    "transient": "paused (temporary error)",
    "interrupted": "interrupted",
}


def resolve_job_outcome(job: SyncJob) -> JobOutcome:
    """How one sync run ended, for Recent Activity and Sync History. Pure."""
    status = job.status
    if _is(status, JobStatus.completed):
        return JobOutcome(Tone.ok, "synced", "badge-idle", "check-circle", False)
    if _is(status, JobStatus.running):
        return JobOutcome(Tone.active, "syncing", "badge-syncing", "loader", True)
    if not _is(status, JobStatus.failed):
        # pending, and cancelled (no sync code path sets it; kept for totality)
        return JobOutcome(Tone.muted, "queued", "badge-disabled", "clock", False)

    kind = job.failure_kind
    if kind is None:
        if job.signal:
            return JobOutcome(Tone.muted, "stopped", "badge-disabled", "circle-slash", False)
        if job.log in _BLOCKED_LOGS:
            return JobOutcome(Tone.muted, "skipped", "badge-disabled", "circle-slash", False)
        if job.log == TOKEN_REFRESH_FAILED:
            return JobOutcome(Tone.attention, "sign-in failed", "badge-warning", "key-round", False)
    elif kind in _PAUSE_KINDS:
        return JobOutcome(Tone.active, _PAUSE_KINDS[kind], "badge-info", "pause-circle", False)
    return JobOutcome(Tone.error, "failed", "badge-error", "x-circle", False)


def latest_finished_jobs_by_account(db: Session, account_ids: list[str]) -> dict[str, SyncJob]:
    """Per account id, the most recent FINISHED job (completed/failed/cancelled)
    that actually STARTED.

    Finished-only on purpose: a queued retry or a running job must not hide how
    the previous run ended. Started-only for the same reason: a pre-flight
    "Sync blocked" job never ran. One query, portable between SQLite and
    PostgreSQL.
    """
    ids = list(dict.fromkeys(account_ids))
    if not ids:
        return {}
    latest = (
        db.query(
            SyncJob.account_id.label("aid"),
            func.max(SyncJob.completed_at).label("m"),
        )
        .filter(
            SyncJob.account_id.in_(ids),
            SyncJob.status.in_(_FINISHED),
            SyncJob.completed_at.isnot(None),
            # A pre-flight "Sync blocked" job never ran (no started_at) and
            # must not hide how the last real run ended.
            SyncJob.started_at.isnot(None),
        )
        .group_by(SyncJob.account_id)
        .subquery()
    )
    rows = (
        db.query(SyncJob)
        .join(
            latest,
            and_(SyncJob.account_id == latest.c.aid, SyncJob.completed_at == latest.c.m),
        )
        .filter(SyncJob.status.in_(_FINISHED), SyncJob.started_at.isnot(None))
        .all()
    )
    out: dict[str, SyncJob] = {}
    for job in rows:
        out.setdefault(job.account_id, job)  # ties: any one of them
    return out


def accounts_with_migrating_owner(db: Session, account_ids: list[str]) -> set[str]:
    """Ids (among ``account_ids``) with at least one owner whose
    ``User.migrating`` is set — one query. The scheduler, /api/sync and the
    agent trigger all refuse to sync such an account."""
    ids = list(dict.fromkeys(account_ids))
    if not ids:
        return set()
    rows = (
        db.query(account_owners.c.account_id)
        .join(User, User.id == account_owners.c.user_id)
        .filter(account_owners.c.account_id.in_(ids), User.migrating.is_(True))
        .distinct()
        .all()
    )
    return {row.account_id for row in rows}


def has_live_progress(account: Account) -> bool:
    """True when the in-memory sampler has progress for this account."""
    # Lazy: routers.ui imports this module at load time.
    from mailfallback.routers.ui import account_live_status

    return account_live_status(account)["pct"] is not None


def resolve_many(
    db: Session,
    accounts: list[Account],
    *,
    now: datetime | None = None,
    jobs: dict[str, SyncJob] | None = None,
) -> dict[str, MailboxStatus]:
    """The entry point every surface uses: identical inputs everywhere.

    ``jobs`` lets a caller that already needs the latest finished jobs (the
    detail hero parses the failed job's summary) pass the result of
    ``latest_finished_jobs_by_account`` instead of querying twice.
    """
    ids = [a.id for a in accounts]
    if jobs is None:
        jobs = latest_finished_jobs_by_account(db, ids)
    migrating_owner = accounts_with_migrating_owner(db, ids)
    now = now or datetime.now(UTC)
    return {
        a.id: resolve_mailbox_status(
            a,
            last_job=jobs.get(a.id),
            live_progress=has_live_progress(a),
            owner_migrating=a.id in migrating_owner,
            now=now,
        )
        for a in accounts
    }
