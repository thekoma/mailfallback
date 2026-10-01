# src/mailfallback/routers/ui.py
import logging
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from sqlalchemy.orm import Session, selectinload

from mailfallback.config import settings
from mailfallback.constants import STAGING_MAILBOX
from mailfallback.dependencies import get_db
from mailfallback.models import (
    Account,
    BackupPolicy,
    BackupStatus,
    SyncJob,
    User,
    UserRole,
    account_owners,
)
from mailfallback.services import notification_service as _ns
from mailfallback.services.account_service import get_accounts_for_user, get_own_accounts
from mailfallback.services.mailbox_status import (
    PAUSE_TOOLTIPS,
    TONE_DOT,
    MailboxState,
    NextAction,
    Tone,
    resolve_job_outcome,
    resolve_many,
    sign_in_message,
    sign_in_never_completed,
)
from mailfallback.services.sync_progress import UNKNOWN_ERROR_MESSAGE, error_category
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


def _duration_human(started, completed):
    """Human duration between two timestamps.

    A missing ``completed`` means the run is still going, so measure against
    now — that is what makes the history table's Duration column useful while
    a backup is in flight.
    """
    if not started:
        return "—"
    start = started.replace(tzinfo=UTC) if started.tzinfo is None else started
    if completed is None:
        end = datetime.now(UTC)
    else:
        end = completed.replace(tzinfo=UTC) if completed.tzinfo is None else completed
    secs = int((end - start).total_seconds())
    if secs < 0:
        return "—"
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m {secs % 60}s"
    return f"{secs // 3600}h {(secs % 3600) // 60}m"


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


# Providers that refuse the normal account password over IMAP and need an app
# password. AuthType.app_password also covers the plain password of a generic
# IMAP server (provider "other", the PEC providers, …), which is just a password.
_APP_PASSWORD_PROVIDERS = frozenset({"google", "microsoft", "yahoo", "icloud"})


# Providers whose IMAP host is fixed: the edit form hides the host field for
# them, so no copy may tell the owner to check it.
FIXED_HOST_PROVIDERS = ("google", "microsoft", "yahoo", "icloud", "protonmail")


def _password_noun(account) -> str:
    """ "app password" or "password" — the one word every password surface uses."""
    return "app password" if account.provider in _APP_PASSWORD_PROVIDERS else "password"


def _auth_label(account) -> str:
    """How a mailbox signs in, in the owner's words (not the SASL mechanism)."""
    if str(getattr(account.auth_type, "value", account.auth_type)) != "oauth2":
        return _password_noun(account).capitalize()
    return {"google": "Google sign-in", "microsoft": "Microsoft sign-in"}.get(
        account.provider, "Sign-in"
    )


def _number_format(value):
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _postmark_date(value) -> str:
    """ "30 SEP 2026" — the postmark's date line, in UTC (the column's zone).
    static/js/postmark.js re-renders it in the viewer's own zone."""
    if not value:
        return ""
    ts = _as_utc(value)
    return f"{ts.day:02d} {_MONTHS[ts.month - 1]} {ts.year}"


def _postmark_time(value) -> str:
    """ "21:04" in UTC; the template marks it UTC until JS localises it."""
    if not value:
        return ""
    return _as_utc(value).strftime("%H:%M")


def _email_wrap(value) -> Markup:
    """An address that may only wrap after "@" and ".": each break point gets
    a <wbr>, and the CSS keeps the words themselves whole."""
    safe = escape(value or "")
    return safe.replace("@", Markup("@<wbr>")).replace(".", Markup(".<wbr>"))


def _iso(value) -> str:
    return _as_utc(value).isoformat() if value else ""


