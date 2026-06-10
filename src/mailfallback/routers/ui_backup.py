# src/mailfallback/routers/ui_backup.py
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.dependencies import get_db
from mailfallback.models import BackendType, BackupPolicy, Repository
from mailfallback.routers.ui import _get_session_user, templates
from mailfallback.security import encrypt_credentials
from mailfallback.services.account_service import get_account
from mailfallback.services.audit_service import log_action

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ui"])


# --- Backup Destination admin routes ---


@router.get("/admin/backup", response_class=HTMLResponse)
def admin_backup_page(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/")

    from datetime import timedelta

    from sqlalchemy import func

    destinations = db.query(Repository).all()
    # Wave 4: per-Repository status quartet (mailbox count, snapshot count,
    # last successful back-up, derived health). Read from cached
    # BackupPolicy columns; never shells restic.
    dest_stats = {}
    fresh_cutoff = datetime.now(UTC) - timedelta(days=2)
    for dest in destinations:
        agg = (
            db.query(
                func.count(BackupPolicy.id).label("policies"),
                func.coalesce(func.sum(BackupPolicy.last_snapshot_count), 0).label("snapshots"),
                func.max(BackupPolicy.last_successful_run_at).label("last_success"),
            )
            .filter(BackupPolicy.destination_id == dest.id)
            .one()
        )
        last_success = agg.last_success
        if agg.policies == 0:
            health = "empty"
        elif last_success is None:
            health = "no-backup"
        elif last_success.replace(tzinfo=UTC) >= fresh_cutoff:
            health = "ok"
        else:
            health = "stale"
        dest_stats[dest.id] = {
            "policies": agg.policies,
            "snapshots": int(agg.snapshots or 0),
            "last_success": last_success,
            "health": health,
        }

    return templates.TemplateResponse(
        request=request,
        name="admin_backup.html",
        context={
            "user": user,
            "destinations": destinations,
            "dest_stats": dest_stats,
            # Legacy alias for backward-compat with the existing template loop.
            "dest_account_counts": {d.id: dest_stats[d.id]["policies"] for d in destinations},
        },
    )


@router.post("/admin/backup/new")
async def admin_create_backup_destination(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)

    form = await request.form()
    name = form.get("name", "").strip()
    backend_type = form.get("backend_type", "")
    restic_password = form.get("restic_password", "").strip()

    if not name:
        request.session["flash_error"] = "Name is required"
        return RedirectResponse("/admin/backup", status_code=303)
    if backend_type not in ("s3", "local"):
        request.session["flash_error"] = "Invalid backend type"
        return RedirectResponse("/admin/backup", status_code=303)
    if not restic_password:
        request.session["flash_error"] = "Restic password is required"
        return RedirectResponse("/admin/backup", status_code=303)

    insecure_tls = bool(form.get("insecure_tls"))

    dest = Repository(
        name=name,
        backend_type=BackendType(backend_type),
        restic_password=encrypt_credentials(restic_password, settings.secret_key),
        insecure_tls=insecure_tls,
    )

    if backend_type == "s3":
        s3_endpoint = form.get("s3_endpoint", "").strip()
        s3_bucket = form.get("s3_bucket", "").strip()
        s3_access_key = form.get("s3_access_key", "").strip()
        s3_secret_key = form.get("s3_secret_key", "").strip()
        if not all([s3_endpoint, s3_bucket, s3_access_key, s3_secret_key]):
            request.session["flash_error"] = "All S3 fields are required"
            return RedirectResponse("/admin/backup", status_code=303)
        dest.s3_endpoint = encrypt_credentials(s3_endpoint, settings.secret_key)
        dest.s3_bucket = encrypt_credentials(s3_bucket, settings.secret_key)
        dest.s3_access_key = encrypt_credentials(s3_access_key, settings.secret_key)
        dest.s3_secret_key = encrypt_credentials(s3_secret_key, settings.secret_key)
    else:
        local_path = form.get("local_path", "").strip()
        if not local_path:
            request.session["flash_error"] = "Local path is required"
            return RedirectResponse("/admin/backup", status_code=303)
        dest.local_path = encrypt_credentials(local_path, settings.secret_key)

    from mailfallback.services.restic_service import test_destination

    test_result = test_destination(dest)
    if not test_result["ok"]:
        error_msg = test_result.get("error", "Unknown error")
        request.session["flash_error"] = f"Connection test failed: {error_msg}"
        return RedirectResponse("/admin/backup", status_code=303)

    db.add(dest)
    db.commit()
    db.refresh(dest)

    log_action(
        db,
        user=user,
        action="backup_destination.create",
        resource_type="backup_destination",
        resource_id=dest.id,
        resource_name=dest.name,
        ip_address=request.client.host if request.client else None,
    )
    request.session["flash_success"] = f"Repository {dest.name} created"
    return RedirectResponse("/admin/backup", status_code=303)


@router.post("/admin/backup/{dest_id}/delete")
async def admin_delete_backup_destination(
    dest_id: str, request: Request, db: Session = Depends(get_db)
):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)

    ref_count = db.query(BackupPolicy).filter(BackupPolicy.destination_id == dest_id).count()
    if ref_count > 0:
        request.session["flash_error"] = (
            f"Cannot delete: {ref_count} account(s) still use this destination"
        )
        return RedirectResponse("/admin/backup", status_code=303)

    dest = db.query(Repository).filter(Repository.id == dest_id).first()
    dest_name = dest.name if dest else dest_id
    if dest:
        db.delete(dest)
        db.commit()

    log_action(
        db,
        user=user,
        action="backup_destination.delete",
        resource_type="backup_destination",
        resource_id=dest_id,
        resource_name=dest_name,
        ip_address=request.client.host if request.client else None,
    )
    request.session["flash_success"] = f"Repository {dest_name} deleted"
    return RedirectResponse("/admin/backup", status_code=303)


