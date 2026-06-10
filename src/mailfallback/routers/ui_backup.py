# src/mailfallback/routers/ui_backup.py
import logging
import re
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.dependencies import get_db
from mailfallback.models import (
    Account,
    BackendType,
    BackupPolicy,
    Repository,
    RepositoryAttachment,
)
from mailfallback.routers.ui import _get_session_user, templates
from mailfallback.routers.ui_admin import _transient_repository_from_form
from mailfallback.security import encrypt_credentials
from mailfallback.services import restic_service
from mailfallback.services.account_service import get_account
from mailfallback.services.audit_service import log_action

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ui"])

# Restic prefixes are single path segments (account UUIDs, the config prefix,
# or operator-named directories). Anything else could escape the repo root in
# build_repo_url's os.path.join for local backends.
_VALID_PREFIX_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _is_valid_prefix(prefix: str) -> bool:
    return bool(_VALID_PREFIX_RE.match(prefix)) and prefix not in (".", "..")


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

    config_backup_enabled = bool(form.get("config_backup_enabled"))
    config_passphrase = form.get("config_backup_passphrase", "").strip()
    if config_backup_enabled:
        if config_passphrase:
            if len(config_passphrase) < 12:
                request.session["flash_error"] = (
                    "Config snapshot passphrase must be at least 12 characters"
                )
                return RedirectResponse("/admin/backup", status_code=303)
            dest.config_backup_passphrase = encrypt_credentials(
                config_passphrase, settings.secret_key
            )
        elif not dest.config_backup_passphrase:
            request.session["flash_error"] = (
                "A passphrase is required to enable configuration snapshots"
            )
            return RedirectResponse("/admin/backup", status_code=303)
    dest.config_backup_enabled = config_backup_enabled

    from starlette.concurrency import run_in_threadpool

    test_result = await run_in_threadpool(restic_service.test_destination, dest)
    if not test_result["ok"]:
        error_msg = test_result.get("error", "Unknown error")
        request.session["flash_error"] = f"Connection test failed: {error_msg}"
        return RedirectResponse("/admin/backup", status_code=303)

    db.add(dest)
    db.commit()
    db.refresh(dest)

    from mailfallback.services.scheduler import config_backup_scheduler_jobs

    config_backup_scheduler_jobs(db)

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

    att_count = (
        db.query(RepositoryAttachment).filter(RepositoryAttachment.repository_id == dest_id).count()
    )
    dest = db.query(Repository).filter(Repository.id == dest_id).first()
    dest_name = dest.name if dest else dest_id
    if dest:
        db.delete(dest)
        db.commit()

        from mailfallback.services.scheduler import config_backup_scheduler_jobs

        config_backup_scheduler_jobs(db)

    log_action(
        db,
        user=user,
        action="backup_destination.delete",
        resource_type="backup_destination",
        resource_id=dest_id,
        resource_name=dest_name,
        ip_address=request.client.host if request.client else None,
    )
    msg = f"Repository {dest_name} deleted"
    if att_count:
        msg += f" ({att_count} attached prefix(es) unlinked)"
    request.session["flash_success"] = msg
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

    config_backup_enabled = bool(form.get("config_backup_enabled"))
    config_passphrase = form.get("config_backup_passphrase", "").strip()
    if config_backup_enabled:
        if config_passphrase:
            if len(config_passphrase) < 12:
                db.rollback()
                request.session["flash_error"] = (
                    "Config snapshot passphrase must be at least 12 characters"
                )
                return RedirectResponse("/admin/backup", status_code=303)
            dest.config_backup_passphrase = encrypt_credentials(
                config_passphrase, settings.secret_key
            )
        elif not dest.config_backup_passphrase:
            db.rollback()
            request.session["flash_error"] = (
                "A passphrase is required to enable configuration snapshots"
            )
            return RedirectResponse("/admin/backup", status_code=303)
    dest.config_backup_enabled = config_backup_enabled

    from starlette.concurrency import run_in_threadpool

    test_result = await run_in_threadpool(restic_service.test_destination, dest)
    if not test_result["ok"]:
        db.rollback()
        error_msg = test_result.get("error", "Unknown error")
        request.session["flash_error"] = f"Connection test failed — changes not saved: {error_msg}"
        return RedirectResponse("/admin/backup", status_code=303)

    db.commit()

    from mailfallback.services.scheduler import config_backup_scheduler_jobs

    config_backup_scheduler_jobs(db)

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


