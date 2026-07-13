# src/mailfallback/routers/ui.py
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, selectinload

from mailfallback.config import settings
from mailfallback.dependencies import get_db
from mailfallback.models import Account, SyncJob, User, UserRole
from mailfallback.services.account_service import get_accounts_for_user
from mailfallback.services.user_service import authenticate_user
from mailfallback.version import __version__

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ui"])

# __file__-relative so templates resolve regardless of the process CWD
# (mirrors the static mount in app.py); a relative path breaks packaged installs.
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def _get_session_user(request: Request, db: Session) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.enabled:
        return None
    return user


def _filesizeformat(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PB"


def _cron_human(value):
    if not value:
        return ""
    parts = str(value).split()
    if len(parts) < 5:
        return value
    m, h, dom, mon, dow = parts[:5]
    if m.startswith("*/") and h == "*" and dom == "*" and mon == "*" and dow == "*":
        return f"Every {m[2:]} min"
    if m == "0" and h == "*":
        return "Every hour"
    if m == "0" and dom == "*" and mon == "*" and dow == "*":
        return f"Daily at {h}:00"
    if m == "0" and dom == "*" and mon == "*" and dow == "1-5":
        return f"Weekdays at {h}:00"
    return value


def _time_ago(value):
    if not value:
        return "Never"
    now = datetime.now(UTC)
    ts = value.replace(tzinfo=UTC) if value.tzinfo is None else value
    delta = now - ts
    secs = delta.total_seconds()
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{int(secs / 60)}m ago"
    if secs < 86400:
        return f"{int(secs / 3600)}h ago"
    return f"{delta.days}d ago"


def _time_until(value):
    """Relative time until a future timestamp ("in 7m", "in 3h", "in 2d").
    Relative by design so it is timezone-independent — a wall-clock format of
    a UTC value renders 2h off for a UTC+2 user. Naive timestamps are read as
    UTC (the column stores UTC). Past/now collapses to "shortly"."""
    if not value:
        return None
    now = datetime.now(UTC)
    ts = value.replace(tzinfo=UTC) if value.tzinfo is None else value
    secs = (ts - now).total_seconds()
    if secs <= 0:
        return "shortly"
    if secs < 60:
        return "in <1m"
    if secs < 3600:
        return f"in {int(-(-secs // 60))}m"
    if secs < 86400:
        return f"in {int(-(-secs // 3600))}h"
    return f"in {int(-(-secs // 86400))}d"


def _time_ago_class(value):
    if not value:
        return "sync-error"
    now = datetime.now(UTC)
    ts = value.replace(tzinfo=UTC) if value.tzinfo is None else value
    delta = now - ts
    secs = delta.total_seconds()
    if secs < 1800:
        return "sync-idle"
    if secs < 7200:
        return "sync-syncing"
    return "sync-error"


def _number_format(value):
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


templates.env.filters["filesizeformat"] = _filesizeformat
templates.env.filters["cron_human"] = _cron_human
templates.env.filters["time_ago"] = _time_ago
templates.env.filters["time_ago_class"] = _time_ago_class
templates.env.filters["number"] = _number_format
templates.env.globals["webmail_url"] = settings.webmail_url
templates.env.globals["webmail_enabled"] = settings.webmail_enabled
templates.env.globals["app_version"] = __version__

# Honest copy per pause reason — chip tooltips + panel headlines.
PAUSE_TOOLTIPS = {
    "budget": "Daily sync budget reached",
    "throttle": "Provider throttled",
    "transient": "Connection lost — retrying",
}


def account_live_status(account) -> dict:
    """Per-account live sync status for the polling partials (spec §8).

    Cheap by contract: the sampler's in-memory last-known entry (kept
    briefly after job end, evicted on the account's next job) + columns
    already loaded on the account row — no extra queries.
    """
    from mailfallback.services import sync_budget
    from mailfallback.services.sync_worker import get_live_progress_for_account

    prog = get_live_progress_for_account(account.id) or {}
    eta = prog.get("eta") or {}
    paused_until = account.sync_paused_until
    return {
        "pct": prog.get("pct"),
        "done_msgs": prog.get("done_msgs"),
        "done_bytes": prog.get("done_bytes"),  # recap "Downloaded" (sampler total)
        "total_msgs": account.initial_sync_total_messages,
        # recap Folders denominator; numerator is snap.folder_index (log
        # parser, same basis — selectable boxes mbsync opens).
        "total_folders": account.initial_sync_total_folders,
        "bytes_today": prog.get("bytes_today", account.bytes_synced_today),
        "budget_bytes": prog.get("budget_bytes", sync_budget.daily_budget_bytes(account)),
        "eta_label": eta.get("label"),
        "rate_msgs_per_s": prog.get("rate_msgs_per_s"),
        "paused_until": paused_until,
        "resume_rel": _time_until(paused_until),
        "pause_reason": account.pause_reason,
        "pause_tooltip": PAUSE_TOOLTIPS.get(account.pause_reason),
        "initial_sync": account.initial_sync_completed_at is None,
    }


def _get_theme(request: Request) -> str:
    if hasattr(request, "session"):
        return request.session.get("theme", "light")
    return "light"


def _get_flash(request, flash_type):
    key = f"flash_{flash_type}"
    msg = request.session.pop(key, None) if hasattr(request, "session") else None
    return msg


templates.env.globals["get_theme"] = _get_theme
templates.env.globals["get_flash"] = _get_flash


_LOGIN_ERROR_MESSAGES = {
    "sso_unreachable": "Could not reach the SSO provider. Please try again.",
    "sso_failed": "SSO sign-in failed. Please try again.",
}


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    error = _LOGIN_ERROR_MESSAGES.get(request.query_params.get("error", ""))
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"oidc_enabled": settings.oidc_enabled, "error": error},
    )


