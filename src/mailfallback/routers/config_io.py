# src/mailfallback/routers/config_io.py
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from mailfallback.dependencies import get_db, require_admin
from mailfallback.models import Account, User

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
def export_config(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
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
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    count = 0
    for acc_data in body.accounts:
        store_id = acc_data.store_id or admin.store_id
        account = Account(
            name=acc_data.name,
            email_address=acc_data.email_address,
            imap_host=acc_data.imap_host,
            imap_port=acc_data.imap_port,
            auth_type=acc_data.auth_type,
            maildir_path=acc_data.maildir_path,
            sync_schedule=acc_data.sync_schedule,
            store_id=store_id,
        )
        db.add(account)
        count += 1
    db.commit()
    return {"imported": count}