def _test_result_context(dest) -> dict:
    """Probe + best-effort prefix count for the enriched test partial."""
    from mailfallback.services import repo_inventory

    result = restic_service.test_destination(dest)
    count = None
    if result["ok"]:
        try:
            count = len(
                [p for p in repo_inventory.list_prefixes(dest) if p != repo_inventory.CONFIG_PREFIX]
            )
        except Exception:
            count = None
    return {"ok": result["ok"], "error": result.get("error"), "count": count}


@router.post("/admin/backup/test-connection", response_class=HTMLResponse)
async def admin_test_connection_transient(request: Request, db: Session = Depends(get_db)):
    """Test connection details from the wizard form before the Repository exists."""
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)

    form = await request.form()
    from starlette.concurrency import run_in_threadpool

    dest = _transient_repository_from_form(form)
    context = await run_in_threadpool(_test_result_context, dest)
    return templates.TemplateResponse(
        request=request, name="partials/repo_test_result.html", context=context
    )


@router.post("/admin/backup/{dest_id}/test")
def admin_test_backup_destination(dest_id: str, request: Request, db: Session = Depends(get_db)):
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

    context = _test_result_context(dest)
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            request=request,
            name="partials/repo_test_result.html",
            context=context,
        )
    if context["ok"]:
        request.session["flash_success"] = f"{dest.name}: connection OK"
    else:
        error_msg = context["error"] or "Unknown error"
        request.session["flash_error"] = f"{dest.name}: {error_msg}"
    return RedirectResponse("/admin/backup", status_code=303)


@router.post("/admin/backup/{dest_id}/config-backup")
def admin_run_config_backup(dest_id: str, request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)
    dest = db.query(Repository).filter(Repository.id == dest_id).first()
    if not dest or not dest.config_backup_enabled:
        request.session["flash_error"] = (
            "Configuration snapshots are not enabled for this repository"
        )
        return RedirectResponse("/admin/backup", status_code=303)

    from mailfallback.services.config_backup_service import run_config_backup

    result = run_config_backup(db, dest)
    if result["ok"]:
        request.session["flash_success"] = f"Configuration snapshot stored in {dest.name}"
    else:
        request.session["flash_error"] = f"Configuration snapshot failed: {result['error']}"
    log_action(
        db,
        user=user,
        action="backup_destination.config_backup",
        resource_type="backup_destination",
        resource_id=dest.id,
        resource_name=dest.name,
        ip_address=request.client.host if request.client else None,
    )
    return RedirectResponse("/admin/backup", status_code=303)


@router.post("/admin/backup/{dest_id}/backfill-tags")
def admin_backfill_tags(dest_id: str, request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)
    dest = db.query(Repository).filter(Repository.id == dest_id).first()
    if not dest:
        request.session["flash_error"] = "Repository not found"
        return RedirectResponse("/admin/backup", status_code=303)

    from mailfallback.services import repo_inventory

    report = repo_inventory.backfill_tags(db, dest)
    total = sum(report.values())
    log_action(
        db,
        user=user,
        action="backup_destination.backfill_tags",
        resource_type="backup_destination",
        resource_id=dest.id,
        resource_name=dest.name,
        details={"tagged": report},
        ip_address=request.client.host if request.client else None,
    )
    if total:
        request.session["flash_success"] = (
            f"Tagged {total} snapshot(s) across {len(report)} prefix(es)"
        )
    else:
        request.session["flash_success"] = "All snapshots already tagged"
    return RedirectResponse("/admin/backup", status_code=303)


@router.get("/admin/backup/{dest_id}/contents", response_class=HTMLResponse)
def admin_repo_contents(dest_id: str, request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)
    dest = db.query(Repository).filter(Repository.id == dest_id).first()
    if not dest:
        return HTMLResponse("Repository not found", status_code=404)

    from mailfallback.services import repo_inventory

    error = None
    entries: list[dict] = []
    try:
        prefixes = repo_inventory.list_prefixes(dest)
        entries = repo_inventory.classify(db, dest, prefixes)
    except Exception as e:
        error = str(e)[:200]

    accounts = db.query(Account).order_by(Account.name).all()
    return templates.TemplateResponse(
        request=request,
        name="partials/repo_contents.html",
        context={"dest": dest, "entries": entries, "error": error, "accounts": accounts},
    )