@router.post("/login")
async def login_submit(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    username = form.get("username", "")
    password = form.get("password", "")
    user = authenticate_user(db, username, password) if username and password else None
    if not user:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"oidc_enabled": settings.oidc_enabled, "error": "Invalid credentials"},
        )
    request.session["user_id"] = user.id
    if user.preferences:
        request.session["theme"] = user.preferences.get("theme", "light")
    from mailfallback.services.audit_service import log_action

    log_action(
        db,
        user=user,
        action="user.login",
        resource_type="user",
        resource_id=user.id,
        resource_name=user.username,
        ip_address=request.client.host if request.client else None,
    )
    return RedirectResponse("/", status_code=303)


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login")
    accounts = get_accounts_for_user(db, user)

    total_messages = sum(a.total_messages for a in accounts)
    total_bytes = sum(a.maildir_size_bytes for a in accounts)
    # Self-recovering pauses are NOT errors (sync-budget spec §8): the
    # account-level pause_reason check is the adopted exclusion logic.
    error_count = sum(
        1 for a in accounts if a.sync_state.value == "error" and a.pause_reason is None
    )

    stats = {
        "accounts": len(accounts),
        "messages": total_messages,
        "storage": _filesizeformat(total_bytes),
        "errors": error_count,
    }

    if user.role.value == "admin":
        from mailfallback.services.store_service import get_default_store, list_stores
        from mailfallback.services.user_service import list_users

        stats["users"] = len(list_users(db))
        stats["stores"] = len(list_stores(db))

        # Add storage capacity from default store
        default_store = get_default_store(db)
        if default_store:
            import shutil

            try:
                usage = shutil.disk_usage(default_store.path)
                stats["storage_total"] = _filesizeformat(usage.total)
            except OSError:
                stats["storage_total"] = None
        else:
            stats["storage_total"] = None

    stale_cutoff = datetime.now(UTC) - timedelta(days=7)
    attention = []
    for a in accounts:
        if a.sync_state.value == "needs_reauth":
            # Revoked/expired OAuth token (e.g. provider password change) —
            # only the user can fix it, so surface it prominently with a
            # one-click reconnect, not buried in the account page.
            attention.append(
                {
                    "id": a.id,
                    "name": a.name,
                    "type": "reauth",
                    "reason": "Sign-in expired — reconnect needed",
                    "provider": a.provider,
                }
            )
        elif a.sync_state.value == "error" and a.pause_reason is None:
            reason = a.last_error[:80] if a.last_error else "Sync failed"
            attention.append({"id": a.id, "name": a.name, "type": "error", "reason": reason})
        elif a.initial_sync_completed_at is None and a.is_authenticated and not a.suspended:
            # The first full sync in flight is an INFO state, never an error.
            ls = account_live_status(a)
            bits = ["initial sync"]
            if ls["pct"] is not None:
                bits[0] += f" {int(ls['pct'])}%"
            if ls["eta_label"]:
                bits.append(f"ETA {ls['eta_label']}")
            if ls["resume_rel"]:
                bits.append(f"resumes {ls['resume_rel']}")
            attention.append(
                {"id": a.id, "name": a.name, "type": "info", "reason": " · ".join(bits)}
            )
        elif a.last_sync_at and a.last_sync_at.replace(tzinfo=UTC) < stale_cutoff:
            attention.append(
                {"id": a.id, "name": a.name, "type": "stale", "reason": "No sync in 7+ days"}
            )

    account_ids = [a.id for a in accounts]
    recent_jobs = []
    if account_ids:
        jobs = (
            db.query(SyncJob)
            .filter(SyncJob.account_id.in_(account_ids))
            .order_by(SyncJob.requested_at.desc())
            .limit(5)
            .all()
        )
        now = datetime.now(UTC)
        account_map = {a.id: a.name for a in accounts}
        for j in jobs:
            ts = j.completed_at or j.requested_at
            if ts:
                delta = now - ts.replace(tzinfo=UTC)
                if delta.total_seconds() < 60:
                    time_ago = "just now"
                elif delta.total_seconds() < 3600:
                    time_ago = f"{int(delta.total_seconds() / 60)}m ago"
                elif delta.total_seconds() < 86400:
                    time_ago = f"{int(delta.total_seconds() / 3600)}h ago"
                else:
                    time_ago = f"{delta.days}d ago"
            else:
                time_ago = "—"
            recent_jobs.append(
                {
                    "account_id": j.account_id,
                    "account_name": account_map.get(j.account_id, "?"),
                    "status": j.status.value,
                    "time_ago": time_ago,
                }
            )

    from sqlalchemy import func

    from mailfallback.models import BackupPolicy, BackupStatus, Repository, SyncState

    # Wave 4: chain summary feeds the dashboard hero card. Four stages:
    # Source (mailboxes connected) → Mirror (local sync health) →
    # Repository (configured + reachable) → Snapshot (cached counts).
    mirrors_healthy = sum(1 for a in accounts if a.sync_state == SyncState.idle)
    mirrors_error = sum(1 for a in accounts if a.sync_state == SyncState.error)
    snapshots_total = (
        db.query(func.coalesce(func.sum(BackupPolicy.last_snapshot_count), 0)).scalar() or 0
    )
    chain_summary = {
        "mailboxes": len(accounts),
        "mirrors_healthy": mirrors_healthy,
        "mirrors_error": mirrors_error,
        "mirrors_total": len(accounts),
        "repositories": db.query(Repository).count(),
        "policies": db.query(BackupPolicy).count(),
        "policies_with_recent_success": db.query(BackupPolicy)
        .filter(BackupPolicy.last_successful_run_at.isnot(None))
        .count(),
        "policies_failed": db.query(BackupPolicy)
        .filter(BackupPolicy.last_status == BackupStatus.failed)
        .count(),
        "policies_never_succeeded": db.query(BackupPolicy)
        .filter(BackupPolicy.last_successful_run_at.is_(None))
        .count(),
        "snapshots_total": int(snapshots_total),
    }

    from mailfallback.services.setup_state import get_setup_state

    setup_state = get_setup_state(db, user)

    # First-time explainer on the chain hero — shown once, dismissed via
    # POST /profile/dismiss-chain-explainer which sets this preference.
    show_chain_explainer = not (user.preferences or {}).get("chain_hero_seen", False)

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "user": user,
            "stats": stats,
            "chain_summary": chain_summary,
            "attention": attention,
            "recent_jobs": recent_jobs,
            "setup_state": setup_state,
            "show_chain_explainer": show_chain_explainer,
        },
    )


