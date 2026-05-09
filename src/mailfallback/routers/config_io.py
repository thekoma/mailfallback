# src/mailfallback/routers/config_io.py
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from mailfallback.dependencies import get_db, require_admin
from mailfallback.models import Account, AuthType, MailStore, User
from mailfallback.services.account_service import assign_owner
from mailfallback.services.audit_service import log_action
from mailfallback.services.imap_check import validate_host_not_internal
from mailfallback.services.store_service import derive_maildir_path

router = APIRouter(prefix="/api/config", tags=["config"])


class AccountExport(BaseModel):
    name: str
    email_address: str = ""
    imap_host: str
    imap_port: int
    auth_type: str
    maildir_path: str
    sync_schedule: str | None
    store_id: str | None = None


class ConfigImport(BaseModel):
    accounts: list[AccountExport]


@router.get("/export")
def export_config(
    request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)
):
    log_action(
        db,
        user=admin,
        action="config.export",
        resource_type="config",
        ip_address=request.client.host if request.client else None,
    )
    accounts = db.query(Account).all()
    return {
        "accounts": [
            {
                "name": a.name,
                "email_address": a.email_address,
                "imap_host": a.imap_host,
                "imap_port": a.imap_port,
                "auth_type": a.auth_type.value,
                "maildir_path": a.maildir_path,
                "sync_schedule": a.sync_schedule,
            }
            for a in accounts
        ]
    }


@router.post("/import")
def import_config(
    body: ConfigImport,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    count = 0
    errors = []
    for idx, acc_data in enumerate(body.accounts):
        try:
            try:
                validate_host_not_internal(acc_data.imap_host)
            except ValueError as e:
                errors.append({"index": idx, "name": acc_data.name, "error": str(e)})
                continue
            valid_auth_types = {t.value for t in AuthType}
            if acc_data.auth_type not in valid_auth_types:
                errors.append({"index": idx, "name": acc_data.name, "error": "Invalid auth_type"})
                continue
            if not (1 <= acc_data.imap_port <= 65535):
                errors.append({"index": idx, "name": acc_data.name, "error": "Invalid imap_port"})
                continue
            store_id = acc_data.store_id or admin.store_id
            store = db.query(MailStore).filter(MailStore.id == store_id).first()
            if not store:
                errors.append({"index": idx, "name": acc_data.name, "error": "Store not found"})
                continue
            sp = db.begin_nested()
            account = Account(
                name=acc_data.name,
                email_address=acc_data.email_address,
                imap_host=acc_data.imap_host,
                imap_port=acc_data.imap_port,
                auth_type=acc_data.auth_type,
                maildir_path="pending",
                sync_schedule=acc_data.sync_schedule,
                store_id=store_id,
            )
            db.add(account)
            db.flush()
            account.maildir_path = derive_maildir_path(store.path, account.id)
            sp.commit()
            assign_owner(db, account.id, admin.id)
            count += 1
        except Exception as e:
            db.rollback()
            errors.append({"index": idx, "name": acc_data.name, "error": str(e)})
    db.commit()
    log_action(
        db,
        user=admin,
        action="config.import",
        resource_type="config",
        ip_address=request.client.host if request.client else None,
        details={"count": count, "errors": errors},
    )
    return {"imported": count, "errors": errors}