templates.env.filters["filesizeformat"] = _filesizeformat
templates.env.filters["cron_human"] = _cron_human
templates.env.filters["time_ago"] = _time_ago
templates.env.filters["time_until"] = _time_until
templates.env.filters["duration_human"] = _duration_human
templates.env.filters["time_ago_class"] = _time_ago_class
templates.env.filters["number"] = _number_format
templates.env.filters["postmark_date"] = _postmark_date
templates.env.filters["postmark_time"] = _postmark_time
templates.env.filters["iso"] = _iso
templates.env.filters["email_wrap"] = _email_wrap
templates.env.filters["auth_label"] = _auth_label
templates.env.filters["password_noun"] = _password_noun
templates.env.globals["fixed_host_providers"] = FIXED_HOST_PROVIDERS
templates.env.globals["error_category"] = error_category
templates.env.globals["sign_in_message"] = sign_in_message
templates.env.globals["sign_in_never_completed"] = sign_in_never_completed
templates.env.globals["webmail_url"] = settings.webmail_url
templates.env.globals["webmail_enabled"] = settings.webmail_enabled
# The webmail deep link has to name the same mailbox the ACL grants writes on
# and the Maildir is created under; hardcoding it here is how it drifts.
templates.env.globals["staging_mailbox"] = STAGING_MAILBOX
# The subscription checkboxes are built from these; see notification_service.
templates.env.globals["notification_problem_events"] = list(_ns.PROBLEM_EVENT_OPTIONS)
templates.env.globals["notification_activity_events"] = list(_ns.ACTIVITY_EVENT_OPTIONS)
templates.env.globals["app_version"] = __version__

# PAUSE_TOOLTIPS (honest copy per pause reason) lives in
# services/mailbox_status.py; it stays importable from here via the import above.


def owned_account_ids(db: Session, user: User) -> set[str]:
    """Ids of the accounts this user directly owns — one query per request,
    so `can_modify` never costs a lazy load per account."""
    return {
        row.account_id
        for row in db.query(account_owners.c.account_id).filter(account_owners.c.user_id == user.id)
    }


def can_modify_account(user: User, account_id: str, owned_ids: set[str]) -> bool:
    """Same rule as account_service.get_account_for_modify: admin or owner.
    A group member sees the account but cannot edit or reconnect it."""
    return user.role == UserRole.admin or account_id in owned_ids


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
        # "" = no explicit choice: the page follows prefers-color-scheme.
        return request.session.get("theme", "")
    return ""


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
    # Only an explicit choice pins the theme; otherwise the page follows
    # the browser (prefers-color-scheme).
    if (user.preferences or {}).get("theme"):
        request.session["theme"] = user.preferences["theme"]
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


def _config_backup_attention_items(repositories) -> list[dict]:
    """Attention entries for a repository whose CONFIGURATION backup failed.

    Mailbox backup failures already reached this panel; config backup failures
    did not, and lived only inside a button's title tooltip on the admin backup
    page — which is how one failed every night for 45 days unnoticed (#248).

    Repository-keyed, not account-keyed, so the entry carries its own href: the
    panel's default link is /accounts/{id} and a repository is not an account.
    """
    items: list[dict] = []
    for repo in repositories:
        if not repo.config_backup_enabled:
            continue
        if repo.last_config_backup_status != "failed":
            continue
        items.append(
            {
                "id": repo.id,
                "name": repo.name,
                "type": "error",
                "reason": (repo.last_config_backup_error or "Configuration snapshot failed")[:80],
                "href": "/admin/backup",
                "config_backup": True,
            }
        )
    return items


def _backup_attention_items(accounts, policies) -> list[dict]:
    """Attention entries for off-site backup state.

    Kept OUT of the per-account if/elif chain in the dashboard on purpose:
    that chain branches on sync state, so a mailbox that syncs fine but whose
    backup is broken would otherwise produce no entry at all.
    """
    by_id = {a.id: a for a in accounts}
    items: list[dict] = []
    for p in policies:
        account = by_id.get(p.account_id)
        if account is None:
            continue
        if p.last_status == BackupStatus.running:
            reason = "Off-site backup running"
            elapsed = _duration_human(p.last_run_at, None) if p.last_run_at else "—"
            if elapsed != "—":
                reason = f"{reason} · {elapsed}"
            items.append({"id": account.id, "name": account.name, "type": "info", "reason": reason})
        elif p.last_status == BackupStatus.failed:
            items.append(
                {
                    "id": account.id,
                    "name": account.name,
                    "type": "error",
                    "reason": (p.last_error or "Off-site backup failed")[:80],
                    "backup": True,
                }
            )
    return items