@router.post("/admin/backup/{dest_id}/edit")
async def admin_edit_backup_destination(
    dest_id: str, request: Request, db: Session = Depends(get_db)
):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)

    dest = db.query(Repository).filter(Repository.id == dest_id).first()
    if not dest:
        request.session["flash_error"] = "Destination not found"
        return RedirectResponse("/admin/backup", status_code=303)

    form = await request.form()
    name = form.get("name", "").strip()
    if name:
        dest.name = name

    if dest.backend_type.value == "s3":
        for field in ("s3_endpoint", "s3_bucket", "s3_access_key", "s3_secret_key"):
            val = form.get(field, "").strip()
            if val:
                setattr(dest, field, encrypt_credentials(val, settings.secret_key))
    else:
        local_path = form.get("local_path", "").strip()
        if local_path:
            dest.local_path = encrypt_credentials(local_path, settings.secret_key)

    restic_password = form.get("restic_password", "").strip()
    if restic_password:
        dest.restic_password = encrypt_credentials(restic_password, settings.secret_key)

    dest.insecure_tls = bool(form.get("insecure_tls"))

    from mailfallback.services.restic_service import test_destination

    test_result = test_destination(dest)
    if not test_result["ok"]:
        db.rollback()
        error_msg = test_result.get("error", "Unknown error")
        request.session["flash_error"] = f"Connection test failed — changes not saved: {error_msg}"
        return RedirectResponse("/admin/backup", status_code=303)

    db.commit()

    log_action(
        db,
        user=user,
        action="backup_destination.edit",
        resource_type="backup_destination",
        resource_id=dest_id,
        resource_name=dest.name,
        ip_address=request.client.host if request.client else None,
    )
    request.session["flash_success"] = f"Destination {dest.name} updated"
    return RedirectResponse("/admin/backup", status_code=303)


@router.post("/admin/backup/{dest_id}/test")
async def admin_test_backup_destination(
    dest_id: str, request: Request, db: Session = Depends(get_db)
):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)

    dest = db.query(Repository).filter(Repository.id == dest_id).first()
    if not dest:
        if request.headers.get("HX-Request"):
            return templates.TemplateResponse(
                request=request,
                name="partials/repo_test_result.html",
                context={"ok": False, "error": "Repository not found"},
            )
        request.session["flash_error"] = "Destination not found"
        return RedirectResponse("/admin/backup", status_code=303)

    from mailfallback.services.restic_service import test_destination

    result = test_destination(dest)
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            request=request,
            name="partials/repo_test_result.html",
            context={"ok": result["ok"], "error": result.get("error")},
        )
    if result["ok"]:
        request.session["flash_success"] = f"{dest.name}: connection OK"
    else:
        error_msg = result.get("error", "Unknown error")
        request.session["flash_error"] = f"{dest.name}: {error_msg}"
    return RedirectResponse("/admin/backup", status_code=303)


# --- Account backup routes ---


@router.post("/accounts/{account_id}/backup/configure")
async def account_backup_configure(
    account_id: str, request: Request, db: Session = Depends(get_db)
):
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    account = get_account(db, account_id, user)
    if not account:
        return RedirectResponse("/", status_code=303)

    form = await request.form()
    destination_id = form.get("destination_id", "").strip()
    schedule = form.get("schedule", "0 2 * * *").strip()
    retention_preset = form.get("retention_preset", "standard")

    if not destination_id:
        request.session["flash_error"] = "Destination is required"
        return RedirectResponse(f"/accounts/{account_id}", status_code=303)

    dest = db.query(Repository).filter(Repository.id == destination_id).first()
    if not dest:
        request.session["flash_error"] = "Destination not found"
        return RedirectResponse(f"/accounts/{account_id}", status_code=303)

    backup = db.query(BackupPolicy).filter(BackupPolicy.account_id == account_id).first()
    if backup:
        backup.destination_id = destination_id
        backup.schedule = schedule
        backup.retention_preset = retention_preset
    else:
        backup = BackupPolicy(
            account_id=account_id,
            destination_id=destination_id,
            schedule=schedule,
            retention_preset=retention_preset,
        )
        db.add(backup)
    db.commit()

    log_action(
        db,
        user=user,
        action="account.backup_configure",
        resource_type="account",
        resource_id=account_id,
        resource_name=account.email_address or account.name,
        ip_address=request.client.host if request.client else None,
    )
    request.session["flash_success"] = "Backup policy saved"
    return RedirectResponse(f"/accounts/{account_id}", status_code=303)


