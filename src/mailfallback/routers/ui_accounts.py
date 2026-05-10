# src/mailfallback/routers/ui_accounts.py
import contextlib
import json
import threading

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session, joinedload

from mailfallback.dependencies import get_db
from mailfallback.models import (
    Account,
    AccountBackup,
    BackupDestination,
    JobStatus,
    StoreMigration,
    SyncJob,
)
from mailfallback.routers.ui import _get_session_user, templates
from mailfallback.services.account_service import (
    assign_owner,
    create_account,
    get_account,
    is_account_owner,
    remove_owner,
    update_account,
)
from mailfallback.services.audit_service import log_action
from mailfallback.services.imap_check import check_imap_credentials, validate_host_not_internal
from mailfallback.services.migration_service import (
    execute_account_migration,
    initiate_account_migration,
)
from mailfallback.services.store_service import (
    get_selectable_stores,
    get_store,
    get_user_store,
    list_stores,
)
from mailfallback.services.sync_progress import parse_mbsync_lines
from mailfallback.services.sync_service import list_jobs_for_account
from mailfallback.services.user_service import list_users

router = APIRouter(tags=["ui"])


@router.get("/accounts/{account_id}/sync-status", response_class=HTMLResponse)
def account_sync_status(account_id: str, request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user:
        return HTMLResponse("")
    account = get_account(db, account_id, user)
    if not account:
        return HTMLResponse("")

    state = account.sync_state.value
    response = templates.TemplateResponse(
        request=request,
        name="partials/sync_status.html",
        context={"state": state, "account_id": account_id},
    )
    if state != "syncing":
        response.headers["HX-Trigger"] = "sync-finished"
    return response


@router.get("/accounts/{account_id}/partials/stats", response_class=HTMLResponse)
def account_stats_partial(account_id: str, request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user:
        return HTMLResponse("")
    account = get_account(db, account_id, user)
    if not account:
        return HTMLResponse("")

    folder_stats = []
    if account.folder_stats:
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            folder_stats = json.loads(account.folder_stats)

    return templates.TemplateResponse(
        request=request,
        name="partials/account_stats.html",
        context={"account": account, "folder_stats": folder_stats},
    )


@router.get("/accounts/{account_id}/partials/live-log", response_class=HTMLResponse)
def account_live_log_partial(
    account_id: str, job_id: str, request: Request, db: Session = Depends(get_db)
):
    from mailfallback.services.sync_worker import get_live_log

    user = _get_session_user(request, db)
    if not user:
        return HTMLResponse("")
    account = get_account(db, account_id, user)
    if not account:
        return HTMLResponse("")

    live = get_live_log(job_id)
    finished = live is None
    if finished:
        from mailfallback.services.sync_service import get_job

        job = get_job(db, job_id)
        log_text = job.log if job else ""
    else:
        log_text = live

    return templates.TemplateResponse(
        request=request,
        name="partials/sync_live_log.html",
        context={
            "account_id": account_id,
            "job_id": job_id,
            "log_text": log_text,
            "finished": finished,
        },
    )


@router.get("/accounts/{account_id}/partials/sync-panel", response_class=HTMLResponse)
def account_sync_panel(account_id: str, request: Request, db: Session = Depends(get_db)):
    from mailfallback.services.sync_worker import get_live_log

    user = _get_session_user(request, db)
    if not user:
        return HTMLResponse("")
    account = get_account(db, account_id, user)
    if not account:
        return HTMLResponse("")

    hero_state, snap, last_job = _compute_hero_state(account, db)

    polling = hero_state in ("syncing", "syncing-indeterminate", "first-sync")

    if polling:
        running_job = (
            db.query(SyncJob)
            .filter(SyncJob.account_id == account_id, SyncJob.status == JobStatus.running)
            .first()
        )
        if running_job:
            live = get_live_log(running_job.id)
            if live:
                lines = live.splitlines()
                prior_count = None
                if account.folder_stats:
                    with contextlib.suppress(json.JSONDecodeError, TypeError):
                        prior_count = len(json.loads(account.folder_stats))
                snap = parse_mbsync_lines(lines, prior_folder_count=prior_count)

    migration = None
    if hero_state == "migrating":
        migration = (
            db.query(StoreMigration)
            .filter(StoreMigration.account_id == account_id)
            .order_by(StoreMigration.created_at.desc())
            .first()
        )

    response = templates.TemplateResponse(
        request=request,
        name="partials/sync_panel.html",
        context={
            "account": account,
            "hero_state": hero_state,
            "snap": snap,
            "last_job": last_job,
            "migration": migration,
            "user": user,
        },
    )
    if not polling:
        response.headers["HX-Trigger"] = "sync-finished"
    return response


def _compute_hero_state(account, db):
    snap = None
    last_job = (
        db.query(SyncJob)
        .filter(SyncJob.account_id == account.id)
        .order_by(SyncJob.completed_at.desc())
        .first()
    )

    if account.migrating:
        return "migrating", snap, last_job
    if account.suspended:
        return "paused", snap, last_job
    if not account.is_authenticated:
        return "sign-in-needed", snap, last_job

    if account.sync_state.value == "syncing":
        if account.last_sync_at is None:
            return "first-sync", snap, last_job
        return "syncing", snap, last_job

    if account.sync_state.value == "error":
        if last_job and last_job.parsed_summary:
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                import dataclasses

                from mailfallback.services.sync_progress import ProgressSnapshot

                data = json.loads(last_job.parsed_summary)
                fields = {f.name for f in dataclasses.fields(ProgressSnapshot)}
                snap = ProgressSnapshot(**{k: v for k, v in data.items() if k in fields})
        return "error", snap, last_job

    if account.last_sync_at is None:
        return "empty", snap, last_job

    return "idle", snap, last_job


@router.get("/accounts/{account_id}/partials/history", response_class=HTMLResponse)
def account_history_partial(account_id: str, request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user:
        return HTMLResponse("")
    account = get_account(db, account_id, user)
    if not account:
        return HTMLResponse("")

    jobs = list_jobs_for_account(db, account_id, limit=150)
    return templates.TemplateResponse(
        request=request,
        name="partials/account_history.html",
        context={"account": account, "jobs": jobs},
    )


@router.get("/accounts/new", response_class=HTMLResponse)
def account_form(request: Request, db: Session = Depends(get_db)):
    from mailfallback.config import settings

    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login")
    store = get_user_store(db, user)
    selectable_stores = get_selectable_stores(db, user)
    oauth_providers = {
        "google": bool(settings.google_client_id and settings.google_client_secret),
        "microsoft": bool(settings.microsoft_client_id and settings.microsoft_client_secret),
    }
    oauth_failed = request.query_params.get("oauth_failed")
    prefill = (
        {
            "email": request.query_params.get("email", ""),
            "name": request.query_params.get("name", ""),
        }
        if oauth_failed
        else {}
    )
    return templates.TemplateResponse(
        request=request,
        name="account_form.html",
        context={
            "user": user,
            "store": store,
            "selectable_stores": selectable_stores,
            "oauth_providers": oauth_providers,
            "oauth_failed": oauth_failed,
            "prefill": prefill,
            "error": None,
        },
    )


@router.post("/accounts/new")
async def account_form_submit(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    form = await request.form()
    try:
        auth_type = form["auth_type"]
        name = form["name"]
    except KeyError as e:
        request.session["flash_error"] = f"Missing required field: {e}"
        return RedirectResponse("/accounts/new", status_code=303)

    email_address = form.get("email_address", "")

    store_id_override = form.get("store_id")
    if store_id_override:
        allowed_ids = {s.id for s in user.allowed_stores}
        store = (
            get_store(db, store_id_override)
            if store_id_override in allowed_ids
            else get_user_store(db, user)
        )
    else:
        store = get_user_store(db, user)
    if not store:
        return templates.TemplateResponse(
            request=request,
            name="account_form.html",
            context={
                "user": user,
                "store": None,
                "selectable_stores": None,
                "error": "No store assigned. Contact your administrator.",
            },
        )

    provider = form.get("provider", "other")
    credentials = form.get("credentials") or None
    tls_type = form.get("tls_type", "IMAPS")
    imap_host = form.get("imap_host", "")
    try:
        imap_port = int(form.get("imap_port", 993))
    except (ValueError, TypeError):
        request.session["flash_error"] = "Invalid IMAP port value"
        return RedirectResponse("/accounts/new", status_code=303)
    auth_mechs = form.get("auth_mechs", "")

    try:
        validate_host_not_internal(imap_host)
    except ValueError as e:
        selectable_stores = get_selectable_stores(db, user)
        return templates.TemplateResponse(
            request=request,
            name="account_form.html",
            context={
                "user": user,
                "store": store,
                "selectable_stores": selectable_stores,
                "error": str(e),
            },
        )

    if auth_type == "app_password" and credentials:
        result = check_imap_credentials(
            host=imap_host,
            port=imap_port,
            tls_type=tls_type,
            username=email_address,
            password=credentials,
        )
        if not result["ok"] or result.get("login_ok") is False:
            error_msg = result.get("login_message") or result.get("message", "Connection failed")
            selectable_stores = get_selectable_stores(db, user)
            return templates.TemplateResponse(
                request=request,
                name="account_form.html",
                context={
                    "user": user,
                    "store": store,
                    "selectable_stores": selectable_stores,
                    "error": f"Credential verification failed: {error_msg}",
                },
            )

    from mailfallback.services.provider_discovery import discover_provider

    account = create_account(
        db,
        name=name,
        email_address=email_address,
        imap_host=imap_host,
        imap_port=imap_port,
        auth_type=auth_type,
        store=store,
        credentials=credentials,
        provider=provider,
    )
    account.tls_type = tls_type
    account.imap_user = email_address
    extra = {}
    if auth_mechs:
        extra["auth_mechs"] = auth_mechs
    domain = (email_address.split("@")[1:] or [""])[0]
    if domain:
        disc = discover_provider(domain)
        if disc and disc.get("patterns"):
            extra["patterns"] = disc["patterns"]
    account.extra_config = json.dumps(extra) if extra else None
    db.commit()
    assign_owner(db, account.id, user.id)
    log_action(
        db,
        user=user,
        action="account.create",
        resource_type="account",
        resource_id=account.id,
        resource_name=account.email_address or account.name,
        ip_address=request.client.host if request.client else None,
    )
    return RedirectResponse(f"/accounts/{account.id}", status_code=303)


@router.get("/accounts/{account_id}", response_class=HTMLResponse)
def account_detail(account_id: str, request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login")

    account = get_account(db, account_id, user)
    if not account:
        return RedirectResponse("/")
    jobs = list_jobs_for_account(db, account_id, limit=150)

    folder_stats = []
    if account.folder_stats:
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            folder_stats = json.loads(account.folder_stats)

    extra = {}
    if account.extra_config:
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            extra = json.loads(account.extra_config)

    stores = list_stores(db) if user.role.value == "admin" else []
    all_users_list = (
        list_users(db) if user.role.value == "admin" or is_account_owner(user, account) else []
    )

    flash_error = request.session.pop("flash_error", None)

    hero_state, snap, last_job = _compute_hero_state(account, db)

    migration = None
    if hero_state == "migrating":
        migration = (
            db.query(StoreMigration)
            .filter(StoreMigration.account_id == account_id)
            .order_by(StoreMigration.created_at.desc())
            .first()
        )

    backup_config = (
        db.query(AccountBackup)
        .options(joinedload(AccountBackup.destination))
        .filter(AccountBackup.account_id == account_id)
        .first()
    )
    backup_destinations = db.query(BackupDestination).all()

    return templates.TemplateResponse(
        request=request,
        name="account_detail.html",
        context={
            "user": user,
            "account": account,
            "jobs": jobs,
            "folder_stats": folder_stats,
            "extra": extra,
            "stores": stores,
            "all_users": all_users_list,
            "is_owner": is_account_owner(user, account),
            "flash_error": flash_error,
            "hero_state": hero_state,
            "snap": snap,
            "last_job": last_job,
            "migration": migration,
            "backup_config": backup_config,
            "backup_destinations": backup_destinations,
        },
    )


@router.post("/accounts/{account_id}/edit")
async def account_edit_submit(account_id: str, request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    form = await request.form()
    updates = {}
    editable = (
        "name",
        "email_address",
        "imap_host",
        "imap_port",
        "sync_schedule",
        "credentials",
        "provider",
        "tls_type",
    )
    clearable_fields = {"sync_schedule"}
    for key in editable:
        val = form.get(key, "")
        if val:
            if key == "imap_port":
                try:
                    updates[key] = int(val)
                except (ValueError, TypeError):
                    request.session["flash_error"] = "Invalid IMAP port value"
                    return RedirectResponse(f"/accounts/{account_id}", status_code=303)
            else:
                updates[key] = val
        elif key in clearable_fields:
            updates[key] = ""

    account = get_account(db, account_id, user)
    if not account:
        return RedirectResponse("/", status_code=303)

    if "imap_host" in updates:
        try:
            validate_host_not_internal(updates["imap_host"])
        except ValueError as e:
            request.session["flash_error"] = str(e)
            return RedirectResponse(f"/accounts/{account_id}", status_code=303)

    known_providers = {"google", "microsoft", "yahoo", "icloud", "protonmail"}
    if account.provider in known_providers:
        for key in ("imap_host", "imap_port", "tls_type", "provider"):
            updates.pop(key, None)
    if account.auth_type.value == "oauth2":
        updates.pop("credentials", None)

    extra = {}
    if form.get("sync_direction"):
        extra["sync"] = form["sync_direction"]
    if form.get("create_policy"):
        extra["create"] = form["create_policy"]
    if form.get("expunge_policy"):
        extra["expunge"] = form["expunge_policy"]
    for key in (
        "patterns",
        "max_messages",
        "max_size",
        "timeout",
        "pipeline_depth",
        "disable_extensions",
        "auth_mechs",
    ):
        val = form.get(key, "")
        if val and val != "0":
            extra[key] = val
    if form.get("copy_arrival_date"):
        extra["copy_arrival_date"] = True
    updates["extra_config"] = json.dumps(extra) if extra else None

    update_account(db, account_id, user, **updates)
    log_action(
        db,
        user=user,
        action="account.edit",
        resource_type="account",
        resource_id=account_id,
        resource_name=account.email_address or account.name,
        ip_address=request.client.host if request.client else None,
    )
    return RedirectResponse(f"/accounts/{account_id}", status_code=303)


@router.post("/accounts/{account_id}/toggle-visible")
async def account_toggle_visible(
    account_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    account = get_account(db, account_id, user)
    if account:
        update_account(db, account_id, user, enabled=not account.enabled)
        log_action(
            db,
            user=user,
            action="account.edit",
            resource_type="account",
            resource_id=account_id,
            resource_name=account.email_address or account.name,
            ip_address=request.client.host if request.client else None,
            details={"toggled": "visibility"},
        )
    return RedirectResponse(f"/accounts/{account_id}", status_code=303)


@router.post("/accounts/{account_id}/toggle-suspend")
async def account_toggle_suspend(
    account_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    account = get_account(db, account_id, user)
    if account:
        was_suspended = account.suspended
        update_account(db, account_id, user, suspended=not account.suspended)
        action_name = "account.unsuspend" if was_suspended else "account.suspend"
        log_action(
            db,
            user=user,
            action=action_name,
            resource_type="account",
            resource_id=account_id,
            resource_name=account.email_address or account.name,
            ip_address=request.client.host if request.client else None,
        )
    return RedirectResponse(f"/accounts/{account_id}", status_code=303)


@router.post("/accounts/{account_id}/migrate")
async def account_migrate(
    account_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)

    form = await request.form()
    target_store_id = form["target_store_id"]

    try:
        migration = initiate_account_migration(db, account_id, target_store_id)
        log_action(
            db,
            user=user,
            action="account.migrate",
            resource_type="account",
            resource_id=account_id,
            ip_address=request.client.host if request.client else None,
        )
    except ValueError as e:
        return RedirectResponse(f"/accounts/{account_id}?error={e}", status_code=303)

    def run():
        from mailfallback.db import SessionLocal

        mdb = SessionLocal()
        try:
            execute_account_migration(mdb, migration.id)
        finally:
            mdb.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    return RedirectResponse(f"/accounts/{account_id}", status_code=303)


@router.get("/accounts/{account_id}/migration-progress", response_class=HTMLResponse)
def account_migration_progress(
    account_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return HTMLResponse("")

    migration = (
        db.query(StoreMigration)
        .filter(StoreMigration.account_id == account_id)
        .order_by(StoreMigration.created_at.desc())
        .first()
    )
    if not migration:
        return HTMLResponse("")

    pct = 0
    if migration.total_bytes > 0:
        pct = int(migration.copied_bytes * 100 / migration.total_bytes)

    response = templates.TemplateResponse(
        request=request,
        name="partials/migration_progress.html",
        context={
            "migration": migration,
            "account_id": account_id,
            "pct": pct,
        },
    )
    if migration.status.value == "completed":
        response.headers["HX-Refresh"] = "true"
    return response


@router.post("/accounts/{account_id}/cancel-migration")
async def account_cancel_migration(
    account_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)

    account = db.query(Account).filter(Account.id == account_id).first()
    if account and account.migrating:
        from mailfallback.models import MigrationStatus, StoreMigration

        active = (
            db.query(StoreMigration)
            .filter(
                StoreMigration.account_id == account_id,
                StoreMigration.status.in_(
                    [MigrationStatus.pending, MigrationStatus.copying, MigrationStatus.verifying]
                ),
            )
            .first()
        )
        if active:
            from mailfallback.services.migration_service import request_migration_cancel

            request_migration_cancel(active.id)
        else:
            account.migrating = False
            db.commit()
    return RedirectResponse(f"/accounts/{account_id}", status_code=303)


@router.post("/accounts/{account_id}/add-owner")
async def account_add_owner(account_id: str, request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    account = get_account(db, account_id, user)
    if not account:
        return RedirectResponse("/", status_code=303)
    if user.role.value != "admin":
        return RedirectResponse(f"/accounts/{account_id}", status_code=303)
    form = await request.form()
    assign_owner(db, account_id, form["user_id"])
    log_action(
        db,
        user=user,
        action="account.add_owner",
        resource_type="account",
        resource_id=account_id,
        resource_name=form["user_id"],
        ip_address=request.client.host if request.client else None,
    )
    return RedirectResponse(f"/accounts/{account_id}", status_code=303)


@router.post("/accounts/{account_id}/remove-owner")
async def account_remove_owner(account_id: str, request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    account = get_account(db, account_id, user)
    if not account:
        return RedirectResponse("/", status_code=303)
    form = await request.form()
    target_user_id = form["user_id"]
    if user.role.value != "admin" and target_user_id != user.id:
        return RedirectResponse(f"/accounts/{account_id}", status_code=303)
    remove_owner(db, account_id, target_user_id)
    log_action(
        db,
        user=user,
        action="account.remove_owner",
        resource_type="account",
        resource_id=account_id,
        resource_name=form["user_id"],
        ip_address=request.client.host if request.client else None,
    )
    return RedirectResponse(f"/accounts/{account_id}", status_code=303)