# Most urgent first: the owner verdict leads with the first of these, and the
# envelopes / ledger rows sort by it. Only presentation order — the states and
# their tones are the resolver's.
_URGENCY = {
    MailboxState.error: 0,
    MailboxState.sign_in_needed: 1,
    MailboxState.initial_stalled: 2,
    MailboxState.stale: 3,
    MailboxState.migrating: 4,
    MailboxState.first_sync: 5,
    MailboxState.syncing: 6,
    MailboxState.initial_sync: 7,
    MailboxState.paused: 8,
    MailboxState.stopped: 9,
    MailboxState.waiting: 10,
    MailboxState.suspended: 11,
    MailboxState.current: 12,
}


def _time_ago_short(now: datetime, ts: datetime | None) -> str:
    if not ts:
        return "—"
    delta = now - (ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts)
    secs = delta.total_seconds()
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{int(secs / 60)}m ago"
    if secs < 86400:
        return f"{int(secs / 3600)}h ago"
    return f"{delta.days}d ago"


def _mailbox_attention_item(account, st, can_modify: bool) -> dict:
    """The Needs attention entry for one mailbox whose verdict says so."""
    base = {"id": account.id, "name": account.name, "account": account, "status": st}
    if st.state == MailboxState.sign_in_needed:
        # Revoked/expired OAuth token (e.g. provider password change) — only
        # an owner can fix it, so surface it with a one-click reconnect, not
        # buried in the account page. Someone who can't reconnect is told who
        # can, not handed an action they lack.
        return {
            **base,
            "type": "reauth",
            "reason": sign_in_message(account, can_modify=can_modify),
            "never_connected": sign_in_never_completed(account),
            "provider": account.provider,
            "action": NextAction.reconnect.value if can_modify else None,
        }
    if st.state == MailboxState.error:
        action = st.action.value if st.action else None
        if st.action == NextAction.update_password and not can_modify:
            action = None
        return {
            **base,
            "type": "error",
            # The classified headline; the raw last_error (a whole mbsync log
            # at worst) stays on the detail page.
            "reason": st.detail,
            "action": action,
            "password_noun": _password_noun(account),
        }
    # stale, initial_stalled
    return {
        **base,
        "type": "stale",
        "reason": st.detail,
        "action": st.action.value if st.action else None,
        # `action` is agent-facing (None while paused, because the agent
        # trigger refuses); the UI trigger overrides a pause with a warning,
        # so the button follows the state.
        "sync_button": True,
    }


_BACKUP_FAILED_STAMP = {
    "badge": "stamp-error",
    "icon": "cloud-off",
    "spin": False,
    "label": "Snapshot failed",
}


def _verdict(accounts, statuses, backup_items=()) -> dict:
    """The owner home's postmark verdict: the most urgent mailbox if one needs
    action, otherwise the quietest true sentence about all of them.

    A failed off-site backup counts as needing attention even when the
    mailbox syncs fine — the card shows a red "Back up now", so the headline
    must not say nothing needs attention (#248 class). Sync problems still
    lead: they are the more urgent of the two."""
    latest = max(
        (st.last_success_at for st in statuses.values() if st.last_success_at), default=None
    )
    if not accounts:
        return {
            "status": None,
            "stamp": None,
            "lead": None,
            "reason": None,
            "attention": 0,
            "latest": None,
        }
    ordered = sorted(accounts, key=lambda a: (_URGENCY[statuses[a.id].state], a.name or ""))
    sync_ids = {a.id for a in accounts if statuses[a.id].needs_attention}
    failed_backup = {i["id"]: i for i in backup_items if i.get("backup")}
    attention = len(sync_ids | set(failed_backup))
    lead = ordered[0]
    stamp = statuses[lead.id]
    reason = None
    if not sync_ids and failed_backup:
        lead = next(a for a in ordered if a.id in failed_backup)
        stamp = _BACKUP_FAILED_STAMP
        reason = failed_backup[lead.id]["reason"]
    return {
        "status": statuses[lead.id],
        "stamp": stamp,
        "lead": lead,
        "reason": reason,
        "attention": attention,
        "all_current": attention == 0
        and all(st.state == MailboxState.current for st in statuses.values()),
        "latest": latest,
    }


