# src/mailfallback/routers/ui_admin.py
import logging
import shutil
import threading
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from mailfallback.config import settings
from mailfallback.dependencies import get_db
from mailfallback.models import (
    Account,
    Group,
    MailStore,
    MigrationStatus,
    Repository,
    StoreMigration,
    User,
    UserRole,
)
from mailfallback.routers.ui import _get_session_user, templates
from mailfallback.security import encrypt_credentials, verify_password
from mailfallback.services import config_backup_service
from mailfallback.services.audit_service import log_action
from mailfallback.services.group_service import (
    can_manage_group,
    create_group,
    delete_group,
    set_group_accounts,
    update_group,
)
from mailfallback.services.migration_service import (
    execute_home_migration,
    execute_store_drain,
    get_drain_status,
    initiate_home_migration,
    initiate_store_drain,
)
from mailfallback.services.store_service import (
    create_store,
    delete_orphaned_dirs,
    delete_store,
    ensure_default_store,
    get_orphaned_dirs,
    list_stores,
    set_allowed_stores,
    set_default_store,
    update_store,
)
from mailfallback.services.user_service import (
    MIN_PASSWORD_LENGTH,
    change_password,
    create_user,
    delete_user,
    list_users,
    set_allowed_repositories,
    update_user,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ui"])

ADMIN_PW_COOLDOWN = 60


def _admin_pw_verified(request: Request) -> bool:
    verified_at = request.session.get("admin_pw_verified_at")
    if not verified_at:
        return False
    return (time.time() - verified_at) < ADMIN_PW_COOLDOWN


@router.post("/admin/users/{target_user_id}/password")
async def admin_change_user_password(
    target_user_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)

    form = await request.form()
    new_password = form["new_password"]
    if len(new_password) < MIN_PASSWORD_LENGTH:
        request.session["flash_error"] = f"Password must be at least {MIN_PASSWORD_LENGTH} chars"
        return RedirectResponse("/admin/users", status_code=303)

    if user.password_hash and not _admin_pw_verified(request):
        admin_password = form.get("admin_password", "")
        if not admin_password or not verify_password(admin_password, user.password_hash):
            request.session["flash_error"] = "Admin password is incorrect"
            return RedirectResponse("/admin/users", status_code=303)
        request.session["admin_pw_verified_at"] = time.time()

    try:
        change_password(db, target_user_id, new_password)
    except ValueError as e:
        request.session["flash_error"] = str(e)
        return RedirectResponse("/admin/users", status_code=303)
    target = db.query(User).filter(User.id == target_user_id).first()
    log_action(
        db,
        user=user,
        action="user.password_reset",
        resource_type="user",
        resource_id=target_user_id,
        resource_name=target.username if target else target_user_id,
        ip_address=request.client.host if request.client else None,
    )
    target_name = target.username if target else target_user_id
    request.session["flash_success"] = f"Password updated for {target_name}"
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/admin/users/{target_user_id}/edit")
async def admin_edit_user(
    target_user_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)

    form = await request.form()
    updates = {}
    username = form.get("username", "").strip()
    if username:
        updates["username"] = username
    role = form.get("role")
    if role in ("admin", "user"):
        updates["role"] = role
    if updates:
        update_user(db, target_user_id, **updates)
        target = db.query(User).filter(User.id == target_user_id).first()
        log_action(
            db,
            user=user,
            action="user.edit",
            resource_type="user",
            resource_id=target_user_id,
            resource_name=target.username if target else target_user_id,
            ip_address=request.client.host if request.client else None,
        )
        request.session["flash_success"] = f"User {target.username if target else ''} updated"
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/admin/users/{target_user_id}/toggle")
async def admin_toggle_user(
    target_user_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)
    if target_user_id == user.id:
        return RedirectResponse("/admin/users", status_code=303)

    target = db.query(User).filter(User.id == target_user_id).first()
    if target:
        new_state = not target.enabled
        update_user(db, target_user_id, enabled=new_state)
        log_action(
            db,
            user=user,
            action="user.toggle",
            resource_type="user",
            resource_id=target_user_id,
            resource_name=target.username,
            ip_address=request.client.host if request.client else None,
        )
        status_label = "enabled" if new_state else "disabled"
        request.session["flash_success"] = f"User {target.username} {status_label}"
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/admin/users/{target_user_id}/delete")
async def admin_delete_user(
    target_user_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)
    if target_user_id == user.id:
        return RedirectResponse("/admin/users", status_code=303)

    target = db.query(User).filter(User.id == target_user_id).first()
    target_name = target.username if target else target_user_id
    delete_user(db, target_user_id)
    log_action(
        db,
        user=user,
        action="user.delete",
        resource_type="user",
        resource_id=target_user_id,
        ip_address=request.client.host if request.client else None,
    )
    request.session["flash_success"] = f"User {target_name} deleted"
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/admin/users/{target_user_id}/migrate")
async def admin_migrate_user(
    target_user_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)

    form = await request.form()
    target_store_id = form["target_store_id"]

    try:
        migration = initiate_home_migration(db, target_user_id, target_store_id)
    except ValueError as e:
        request.session["flash_error"] = str(e)
        return RedirectResponse("/admin/users", status_code=303)

    log_action(
        db,
        user=user,
        action="user.migrate",
        resource_type="user",
        resource_id=target_user_id,
        ip_address=request.client.host if request.client else None,
    )

    def run():
        from mailfallback.db import SessionLocal

        mdb = SessionLocal()
        try:
            execute_home_migration(mdb, migration.id)
        finally:
            mdb.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    return RedirectResponse("/admin/users", status_code=303)


@router.get("/admin/users/{target_user_id}/migration-progress", response_class=HTMLResponse)
def migration_progress(
    target_user_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return HTMLResponse("")

    migration = (
        db.query(StoreMigration)
        .filter(StoreMigration.user_id == target_user_id)
        .order_by(StoreMigration.created_at.desc())
        .first()
    )
    if not migration:
        return HTMLResponse("")

    if migration.total_bytes > 0:
        pct = int(migration.copied_bytes * 100 / migration.total_bytes)
    else:
        pct = 0

    response = templates.TemplateResponse(
        request=request,
        name="partials/migration_progress.html",
        context={
            "migration": migration,
            "user_id": target_user_id,
            "pct": pct,
        },
    )
    if migration.status.value == "completed":
        response.headers["HX-Refresh"] = "true"
    return response


@router.post("/admin/users/{target_user_id}/cancel-migration")
async def admin_cancel_migration(
    target_user_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)

    target = db.query(User).filter(User.id == target_user_id).first()
    if target and target.migrating:
        active = (
            db.query(StoreMigration)
            .filter(
                StoreMigration.user_id == target_user_id,
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
            target.migrating = False
            db.commit()
    log_action(
        db,
        user=user,
        action="user.cancel_migration",
        resource_type="user",
        resource_id=target_user_id,
        ip_address=request.client.host if request.client else None,
    )
    return RedirectResponse("/admin/users", status_code=303)


@router.get("/admin/users", response_class=HTMLResponse)
def admin_users_page(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/")
    users = list_users(db)
    stores = list_stores(db)
    return templates.TemplateResponse(
        request=request,
        name="admin_users.html",
        context={
            "user": user,
            "users": users,
            "stores": stores,
            "repositories": db.query(Repository).all(),
            "admin_verified": _admin_pw_verified(request),
        },
    )


@router.post("/admin/users/new")
async def admin_create_user(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)

    form = await request.form()
    role = form["role"]
    if role not in ("admin", "user"):
        request.session["flash_error"] = "Invalid role"
        return RedirectResponse("/admin/users", status_code=303)
    password = form["password"]
    if len(password) < MIN_PASSWORD_LENGTH:
        request.session["flash_error"] = f"Password must be at least {MIN_PASSWORD_LENGTH} chars"
        return RedirectResponse("/admin/users", status_code=303)
    store_id = form.get("store_id") or ensure_default_store(db).id
    new_user = create_user(db, form["username"], password, role, store_id=store_id)
    set_allowed_stores(db, new_user.id, [store_id])
    log_action(
        db,
        user=user,
        action="user.create",
        resource_type="user",
        resource_id=new_user.id,
        resource_name=new_user.username,
        ip_address=request.client.host if request.client else None,
    )
    request.session["flash_success"] = f"User {new_user.username} created"
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/admin/users/{target_user_id}/allowed-stores")
async def admin_set_allowed_stores(
    target_user_id: str, request: Request, db: Session = Depends(get_db)
):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)
    form = await request.form()
    store_ids = form.getlist("store_ids")
    error = set_allowed_stores(db, target_user_id, store_ids)
    if error:
        logger.warning("set_allowed_stores refused for %s: %s", target_user_id, error)
    log_action(
        db,
        user=user,
        action="user.set_allowed_stores",
        resource_type="user",
        resource_id=target_user_id,
        ip_address=request.client.host if request.client else None,
    )
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/admin/users/{target_user_id}/allowed-repositories")
async def admin_set_allowed_repositories(
    target_user_id: str, request: Request, db: Session = Depends(get_db)
):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)
    form = await request.form()
    repository_ids = form.getlist("repository_ids")
    error = set_allowed_repositories(db, target_user_id, repository_ids)
    if error:
        logger.warning("set_allowed_repositories refused for %s: %s", target_user_id, error)
    log_action(
        db,
        user=user,
        action="user.set_allowed_repositories",
        resource_type="user",
        resource_id=target_user_id,
        ip_address=request.client.host if request.client else None,
    )
    return RedirectResponse("/admin/users", status_code=303)


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/")

    from mailfallback.services.scheduler import scheduler

    scheduler_running = scheduler.running if scheduler else False
    scheduler_jobs = len(scheduler.get_jobs()) if scheduler and scheduler.running else 0

    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "request": request,
            "user": user,
            "total_accounts": db.query(Account).count(),
            "total_users": db.query(User).count(),
            "total_stores": len(list_stores(db)),
            "oidc_enabled": settings.oidc_enabled,
            "scheduler_running": scheduler_running,
            "scheduler_jobs": scheduler_jobs,
            "debug_mode": settings.debug,
            "webmail_enabled": settings.webmail_enabled,
            "tika_enabled": settings.tika_enabled,
            "tika_url": settings.tika_url,
            "dovecot_status": request.query_params.get("dovecot_status"),
            "dovecot_error": request.query_params.get("dovecot_error"),
            "fts_status": request.query_params.get("fts_status"),
            "resync_status": request.query_params.get("resync_status"),
        },
    )


# --- Dovecot management ---


@router.post("/admin/dovecot/health-check")
async def admin_dovecot_health(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)
    from mailfallback.services.dovecot_manager import check_dovecot_health

    result = check_dovecot_health()
    log_action(
        db,
        user=user,
        action="dovecot.health_check",
        resource_type="system",
        ip_address=request.client.host if request.client else None,
        details=result,
    )
    error = None if result["ok"] else result.get("error", "Health check failed")
    return RedirectResponse(
        f"/settings?dovecot_status={'ok' if result['ok'] else 'error'}&dovecot_error={error or ''}",
        status_code=303,
    )


@router.post("/admin/dovecot/fts-reindex")
async def admin_fts_reindex(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)
    from mailfallback.services.background_tasks import submit_fts_reindex

    task = submit_fts_reindex(db, user.id)
    log_action(
        db,
        user=user,
        action="dovecot.fts_reindex",
        resource_type="system",
        ip_address=request.client.host if request.client else None,
    )
    status = "started" if task else "already_running"
    return RedirectResponse(f"/settings?fts_status={status}", status_code=303)


@router.post("/admin/dovecot/force-resync")
async def admin_force_resync(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)
    from mailfallback.services.background_tasks import submit_force_resync

    task = submit_force_resync(db, user.id)
    log_action(
        db,
        user=user,
        action="dovecot.force_resync",
        resource_type="system",
        ip_address=request.client.host if request.client else None,
    )
    status = "started" if task else "already_running"
    return RedirectResponse(f"/settings?resync_status={status}", status_code=303)


# --- Disaster-recovery configuration restore ---


def _transient_repository_from_form(form):
    """Build a NOT-persisted Repository from DR-restore form fields, with
    values encrypted so the normal decrypt paths work."""
    from mailfallback.models import BackendType, Repository

    backend = form.get("backend_type", "s3")
    repo = Repository(
        name="__dr_restore__",
        backend_type=BackendType(backend),
        restic_password=encrypt_credentials(
            form.get("restic_password", "").strip(), settings.secret_key
        ),
        insecure_tls=bool(form.get("insecure_tls")),
    )
    if backend == "s3":
        for field in ("s3_endpoint", "s3_bucket", "s3_access_key", "s3_secret_key"):
            setattr(
                repo, field, encrypt_credentials(form.get(field, "").strip(), settings.secret_key)
            )
    else:
        repo.local_path = encrypt_credentials(
            form.get("local_path", "").strip(), settings.secret_key
        )
    return repo


def _fetch_and_decrypt(form) -> dict:
    import tempfile

    repo = _transient_repository_from_form(form)
    passphrase = form.get("passphrase", "")
    with tempfile.TemporaryDirectory(prefix="mfb-config-restore-") as tmpdir:
        path = config_backup_service.fetch_latest_config(repo, tmpdir)
        with open(path, "rb") as f:
            blob = f.read()
    return config_backup_service.decrypt_export(blob, passphrase)


@router.post("/admin/system/config-restore/preview", response_class=HTMLResponse)
async def config_restore_preview(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)
    form = await request.form()
    error = None
    counts: dict[str, int] = {}
    try:
        data = await run_in_threadpool(_fetch_and_decrypt, form)
        counts = {name: len(rows) for name, rows in data["tables"].items()}
    except config_backup_service.ConfigDecryptError as e:
        error = str(e)
    except Exception as e:
        error = str(e)[:200]
    return templates.TemplateResponse(
        request=request,
        name="partials/config_restore_preview.html",
        context={"error": error, "counts": counts, "form": dict(form)},
    )


@router.post("/admin/system/config-restore/confirm")
async def config_restore_confirm(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)
    form = await request.form()
    try:
        data = await run_in_threadpool(_fetch_and_decrypt, form)
        report = await run_in_threadpool(config_backup_service.import_export, db, data)
    except Exception as e:
        request.session["flash_error"] = f"Restore failed: {str(e)[:200]}"
        return RedirectResponse("/settings", status_code=303)

    imported = sum(report["imported"].values())
    skipped = sum(report["skipped"].values())
    log_action(
        db,
        user=user,
        action="config.restore",
        resource_type="config",
        details={
            "imported": report["imported"],
            "skipped": report["skipped"],
            "errors": report["errors"],
        },
        ip_address=request.client.host if request.client else None,
    )
    msg = f"Configuration restored: {imported} rows imported, {skipped} skipped"
    if report["errors"]:
        request.session["flash_error"] = f"{msg}, {len(report['errors'])} errors (see audit log)"
    else:
        request.session["flash_success"] = msg
    from mailfallback.services.scheduler import refresh_scheduler

    refresh_scheduler()
    return RedirectResponse("/settings", status_code=303)


# --- Store management ---


@router.get("/admin/stores", response_class=HTMLResponse)
def admin_stores_page(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/")
    stores = list_stores(db)
    all_users = list_users(db)

    store_stats = {}
    store_orphans = {}
    for store in stores:
        try:
            usage = shutil.disk_usage(store.path)
            store_stats[store.id] = {
                "total": usage.total,
                "used": usage.used,
                "free": usage.free,
            }
        except OSError:
            store_stats[store.id] = None
        store_orphans[store.id] = get_orphaned_dirs(db, store.id)

    return templates.TemplateResponse(
        request=request,
        name="admin_stores.html",
        context={
            "user": user,
            "stores": stores,
            "all_users": all_users,
            "store_stats": store_stats,
            "store_orphans": store_orphans,
        },
    )


@router.post("/admin/stores/new")
async def admin_create_store(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)
    form = await request.form()
    store = create_store(db, form["name"], form["path"])
    log_action(
        db,
        user=user,
        action="store.create",
        resource_type="store",
        resource_id=store.id,
        resource_name=store.name,
        ip_address=request.client.host if request.client else None,
    )
    request.session["flash_success"] = f"Store {store.name} created"
    return RedirectResponse("/admin/stores", status_code=303)


@router.post("/admin/stores/{store_id}/toggle")
async def admin_toggle_store(store_id: str, request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)
    store = db.query(MailStore).filter(MailStore.id == store_id).first()
    if store:
        new_state = not store.enabled
        update_store(db, store_id, enabled=new_state)
        log_action(
            db,
            user=user,
            action="store.edit",
            resource_type="store",
            resource_id=store_id,
            resource_name=store.name,
            ip_address=request.client.host if request.client else None,
        )
        status_label = "enabled" if new_state else "disabled"
        request.session["flash_success"] = f"Store {store.name} {status_label}"
    return RedirectResponse("/admin/stores", status_code=303)


@router.post("/admin/stores/{store_id}/rename")
async def admin_rename_store(store_id: str, request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)
    form = await request.form()
    name = form.get("name", "").strip()
    if name:
        update_store(db, store_id, name=name)
        log_action(
            db,
            user=user,
            action="store.edit",
            resource_type="store",
            resource_id=store_id,
            resource_name=name,
            ip_address=request.client.host if request.client else None,
        )
    return RedirectResponse("/admin/stores", status_code=303)


@router.post("/admin/stores/{store_id}/set-default")
async def admin_set_default_store(store_id: str, request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)
    set_default_store(db, store_id)
    log_action(
        db,
        user=user,
        action="store.set_default",
        resource_type="store",
        resource_id=store_id,
        ip_address=request.client.host if request.client else None,
    )
    return RedirectResponse("/admin/stores", status_code=303)


@router.post("/admin/stores/{store_id}/delete")
async def admin_delete_store(store_id: str, request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)
    ok, error = delete_store(db, store_id)
    if not ok:
        request.session["flash_error"] = error
        return RedirectResponse("/admin/stores", status_code=303)
    log_action(
        db,
        user=user,
        action="store.delete",
        resource_type="store",
        resource_id=store_id,
        ip_address=request.client.host if request.client else None,
    )
    request.session["flash_success"] = "Store deleted"
    return RedirectResponse("/admin/stores", status_code=303)


@router.post("/admin/stores/{store_id}/drain")
async def admin_drain_store(store_id: str, request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)

    form = await request.form()
    target_store_id = form["target_store_id"]

    try:
        migrations = initiate_store_drain(db, store_id, target_store_id)
    except ValueError as e:
        request.session["flash_error"] = str(e)
        return RedirectResponse("/admin/stores", status_code=303)

    if migrations:
        migration_ids = [m.id for m in migrations]

        def run():
            from mailfallback.db import SessionLocal

            mdb = SessionLocal()
            try:
                execute_store_drain(mdb, migration_ids)
            finally:
                mdb.close()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()

    log_action(
        db,
        user=user,
        action="store.drain",
        resource_type="store",
        resource_id=store_id,
        ip_address=request.client.host if request.client else None,
    )
    return RedirectResponse("/admin/stores", status_code=303)


@router.get("/admin/stores/{store_id}/drain-progress", response_class=HTMLResponse)
def admin_drain_progress(store_id: str, request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return HTMLResponse("")

    status = get_drain_status(db, store_id)
    store = db.query(MailStore).filter(MailStore.id == store_id).first()

    return templates.TemplateResponse(
        request=request,
        name="partials/drain_progress.html",
        context={"store": store, "drain": status},
    )


@router.post("/admin/stores/{store_id}/cleanup-orphans")
async def admin_cleanup_orphans(store_id: str, request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)
    delete_orphaned_dirs(db, store_id)
    log_action(
        db,
        user=user,
        action="store.cleanup_orphans",
        resource_type="store",
        resource_id=store_id,
        ip_address=request.client.host if request.client else None,
    )
    return RedirectResponse("/admin/stores", status_code=303)


# --- Group management ---


@router.get("/admin/groups", response_class=HTMLResponse)
def admin_groups_page(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login")
    if user.role.value == "admin":
        groups = db.query(Group).all()
    else:
        groups = db.query(Group).filter(Group.owner_id == user.id).all()
    if not groups and user.role.value != "admin":
        return RedirectResponse("/")
    all_users = list_users(db) if user.role.value == "admin" else []
    all_accounts = db.query(Account).all() if user.role.value == "admin" else []
    return templates.TemplateResponse(
        request=request,
        name="admin_groups.html",
        context={
            "user": user,
            "groups": groups,
            "all_users": all_users,
            "all_accounts": all_accounts,
        },
    )


@router.post("/admin/groups/new")
async def admin_create_group(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)
    form = await request.form()
    owner_id = form.get("owner_id") or user.id
    sso_sync = bool(form.get("sso_sync"))
    group = create_group(db, form["name"], owner_id, sso_sync=sso_sync)
    log_action(
        db,
        user=user,
        action="group.create",
        resource_type="group",
        resource_id=group.id,
        resource_name=group.name,
        ip_address=request.client.host if request.client else None,
    )
    return RedirectResponse("/admin/groups", status_code=303)


@router.post("/admin/groups/{group_id}/edit")
async def admin_edit_group(group_id: str, request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    group = db.query(Group).filter(Group.id == group_id).first()
    if not group or not can_manage_group(user, group):
        return RedirectResponse("/admin/groups", status_code=303)
    form = await request.form()
    member_ids = form.getlist("member_ids")
    account_ids = form.getlist("account_ids")
    if user.role == UserRole.admin:
        sso_sync = bool(form.get("sso_sync"))
        update_group(db, group_id, sso_sync=sso_sync)
        group.members = db.query(User).filter(User.id.in_(member_ids)).all() if member_ids else []
        set_group_accounts(db, group_id, account_ids)
    else:
        owned_account_ids = {a.id for a in user.accounts}
        safe_account_ids = [aid for aid in account_ids if aid in owned_account_ids]
        non_owned_ids = [a.id for a in group.accounts if a.id not in owned_account_ids]
        set_group_accounts(db, group_id, safe_account_ids + non_owned_ids)
    db.commit()
    log_action(
        db,
        user=user,
        action="group.edit",
        resource_type="group",
        resource_id=group_id,
        resource_name=group.name,
        ip_address=request.client.host if request.client else None,
    )
    return RedirectResponse("/admin/groups", status_code=303)


@router.post("/admin/groups/{group_id}/delete")
async def admin_delete_group_route(group_id: str, request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)
    group = db.query(Group).filter(Group.id == group_id).first()
    group_name = group.name if group else group_id
    delete_group(db, group_id)
    log_action(
        db,
        user=user,
        action="group.delete",
        resource_type="group",
        resource_id=group_id,
        resource_name=group_name,
        ip_address=request.client.host if request.client else None,
    )
    return RedirectResponse("/admin/groups", status_code=303)
