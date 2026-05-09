# src/mailfallback/routers/ui_backup.py
import logging
import os
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.dependencies import get_db
from mailfallback.models import Account, AccountBackup, BackupDestination
from mailfallback.routers.ui import _get_session_user, templates
from mailfallback.security import encrypt_credentials
from mailfallback.services.account_service import assign_owner, get_account
from mailfallback.services.audit_service import log_action

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ui"])


# --- Backup Destination admin routes ---


@router.get("/admin/backup", response_class=HTMLResponse)
def admin_backup_page(request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/")

    destinations = db.query(BackupDestination).all()
    dest_account_counts = {}
    for dest in destinations:
        count = db.query(AccountBackup).filter(AccountBackup.destination_id == dest.id).count()
        dest_account_counts[dest.id] = count

    return templates.TemplateResponse(
        request=request,
        name="admin_backup.html",
        context={
            "user": user,
            "destinations": destinations,
            "dest_account_counts": dest_account_counts,
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

    dest = BackupDestination(
        name=name,
        backend_type=backend_type,
        restic_password=encrypt_credentials(restic_password, settings.secret_key),
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

    db.add(dest)
    db.commit()

    log_action(
        db,
        user=user,
        action="backup_destination.create",
        resource_type="backup_destination",
        resource_id=dest.id,
        resource_name=dest.name,
        ip_address=request.client.host if request.client else None,
    )
    request.session["flash_success"] = f"Backup destination {dest.name} created"
    return RedirectResponse("/admin/backup", status_code=303)


@router.post("/admin/backup/{dest_id}/delete")
async def admin_delete_backup_destination(
    dest_id: str, request: Request, db: Session = Depends(get_db)
):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)

    ref_count = db.query(AccountBackup).filter(AccountBackup.destination_id == dest_id).count()
    if ref_count > 0:
        request.session["flash_error"] = (
            f"Cannot delete: {ref_count} account(s) still use this destination"
        )
        return RedirectResponse("/admin/backup", status_code=303)

    dest = db.query(BackupDestination).filter(BackupDestination.id == dest_id).first()
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
    request.session["flash_success"] = f"Backup destination {dest_name} deleted"
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

    dest = db.query(BackupDestination).filter(BackupDestination.id == destination_id).first()
    if not dest:
        request.session["flash_error"] = "Destination not found"
        return RedirectResponse(f"/accounts/{account_id}", status_code=303)

    backup = db.query(AccountBackup).filter(AccountBackup.account_id == account_id).first()
    if backup:
        backup.destination_id = destination_id
        backup.schedule = schedule
        backup.retention_preset = retention_preset
    else:
        backup = AccountBackup(
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
    request.session["flash_success"] = "Backup configuration saved"
    return RedirectResponse(f"/accounts/{account_id}", status_code=303)


@router.post("/accounts/{account_id}/backup/now")
async def account_backup_now(account_id: str, request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    account = get_account(db, account_id, user)
    if not account:
        return RedirectResponse("/", status_code=303)

    backup = db.query(AccountBackup).filter(AccountBackup.account_id == account_id).first()
    if not backup:
        request.session["flash_error"] = "No backup configured for this account"
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
    request.session["flash_success"] = "Backup started"
    return RedirectResponse(f"/accounts/{account_id}", status_code=303)


@router.get("/accounts/{account_id}/backup/snapshots", response_class=HTMLResponse)
def account_backup_snapshots(account_id: str, request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user:
        return HTMLResponse("")

    account = get_account(db, account_id, user)
    if not account:
        return HTMLResponse("")

    backup = db.query(AccountBackup).filter(AccountBackup.account_id == account_id).first()
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
    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    account = get_account(db, account_id, user)
    if not account:
        return RedirectResponse("/", status_code=303)

    backup = db.query(AccountBackup).filter(AccountBackup.account_id == account_id).first()
    if not backup:
        request.session["flash_error"] = "No backup configured for this account"
        return RedirectResponse(f"/accounts/{account_id}", status_code=303)

    from mailfallback.services.restic_service import restore_snapshot

    temp_dir = (
        f"{account.store.path}/.offsite-restore"
        f"/{account.id}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
    )
    os.makedirs(temp_dir, exist_ok=True)

    try:
        restore_snapshot(backup.destination, account.id, snapshot_id, temp_dir)
    except Exception as e:
        request.session["flash_error"] = f"Restore failed: {e}"
        return RedirectResponse(f"/accounts/{account_id}", status_code=303)

    restored_account = Account(
        name=f"Backup {account.name} ({datetime.now(UTC).strftime('%Y-%m-%d')})",
        email_address=account.email_address,
        imap_host="restored",
        imap_port=0,
        maildir_path=temp_dir,
        store_id=account.store_id,
        suspended=True,
    )
    db.add(restored_account)
    db.flush()
    assign_owner(db, restored_account.id, user.id)
    db.commit()

    log_action(
        db,
        user=user,
        action="account.backup_restore",
        resource_type="account",
        resource_id=account_id,
        resource_name=account.email_address or account.name,
        ip_address=request.client.host if request.client else None,
        details={"snapshot_id": snapshot_id, "restored_account_id": restored_account.id},
    )
    request.session["flash_success"] = f"Snapshot restored as '{restored_account.name}'"
    return RedirectResponse(f"/accounts/{account_id}", status_code=303)