def _dashboard_context(db: Session, user: User, accounts: list, *, admin_view: bool) -> dict:
    """Everything both the admin ledger and the owner home read. One verdict
    per mailbox (services/mailbox_status.py): every count, mark, row and
    attention item below reads it; none re-derives health."""
    now = datetime.now(UTC)
    statuses = resolve_many(db, accounts, now=now)
    owned_ids = owned_account_ids(db, user)

    total_messages = sum(a.total_messages for a in accounts)
    total_bytes = sum(a.maildir_size_bytes for a in accounts)
    # Only the error tone is an error: self-recovering pauses, stops and
    # sign-ins are not (sync-budget spec §8).
    error_count = sum(1 for st in statuses.values() if st.tone == Tone.error)

    stats = {
        "accounts": len(accounts),
        "messages": total_messages,
        "storage": _filesizeformat(total_bytes),
        "errors": error_count,
    }

    if admin_view:
        from mailfallback.services.store_service import get_default_store, list_stores
        from mailfallback.services.user_service import list_users

        stats["users"] = len(list_users(db))
        stats["stores"] = len(list_stores(db))

        # Add storage capacity from default store
        default_store = get_default_store(db)
        stats["storage_total"] = None
        if default_store:
            import shutil

            try:
                usage = shutil.disk_usage(default_store.path)
                stats["storage_total"] = _filesizeformat(usage.total)
            except OSError:
                stats["storage_total"] = None

    # Mailbox entries come from the resolver's needs_attention verdict. Backup
    # entries are appended separately, after it — a broken backup must not
    # hide behind (or be hidden by) a healthy sync.
    attention = [
        _mailbox_attention_item(a, statuses[a.id], can_modify_account(user, a.id, owned_ids))
        for a in accounts
        if statuses[a.id].needs_attention
    ]
    attention.sort(key=lambda item: _URGENCY[item["status"].state])

    account_ids = [a.id for a in accounts]
    backup_items: list[dict] = []
    if account_ids:
        by_id = {a.id: a for a in accounts}
        backup_items = _backup_attention_items(
            accounts,
            db.query(BackupPolicy).filter(BackupPolicy.account_id.in_(account_ids)).all(),
        )
        for item in backup_items:
            item["account"] = by_id[item["id"]]
            item["status"] = statuses[item["id"]]
        # A running back-up is work in progress, not a problem: it stays out
        # of Needs attention and its count, and reads as a quiet note on the
        # mailbox's own row / envelope instead.
        attention.extend(i for i in backup_items if i["type"] != "info")
    backup_notes = {i["id"]: i["reason"] for i in backup_items if i["type"] == "info"}

    # Repositories are an admin-level object; a plain user has no page to act on.
    if admin_view:
        from mailfallback.models import Repository

        attention.extend(_config_backup_attention_items(db.query(Repository).all()))

    recent_jobs = []
    if account_ids:
        jobs = (
            db.query(SyncJob)
            .filter(SyncJob.account_id.in_(account_ids))
            .order_by(SyncJob.requested_at.desc())
            .limit(5)
            .all()
        )
        account_map = {a.id: a.name for a in accounts}
        for j in jobs:
            outcome = resolve_job_outcome(j)
            recent_jobs.append(
                {
                    "account_id": j.account_id,
                    "account_name": account_map.get(j.account_id, "?"),
                    "status": j.status.value,
                    "time_ago": _time_ago_short(now, j.completed_at or j.requested_at),
                    "outcome_badge": outcome.badge,
                    "outcome_icon": outcome.icon,
                    "outcome_spin": outcome.spin,
                    "outcome_label": outcome.label,
                }
            )

    from sqlalchemy import func

    from mailfallback.models import Repository

    # Chain summary feeds the route strip. Four stages:
    # Source (mailboxes connected) → Local backup (local sync health) →
    # Repository (configured + reachable) → Snapshot (cached counts).
    # Source and Local backup are counted from the resolver's verdicts.
    tones = [st.tone for st in statuses.values()]
    states = [st.state for st in statuses.values()]
    mirrors_total = len(accounts)
    mirrors_failing = tones.count(Tone.error)
    mirrors_attention = tones.count(Tone.attention)
    connected = sum(1 for st in statuses.values() if st.signed_in)
    if not mirrors_total:
        source_tone = Tone.muted.value
    elif connected < mirrors_total:
        source_tone = Tone.attention.value
    else:
        source_tone = Tone.ok.value
    if mirrors_failing:
        local_tone = Tone.error.value
    elif mirrors_attention:
        local_tone = Tone.attention.value
    else:
        local_tone = Tone.ok.value

    # Off-site counts: everything on the admin ledger; on an owner page only
    # the policies of the viewer's own mailboxes and the repositories those
    # policies use — one user must never read another's policy or snapshot
    # counts off the route strip.
    def _policies():
        q = db.query(BackupPolicy)
        if not admin_view:
            q = q.filter(BackupPolicy.account_id.in_(account_ids or [""]))
        return q

    snapshot_q = db.query(func.coalesce(func.sum(BackupPolicy.last_snapshot_count), 0))
    if not admin_view:
        snapshot_q = snapshot_q.filter(BackupPolicy.account_id.in_(account_ids or [""]))
    snapshots_total = snapshot_q.scalar() or 0
    if admin_view:
        repositories_count = db.query(Repository).count()
    else:
        repositories_count = (
            _policies()
            .with_entities(func.count(func.distinct(BackupPolicy.destination_id)))
            .scalar()
            or 0
        )
    chain_summary = {
        "mailboxes": len(accounts),
        "mirrors_total": mirrors_total,
        "mirrors_failing": mirrors_failing,
        "mirrors_attention": mirrors_attention,
        "mirrors_ok": sum(1 for t in tones if t in (Tone.ok, Tone.active)),
        "mirrors_suspended": states.count(MailboxState.suspended),
        "mirrors_stopped": states.count(MailboxState.stopped),
        "mirrors_waiting": states.count(MailboxState.waiting),
        "connected": connected,
        "source_tone": source_tone,
        "local_tone": local_tone,
        "repositories": repositories_count,
        "policies": _policies().count(),
        "policies_with_recent_success": _policies()
        .filter(BackupPolicy.last_successful_run_at.isnot(None))
        .count(),
        "policies_failed": _policies()
        .filter(BackupPolicy.last_status == BackupStatus.failed)
        .count(),
        "policies_never_succeeded": _policies()
        .filter(BackupPolicy.last_successful_run_at.is_(None))
        .count(),
        "snapshots_total": int(snapshots_total),
    }

    from mailfallback.services.setup_state import get_setup_state

    # The checklist is an admin's first-run helper on the admin view only.
    setup_state = get_setup_state(db, user) if admin_view else None

    # First-time explainer on the route strip — shown once, dismissed via
    # POST /profile/dismiss-chain-explainer which sets this preference.
    show_chain_explainer = not (user.preferences or {}).get("chain_hero_seen", False)

    # Ledger / envelope order: what needs the reader first, then work in
    # progress, then the quiet ones.
    def order(a):
        return (_URGENCY[statuses[a.id].state], (a.name or "").lower())

    mailbox_attention_ids = {a.id for a in accounts if statuses[a.id].needs_attention}
    # A mailbox whose only problem is a failed off-site backup already sits
    # under Needs attention (as its backup item): list it there, once.
    mailbox_attention_ids |= {i["id"] for i in backup_items if i.get("backup")}
    other_rows = sorted((a for a in accounts if a.id not in mailbox_attention_ids), key=order)
    envelopes = [
        {
            "account": a,
            "status": statuses[a.id],
            "item": next(
                (
                    i
                    for i in attention
                    if i.get("id") == a.id
                    and i["type"] in ("reauth", "error", "stale")
                    and not i.get("backup")
                ),
                None,
            ),
            "backup_items": [i for i in backup_items if i["id"] == a.id],
        }
        for a in sorted(accounts, key=order)
    ]

    return {
        "user": user,
        "stats": stats,
        "statuses": statuses,
        "chain_summary": chain_summary,
        "attention": attention,
        "backup_notes": backup_notes,
        "other_rows": other_rows,
        "envelopes": envelopes,
        "verdict": _verdict(accounts, statuses, backup_items),
        "recent_jobs": recent_jobs,
        "setup_state": setup_state,
        "show_chain_explainer": show_chain_explainer,
        "dot_class": TONE_DOT,
    }


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    """Role-split home: an admin gets the ledger of every mailbox, anyone
    else gets the owner view of their own (the same page as /mine)."""
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login")
    if user.role != UserRole.admin:
        return _owner_home(request, db, user)
    context = _dashboard_context(db, user, get_accounts_for_user(db, user), admin_view=True)
    return templates.TemplateResponse(request=request, name="dashboard.html", context=context)


