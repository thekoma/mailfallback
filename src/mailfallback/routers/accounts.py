# src/mailfallback/routers/accounts.py
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from mailfallback.dependencies import get_current_user, get_db, require_admin
from mailfallback.models import Account, User, UserRole
from mailfallback.services import account_service
from mailfallback.services.account_service import is_account_owner
from mailfallback.services.imap_check import check_imap_credentials
from mailfallback.services.provider_discovery import discover_provider

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


class AccountCreate(BaseModel):
    name: str
    email_address: str = ""
    provider: str = "other"
    imap_host: str
    imap_port: int = 993
    tls_type: str = "IMAPS"
    auth_type: str = "app_password"
    credentials: str | None = None
    sync_schedule: str = "0 * * * *"
    store_id: str | None = None


class AccountUpdate(BaseModel):
    name: str | None = None
    email_address: str | None = None
    provider: str | None = None
    imap_host: str | None = None
    imap_port: int | None = None
    sync_schedule: str | None = None
    credentials: str | None = None
    enabled: bool | None = None
    suspended: bool | None = None


class OwnerAssign(BaseModel):
    user_id: str


@router.post("")
def create(
    body: AccountCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from mailfallback.services.store_service import get_store

    if body.store_id:
        store = get_store(db, body.store_id)
        if not store:
            raise HTTPException(status_code=404, detail="Store not found")
    else:
        store = user.store
        if not store:
            raise HTTPException(status_code=400, detail="No store assigned")

    if body.auth_type == "app_password" and body.credentials:
        result = check_imap_credentials(
            host=body.imap_host,
            port=body.imap_port,
            username=body.email_address,
            password=body.credentials,
        )
        if not result["ok"]:
            raise HTTPException(status_code=422, detail=f"Connection failed: {result['message']}")
        if result.get("login_ok") is False:
            raise HTTPException(status_code=422, detail=f"Login failed: {result['login_message']}")

    account = account_service.create_account(
        db,
        name=body.name,
        email_address=body.email_address,
        provider=body.provider,
        imap_host=body.imap_host,
        imap_port=body.imap_port,
        auth_type=body.auth_type,
        credentials=body.credentials,
        sync_schedule=body.sync_schedule,
        store=store,
    )
    account.tls_type = body.tls_type
    account.imap_user = body.email_address
    domain = (body.email_address.split("@")[1:] or [""])[0]
    if domain:
        disc = discover_provider(domain)
        if disc and disc.get("patterns"):
            account.extra_config = json.dumps({"patterns": disc["patterns"]})
    db.commit()
    account_service.assign_owner(db, account.id, user.id)
    return {"id": account.id, "name": account.name, "maildir_path": account.maildir_path}


@router.get("")
def list_accounts(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    accounts = account_service.get_accounts_for_user(db, user)
    return [
        {
            "id": a.id,
            "name": a.name,
            "provider": a.provider,
            "imap_host": a.imap_host,
            "sync_state": a.sync_state.value,
            "last_sync_at": a.last_sync_at.isoformat() if a.last_sync_at else None,
            "enabled": a.enabled,
        }
        for a in accounts
    ]


@router.get("/{account_id}")
def get(account_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    account = account_service.get_account(db, account_id, user)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    result = {
        "id": account.id,
        "name": account.name,
        "email_address": account.email_address,
        "provider": account.provider,
        "imap_host": account.imap_host,
        "imap_port": account.imap_port,
        "auth_type": account.auth_type.value,
        "sync_schedule": account.sync_schedule,
        "sync_state": account.sync_state.value,
        "last_sync_at": account.last_sync_at.isoformat() if account.last_sync_at else None,
        "last_error": account.last_error,
        "enabled": account.enabled,
        "owners": [{"id": o.id, "username": o.username} for o in account.owners],
    }
    if user.role.value == "admin":
        result["maildir_path"] = account.maildir_path
    return result


@router.patch("/{account_id}")
def update(
    account_id: str,
    body: AccountUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    updates = body.model_dump(exclude_unset=True)
    account = account_service.update_account(db, account_id, user, **updates)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return {"id": account.id, "name": account.name}


@router.delete("/{account_id}")
def delete(
    account_id: str,
    delete_files: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if user.role != UserRole.admin and not is_account_owner(user, account):
        raise HTTPException(status_code=403, detail="Only owners can delete accounts")
    if not account_service.delete_account(db, account_id, delete_files=delete_files):
        raise HTTPException(status_code=404, detail="Account not found")
    return {"ok": True}


@router.post("/{account_id}/owners")
def assign_owner(
    account_id: str,
    body: OwnerAssign,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    try:
        account_service.assign_owner(db, account_id, body.user_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    return {"ok": True}


@router.delete("/{account_id}/owners/{user_id}")
def remove_owner(
    account_id: str,
    user_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    account_service.remove_owner(db, account_id, user_id)
    return {"ok": True}