@router.get("/admin/backup/{dest_id}/contents/{prefix}/detail", response_class=HTMLResponse)
def admin_repo_prefix_detail(
    dest_id: str, prefix: str, request: Request, db: Session = Depends(get_db)
):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)
    dest = db.query(Repository).filter(Repository.id == dest_id).first()
    if not dest:
        return HTMLResponse("Repository not found", status_code=404)
    if not _is_valid_prefix(prefix):
        return HTMLResponse("Invalid prefix", status_code=400)

    from mailfallback.services import repo_inventory

    att = (
        db.query(RepositoryAttachment)
        .filter(
            RepositoryAttachment.repository_id == dest_id,
            RepositoryAttachment.prefix == prefix,
        )
        .first()
    )
    detail = repo_inventory.prefix_detail(
        dest, prefix, restic_password_enc=att.restic_password if att else None
    )
    return templates.TemplateResponse(
        request=request,
        name="partials/repo_prefix_detail.html",
        context={"detail": detail},
    )


@router.post("/admin/backup/{dest_id}/attach")
async def admin_repo_attach(dest_id: str, request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)
    dest = db.query(Repository).filter(Repository.id == dest_id).first()
    if not dest:
        request.session["flash_error"] = "Repository not found"
        return RedirectResponse("/admin/backup", status_code=303)

    form = await request.form()
    prefix = form.get("prefix", "").strip()
    account_id = form.get("account_id", "").strip()
    account = db.query(Account).filter(Account.id == account_id).first()
    if not prefix or not account:
        request.session["flash_error"] = "Prefix and account are required"
        return RedirectResponse("/admin/backup", status_code=303)

    if not _is_valid_prefix(prefix):
        request.session["flash_error"] = "Invalid prefix"
        return RedirectResponse("/admin/backup", status_code=303)

    from mailfallback.services.repo_inventory import CONFIG_PREFIX
    from mailfallback.services.s3_probe import LEGACY_TEST_PREFIX

    if prefix in (CONFIG_PREFIX, LEGACY_TEST_PREFIX.rstrip("/")):
        request.session["flash_error"] = "This prefix is reserved and cannot be attached"
        return RedirectResponse("/admin/backup", status_code=303)

    if db.query(Account).filter(Account.id == prefix).first():
        request.session["flash_error"] = (
            "This prefix belongs to a live mailbox and cannot be attached"
        )
        return RedirectResponse("/admin/backup", status_code=303)

    existing = (
        db.query(RepositoryAttachment)
        .filter(
            RepositoryAttachment.repository_id == dest_id,
            RepositoryAttachment.prefix == prefix,
        )
        .first()
    )
    if existing:
        request.session["flash_error"] = f"Prefix {prefix} is already attached"
        return RedirectResponse("/admin/backup", status_code=303)

    password = form.get("restic_password", "").strip()
    password_enc = encrypt_credentials(password, settings.secret_key) if password else None

    from starlette.concurrency import run_in_threadpool

    try:
        await run_in_threadpool(
            restic_service.list_snapshots, dest, prefix, restic_password_enc=password_enc
        )
    except Exception as e:
        request.session["flash_error"] = (
            f"Cannot open {prefix} with the given password: {str(e)[:150]}"
        )
        return RedirectResponse("/admin/backup", status_code=303)

    att = RepositoryAttachment(
        repository_id=dest_id, account_id=account.id, prefix=prefix, restic_password=password_enc
    )
    db.add(att)
    db.commit()
    log_action(
        db,
        user=user,
        action="backup_destination.attach",
        resource_type="backup_destination",
        resource_id=dest_id,
        resource_name=dest.name,
        ip_address=request.client.host if request.client else None,
        details={"prefix": prefix, "account_id": account.id},
    )
    request.session["flash_success"] = f"Attached {prefix} to {account.name}"
    return RedirectResponse("/admin/backup", status_code=303)


@router.post("/admin/backup/attachments/{attachment_id}/delete")
async def admin_repo_detach(attachment_id: str, request: Request, db: Session = Depends(get_db)):
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)
    att = db.query(RepositoryAttachment).filter(RepositoryAttachment.id == attachment_id).first()
    if att:
        repo_id = att.repository_id
        repo_name = att.repository.name if att.repository else repo_id
        prefix = att.prefix
        db.delete(att)
        db.commit()
        log_action(
            db,
            user=user,
            action="backup_destination.detach",
            resource_type="backup_destination",
            resource_id=repo_id,
            resource_name=repo_name,
            ip_address=request.client.host if request.client else None,
            details={"prefix": prefix},
        )
        request.session["flash_success"] = "Prefix detached"
    else:
        request.session["flash_error"] = "Attachment not found"
    return RedirectResponse("/admin/backup", status_code=303)


