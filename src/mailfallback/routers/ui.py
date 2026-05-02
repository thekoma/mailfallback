# src/mailfallback/routers/ui.py
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.dependencies import get_db
from mailfallback.models import SyncJob, User
from mailfallback.services.account_service import get_accounts_for_user
from mailfallback.services.user_service import authenticate_user

router = APIRouter(tags=["ui"])

templates = Jinja2Templates(directory="src/mailfallback/templates")


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


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"oidc_enabled": settings.oidc_enabled, "error": None},
    )


@router.post("/login")
async def login_submit(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    user = authenticate_user(db, form["username"], form["password"])
    if not user:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"oidc_enabled": settings.oidc_enabled, "error": "Invalid credentials"},
        )
    request.session["user_id"] = user.id
    return RedirectResponse("/", status_code=303)


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login")
    accounts = get_accounts_for_user(db, user)

    total_messages = sum(a.total_messages for a in accounts)
    total_bytes = sum(a.maildir_size_bytes for a in accounts)
    error_count = sum(1 for a in accounts if a.sync_state.value == "error")

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
        if a.sync_state.value == "error":
            reason = a.last_error[:80] if a.last_error else "Sync failed"
            attention.append({"id": a.id, "name": a.name, "type": "error", "reason": reason})
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

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "user": user,
            "stats": stats,
            "attention": attention,
            "recent_jobs": recent_jobs,
        },
    )


@router.get("/accounts", response_class=HTMLResponse)
def accounts_page(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login")
    accounts = get_accounts_for_user(db, user)
    return templates.TemplateResponse(
        request=request,
        name="accounts.html",
        context={"user": user, "accounts": accounts},
    )


@router.get("/partials/accounts-table", response_class=HTMLResponse)
def accounts_table_partial(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user:
        return HTMLResponse("")
    accounts = get_accounts_for_user(db, user)
    return templates.TemplateResponse(
        request=request,
        name="partials/accounts_table.html",
        context={"user": user, "accounts": accounts},
    )
