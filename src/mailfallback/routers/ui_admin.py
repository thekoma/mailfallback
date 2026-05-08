# src/mailfallback/routers/ui_admin.py
import logging
import shutil
import threading
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.dependencies import get_db
from mailfallback.models import (
    Account,
    Group,
    MailStore,
    MigrationStatus,
    StoreMigration,
    User,
    UserRole,
)
from mailfallback.routers.ui import _get_session_user, templates
from mailfallback.security import verify_password
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
        return RedirectResponse("/admin/users?error=password_too_short", status_code=303)

    if not _admin_pw_verified(request):
        admin_password = form.get("admin_password", "")
        if not admin_password or not verify_password(admin_password, user.password_hash):
            return RedirectResponse("/admin/users?error=invalid_admin_password", status_code=303)
        request.session["admin_pw_verified_at"] = time.time()

    change_password(db, target_user_id, new_password)
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
        update_user(db, target_user_id, enabled=not target.enabled)
        log_action(
            db,
            user=user,
            action="user.toggle",
            resource_type="user",
            resource_id=target_user_id,
            resource_name=target.username,
            ip_address=request.client.host if request.client else None,
        )
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

    delete_user(db, target_user_id)
    log_action(
        db,
        user=user,
        action="user.delete",
        resource_type="user",
        resource_id=target_user_id,
        ip_address=request.client.host if request.client else None,
    )
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
        return RedirectResponse(f"/admin/users?error={e}", status_code=303)

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
    error = request.query_params.get("error")
    return templates.TemplateResponse(
        request=request,
        name="admin_users.html",
        context={
            "user": user,
            "users": users,
            "stores": stores,
            "admin_verified": _admin_pw_verified(request),
            "error": error,
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
        return RedirectResponse("/admin/users?error=invalid_role", status_code=303)
    password = form["password"]
    if len(password) < MIN_PASSWORD_LENGTH:
        return RedirectResponse("/admin/users?error=password_too_short", status_code=303)
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
        },
    )


# --- Store management ---


@router.get("/admin/stores", response_class=HTMLResponse)
def admin_stores_page(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/")
    stores = list_stores(db)
    all_users = list_users(db)
    error = request.query_params.get("error")

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
            "error": error,
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
    return RedirectResponse("/admin/stores", status_code=303)


@router.post("/admin/stores/{store_id}/toggle")
async def admin_toggle_store(store_id: str, request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)
    store = db.query(MailStore).filter(MailStore.id == store_id).first()
    if store:
        update_store(db, store_id, enabled=not store.enabled)
        log_action(
            db,
            user=user,
            action="store.edit",
            resource_type="store",
            resource_id=store_id,
            resource_name=store.name,
            ip_address=request.client.host if request.client else None,
        )
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
        return RedirectResponse(f"/admin/stores?error={error}", status_code=303)
    log_action(
        db,
        user=user,
        action="store.delete",
        resource_type="store",
        resource_id=store_id,
        ip_address=request.client.host if request.client else None,
    )
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
        return RedirectResponse(f"/admin/stores?error={e}", status_code=303)

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
    sso_sync = bool(form.get("sso_sync"))
    update_group(db, group_id, sso_sync=sso_sync)
    if user.role == UserRole.admin:
        group.members = db.query(User).filter(User.id.in_(member_ids)).all() if member_ids else []
        set_group_accounts(db, group_id, account_ids)
    else:
        owned_account_ids = {a.id for a in user.accounts}
        safe_account_ids = [aid for aid in account_ids if aid in owned_account_ids]
        set_group_accounts(db, group_id, safe_account_ids)
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