@router.get("/partials/system-status", response_class=HTMLResponse)
def system_status_partial(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return HTMLResponse("")

    from mailfallback.models import BackupPolicy, BackupStatus, JobStatus, RestoreJob, SyncJob
    from mailfallback.services.background_tasks import get_latest_task
    from mailfallback.services.dovecot_manager import get_cached_health

    dovecot = get_cached_health()
    fts = get_latest_task(db, "fts_reindex")
    resync = get_latest_task(db, "force_resync")

    syncing_count = db.query(SyncJob).filter(SyncJob.status == JobStatus.running).count()
    error_accounts = db.query(Account).filter(Account.sync_state == "error").all()

    active_restores = (
        db.query(RestoreJob)
        .filter(RestoreJob.status.in_([JobStatus.pending, JobStatus.running]))
        .all()
    )

    active_backups = (
        db.query(BackupPolicy).filter(BackupPolicy.last_status == BackupStatus.running).count()
    )

    has_activity = (
        dovecot.get("ok") is False
        or fts.get("status") == "running"
        or resync.get("status") == "running"
        or syncing_count > 0
        or error_accounts
        or active_restores
        or active_backups > 0
    )
    if not has_activity:
        return HTMLResponse("")

    return templates.TemplateResponse(
        request=request,
        name="partials/system_status.html",
        context={
            "dovecot": dovecot,
            "fts": fts,
            "resync": resync,
            "syncing_count": syncing_count,
            "error_accounts": error_accounts,
            "active_restores": active_restores,
            "active_backups": active_backups,
        },
    )


@router.get("/accounts", response_class=HTMLResponse)
def accounts_page(request: Request, show_all: str = "", db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login")
    is_admin = user.role == UserRole.admin
    show_all_users = is_admin and show_all == "1"
    if show_all_users:
        accounts = (
            db.query(Account)
            .options(
                selectinload(Account.backup_policies),
                selectinload(Account.recoveries),
            )
            .all()
        )
    else:
        accounts = get_accounts_for_user(db, user)
    return templates.TemplateResponse(
        request=request,
        name="accounts.html",
        context={
            "user": user,
            "accounts": accounts,
            "show_all_users": show_all_users,
            "live_status": {a.id: account_live_status(a) for a in accounts},
        },
    )


@router.get("/partials/accounts-table", response_class=HTMLResponse)
def accounts_table_partial(request: Request, show_all: str = "", db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user:
        response = HTMLResponse("")
        response.headers["HX-Redirect"] = "/login"
        return response
    is_admin = user.role == UserRole.admin
    show_all_users = is_admin and show_all == "1"
    if show_all_users:
        accounts = (
            db.query(Account)
            .options(
                selectinload(Account.backup_policies),
                selectinload(Account.recoveries),
            )
            .all()
        )
    else:
        accounts = get_accounts_for_user(db, user)
    any_syncing = any(a.sync_state.value == "syncing" for a in accounts)
    response = templates.TemplateResponse(
        request=request,
        name="partials/accounts_table.html",
        context={
            "user": user,
            "accounts": accounts,
            "live_status": {a.id: account_live_status(a) for a in accounts},
        },
    )
    if not any_syncing:
        response.headers["HX-Trigger"] = "sync-idle"
    return response