@router.get("/mine", response_class=HTMLResponse)
def my_mailboxes(request: Request, db: Session = Depends(get_db)):
    """The owner view of the mailboxes the current user owns or shares
    through a group. For an admin this is never everyone's mailboxes."""
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login")
    return _owner_home(request, db, user)


def _owner_home(request: Request, db: Session, user: User):
    context = _dashboard_context(db, user, get_own_accounts(db, user), admin_view=False)
    return templates.TemplateResponse(request=request, name="owner_home.html", context=context)


SYSTEM_CALM_HTML = (
    '<span class="health-calm" data-health="calm">'
    '<span class="mark mark-ok" aria-hidden="true"></span> System normal</span>'
)


@router.get("/partials/system-status", response_class=HTMLResponse)
def system_status_partial(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        # Not 200: an empty 200 would swap an empty bar in — the "empty means
        # calm" state this partial must never produce. A 401 makes core.js show
        # "Status unavailable" in a tab whose session expired or was demoted.
        return HTMLResponse("", status_code=401)

    from mailfallback.models import BackupPolicy, BackupStatus, JobStatus, RestoreJob
    from mailfallback.services.background_tasks import get_latest_task
    from mailfallback.services.dovecot_manager import get_cached_health

    dovecot = get_cached_health()
    fts = get_latest_task(db, "fts_reindex")
    resync = get_latest_task(db, "force_resync")

    # Global and admin-only: every account, resolved once. Counts are
    # ACCOUNTS (not jobs), from the same verdict every other surface uses.
    # O(accounts) every 5 s per admin tab — fine at self-hosted scale.
    all_accounts = db.query(Account).order_by(Account.name).all()
    statuses = resolve_many(db, all_accounts)
    syncing_count = sum(
        1 for st in statuses.values() if st.state in (MailboxState.first_sync, MailboxState.syncing)
    )

    def _row(a):
        st = statuses[a.id]
        # "Sync failed: The last sync failed." says nothing twice: an
        # unclassified error shows the label alone.
        detail = None if st.detail == UNKNOWN_ERROR_MESSAGE else st.detail
        return {"id": a.id, "name": a.name, "label": st.label, "detail": detail}

    error_accounts = [_row(a) for a in all_accounts if statuses[a.id].tone == Tone.error]
    # Sign-in only: stale / stalled live on the dashboard, and a deliberately
    # rare schedule must not keep the strip on forever.
    attention_accounts = [
        _row(a) for a in all_accounts if statuses[a.id].state == MailboxState.sign_in_needed
    ]

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
        or attention_accounts
        or active_restores
        or active_backups > 0
    )
    if not has_activity:
        # Explicit calm markup, never an empty body: an empty bar can't tell
        # "nothing is happening" from "the poll never answered".
        return HTMLResponse(SYSTEM_CALM_HTML)

    return templates.TemplateResponse(
        request=request,
        name="partials/system_status.html",
        context={
            "dovecot": dovecot,
            "fts": fts,
            "resync": resync,
            "syncing_count": syncing_count,
            "error_accounts": error_accounts,
            "attention_accounts": attention_accounts,
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
            "statuses": resolve_many(db, accounts),
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
            "statuses": resolve_many(db, accounts),
        },
    )
    if not any_syncing:
        response.headers["HX-Trigger"] = "sync-idle"
    return response
