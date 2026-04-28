# src/mailfallback/routers/accounts.py
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from mailfallback.dependencies import get_current_user, get_db, require_admin
from mailfallback.models import User
from mailfallback.services import account_service

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


class AccountCreate(BaseModel):
    name: str
    imap_host: str
    imap_port: int = 993
    auth_type: str = "app_password"
    credentials: str | None = None
    maildir_path: str
    sync_schedule: str = "0 */6 * * *"


class AccountUpdate(BaseModel):
    name: str | None = None
    imap_host: str | None = None
    imap_port: int | None = None
    sync_schedule: str | None = None
    credentials: str | None = None
    enabled: bool | None = None


class OwnerAssign(BaseModel):
    user_id: str


@router.post("")
def create(body: AccountCreate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    account = account_service.create_account(db, **body.model_dump())
    return {"id": account.id, "name": account.name}


@router.get("")
def list_accounts(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    accounts = account_service.get_accounts_for_user(db, user)
    return [
        {
            "id": a.id,
            "name": a.name,
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
    return {
        "id": account.id,
        "name": account.name,
        "imap_host": account.imap_host,
        "imap_port": account.imap_port,
        "auth_type": account.auth_type.value,
        "maildir_path": account.maildir_path,
        "sync_schedule": account.sync_schedule,
        "sync_state": account.sync_state.value,
        "last_sync_at": account.last_sync_at.isoformat() if account.last_sync_at else None,
        "last_error": account.last_error,
        "enabled": account.enabled,
        "owners": [{"id": o.id, "username": o.username} for o in account.owners],
    }


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
def delete(account_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    if not account_service.delete_account(db, account_id):
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
        raise HTTPException(status_code=404, detail=str(e))
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
