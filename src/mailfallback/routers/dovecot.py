# src/mailfallback/routers/dovecot.py
"""Internal API for Dovecot Lua userdb lookups."""

import hmac
import re

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.dependencies import get_db
from mailfallback.models import (
    Account,
    Recovery,
    RecoveryStatus,
    User,
    account_groups,
    account_owners,
    group_members,
)

router = APIRouter(prefix="/api/internal/dovecot", tags=["dovecot-internal"])


def _verify_api_key(x_api_key: str | None = Header(default=None)):
    if not x_api_key or not settings.dovecot_api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    if not hmac.compare_digest(x_api_key, settings.dovecot_api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")


@router.get("/userdb/{username}", dependencies=[Depends(_verify_api_key)])
def userdb_lookup(username: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == username).first()
    if not user or not user.enabled or user.migrating:
        raise HTTPException(status_code=404, detail="User not found")

    store_path = user.store.path.rstrip("/")
    safe_username = re.sub(r"[^a-zA-Z0-9@._-]", "_", username)
    home = f"{store_path}/.dovecot-home/{safe_username}"

    # Get user's enabled accounts on enabled stores, ordered by created_at
    owned = (
        db.query(Account)
        .join(account_owners, Account.id == account_owners.c.account_id)
        .filter(account_owners.c.user_id == user.id)
    )
    via_groups = (
        db.query(Account)
        .join(account_groups, Account.id == account_groups.c.account_id)
        .join(group_members, account_groups.c.group_id == group_members.c.group_id)
        .filter(group_members.c.user_id == user.id)
    )
    all_accounts = owned.union(via_groups).order_by(Account.created_at.asc()).all()
    accounts = [a for a in all_accounts if a.enabled and not a.suspended and a.store.enabled]

    namespaces = []
    for i, account in enumerate(accounts):
        is_inbox = i == 0
        short_id = account.id[-4:]
        prefix = f"{account.name} ({account.email_address}) [{short_id}]/"

        namespaces.append(
            {
                "name": f"acc_{account.id}",
                "prefix": prefix,
                "mail_driver": "maildir",
                "mail_path": account.maildir_path,
                "inbox": is_inbox,
            }
        )

    # Recoveries: each ready recovery becomes an additional read-only
    # namespace under one of the user's accounts. They're not synced and
    # not browsable as accounts; they appear as a folder in the user's
    # webmail under the source account's prefix.
    if accounts:
        account_ids = [a.id for a in accounts]
        recoveries = (
            db.query(Recovery)
            .filter(Recovery.account_id.in_(account_ids))
            .filter(Recovery.status == RecoveryStatus.ready)
            .order_by(Recovery.restored_at.desc())
            .all()
        )
        accounts_by_id = {a.id: a for a in accounts}
        for rec in recoveries:
            src = accounts_by_id.get(rec.account_id)
            if not src:
                continue
            short = (rec.snapshot_id or rec.id)[:8]
            label = src.name
            ts = rec.restored_at.strftime("%Y-%m-%d") if rec.restored_at else "snapshot"
            prefix = f"Recovery - {label} ({ts}) [{short}]/"
            namespaces.append(
                {
                    "name": f"rec_{rec.id}",
                    "prefix": prefix,
                    "mail_driver": "maildir",
                    "mail_path": rec.restore_path,
                    "inbox": False,
                }
            )

    return {
        "uid": 1000,
        "gid": 1000,
        "home": home,
        "namespaces": namespaces,
    }