@router.post("/admin/backup/attachments/{attachment_id}/password")
async def admin_attachment_password(
    attachment_id: str, request: Request, db: Session = Depends(get_db)
):
    """Set or replace the restic password for an attached prefix.

    The password is validated against the sub-repo before being stored;
    a blank password falls back to the repository's own restic password.
    """
    user = _get_session_user(request, db)
    if not user or user.role.value != "admin":
        return RedirectResponse("/", status_code=303)
    att = db.query(RepositoryAttachment).filter(RepositoryAttachment.id == attachment_id).first()
    if not att:
        request.session["flash_error"] = "Attachment not found"
        return RedirectResponse("/admin/backup", status_code=303)

    form = await request.form()
    password = form.get("restic_password", "").strip()
    password_enc = encrypt_credentials(password, settings.secret_key) if password else None

    from starlette.concurrency import run_in_threadpool

    try:
        await run_in_threadpool(
            restic_service.list_snapshots,
            att.repository,
            att.prefix,
            restic_password_enc=password_enc,
        )
    except Exception as e:
        request.session["flash_error"] = (
            f"Cannot open {att.prefix} with the given password: {str(e)[:150]}"
        )
        return RedirectResponse("/admin/backup", status_code=303)

    att.restic_password = password_enc
    db.commit()
    log_action(
        db,
        user=user,
        action="backup_destination.attach_password",
        resource_type="backup_destination",
        resource_id=att.repository_id,
        resource_name=att.repository.name if att.repository else None,
        details={"prefix": att.prefix},
        ip_address=request.client.host if request.client else None,
    )
    request.session["flash_success"] = f"Password updated for {att.prefix}"
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

    if user.role.value != "admin":
        allowed_ids = {r.id for r in user.allowed_repositories}
        current_id = backup.destination_id if backup else None
        if destination_id not in allowed_ids and destination_id != current_id:
            request.session["flash_error"] = (
                "You are not allowed to use this repository — ask an administrator"
            )
            return RedirectResponse(f"/accounts/{account_id}", status_code=303)

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
    attachments = (
        db.query(RepositoryAttachment).filter(RepositoryAttachment.account_id == account_id).all()
    )
    if not backup and not attachments:
        return HTMLResponse('<p class="text-muted">No backup configured.</p>')

    snapshots = []
    if backup:
        try:
            snapshots = restic_service.list_snapshots(backup.destination, account.id)
        except Exception as e:
            logger.warning("Failed to list snapshots for account %s: %s", account_id, e)
            snapshots = []

    attached_sources = []
    for att in attachments:
        try:
            snaps = restic_service.list_snapshots(
                att.repository, att.prefix, restic_password_enc=att.restic_password
            )
        except Exception as e:
            snaps = []
            logger.warning("Cannot list attached prefix %s: %s", att.prefix, e)
        attached_sources.append({"attachment": att, "snapshots": snaps})

    return templates.TemplateResponse(
        request=request,
        name="partials/backup_snapshots.html",
        context={
            "account": account,
            "snapshots": snapshots,
            "attached_sources": attached_sources,
        },
    )


@router.post("/accounts/{account_id}/backup/restore/{snapshot_id}")
def account_backup_restore(
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


@router.post("/accounts/{account_id}/backup/attachments/{attachment_id}/restore/{snapshot_id}")
def account_attachment_restore(
    account_id: str,
    attachment_id: str,
    snapshot_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Restore a snapshot from an attached repository prefix into a Recovery.

    Same flow as account_backup_restore, but the repository and restic prefix
    come from the RepositoryAttachment instead of the account's BackupPolicy —
    works even when the account has no backup policy of its own.
    """
    from mailfallback.services.recovery_service import create_recovery

    user = _get_session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    account = get_account(db, account_id, user)
    if not account:
        return RedirectResponse("/", status_code=303)

    att = (
        db.query(RepositoryAttachment)
        .filter(
            RepositoryAttachment.id == attachment_id,
            RepositoryAttachment.account_id == account_id,
        )
        .first()
    )
    if not att:
        request.session["flash_error"] = "Attachment not found"
        return RedirectResponse(f"/accounts/{account_id}", status_code=303)

    try:
        recovery = create_recovery(
            db,
            account.id,
            snapshot_id,
            source_repository=att.repository,
            source_prefix=att.prefix,
            source_password_enc=att.restic_password,
        )
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
        details={
            "snapshot_id": snapshot_id,
            "recovery_id": recovery.id,
            "attachment_id": att.id,
            "prefix": att.prefix,
        },
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
def account_recovery_delete(
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