@router.post("/accounts/{account_id}/backup/now")
async def account_backup_now(account_id: str, request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    account = get_account(db, account_id, user)
    if not account:
        return RedirectResponse("/", status_code=303)

    backup = db.query(BackupPolicy).filter(BackupPolicy.account_id == account_id).first()
    if not backup:
        request.session["flash_error"] = "No off-site backup configured for this account"
        return RedirectResponse(f"/accounts/{account_id}", status_code=303)

    from mailfallback.services.backup_worker import submit_backup

    submit_backup(backup.id)

    log_action(
        db,
        user=user,
        action="account.backup_now",
        resource_type="account",
        resource_id=account_id,
        resource_name=account.email_address or account.name,
        ip_address=request.client.host if request.client else None,
    )
    request.session["flash_success"] = "Snapshot started"
    return RedirectResponse(f"/accounts/{account_id}", status_code=303)


@router.get("/accounts/{account_id}/backup/snapshots", response_class=HTMLResponse)
def account_backup_snapshots(account_id: str, request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user:
        return HTMLResponse("")

    account = get_account(db, account_id, user)
    if not account:
        return HTMLResponse("")

    backup = db.query(BackupPolicy).filter(BackupPolicy.account_id == account_id).first()
    if not backup:
        return HTMLResponse('<p class="text-muted">No backup configured.</p>')

    try:
        from mailfallback.services.restic_service import list_snapshots

        snapshots = list_snapshots(backup.destination, account.id)
    except Exception as e:
        logger.warning("Failed to list snapshots for account %s: %s", account_id, e)
        snapshots = []

    return templates.TemplateResponse(
        request=request,
        name="partials/backup_snapshots.html",
        context={
            "account": account,
            "snapshots": snapshots,
        },
    )


@router.post("/accounts/{account_id}/backup/restore/{snapshot_id}")
async def account_backup_restore(
    account_id: str,
    snapshot_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Restore a snapshot into a Recovery (read-only on-disk artefact).

    Replaces the legacy Account-as-recovery flow. The Recovery is attached
    to the source Account; Dovecot exposes it as an additional read-only
    namespace under the source account's owners. It is NEVER synced.
    """
    from mailfallback.services.recovery_service import create_recovery

    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    account = get_account(db, account_id, user)
    if not account:
        return RedirectResponse("/", status_code=303)

    try:
        recovery = create_recovery(db, account.id, snapshot_id)
    except ValueError as e:
        request.session["flash_error"] = str(e)
        return RedirectResponse(f"/accounts/{account_id}", status_code=303)

    log_action(
        db,
        user=user,
        action="account.backup_restore",
        resource_type="account",
        resource_id=account_id,
        resource_name=account.email_address or account.name,
        ip_address=request.client.host if request.client else None,
        details={"snapshot_id": snapshot_id, "recovery_id": recovery.id},
    )

    if recovery.status.value == "ready":
        request.session["flash_success"] = (
            f"Snapshot {snapshot_id} recovered. Browse it in webmail under the "
            f"'Recovered {snapshot_id}' folder, or delete it from the account page when done."
        )
    else:
        request.session["flash_error"] = f"Recovery failed: {recovery.error or 'unknown error'}"

    return RedirectResponse(f"/accounts/{account_id}", status_code=303)


@router.post("/accounts/{account_id}/recoveries/{recovery_id}/delete")
async def account_recovery_delete(
    account_id: str,
    recovery_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Delete a Recovery: removes the on-disk tree + DB row."""
    from mailfallback.services.recovery_service import delete_recovery

    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    account = get_account(db, account_id, user)
    if not account:
        return RedirectResponse("/", status_code=303)

    delete_recovery(db, recovery_id)

    log_action(
        db,
        user=user,
        action="account.recovery_delete",
        resource_type="account",
        resource_id=account_id,
        resource_name=account.email_address or account.name,
        ip_address=request.client.host if request.client else None,
        details={"recovery_id": recovery_id},
    )
    request.session["flash_success"] = "Recovery deleted."
    return RedirectResponse(f"/accounts/{account_id}", status_code=303)
