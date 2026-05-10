# src/mailfallback/services/account_service.py
import uuid

from sqlalchemy.orm import Session, selectinload

from mailfallback.config import settings
from mailfallback.models import Account, MailStore, User, UserRole, account_groups, group_members
from mailfallback.security import decrypt_credentials, encrypt_credentials
from mailfallback.services.scheduler import refresh_scheduler
from mailfallback.services.store_service import derive_maildir_path


def create_account(
    db: Session,
    name: str,
    imap_host: str,
    imap_port: int,
    auth_type: str,
    store: MailStore,
    credentials: str | None = None,
    sync_schedule: str = "*/10 * * * *",
    email_address: str = "",
    provider: str = "other",
) -> Account:
    encrypted_creds = None
    if credentials:
        encrypted_creds = encrypt_credentials(credentials, settings.secret_key)
    account_id = str(uuid.uuid4())
    account = Account(
        id=account_id,
        name=name,
        email_address=email_address,
        provider=provider,
        imap_host=imap_host,
        imap_port=imap_port,
        auth_type=auth_type,
        credentials=encrypted_creds,
        store_id=store.id,
        maildir_path=derive_maildir_path(store.path, account_id),
        sync_schedule=sync_schedule,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    refresh_scheduler()
    return account


def assign_owner(db: Session, account_id: str, user_id: str) -> None:
    account = db.query(Account).filter(Account.id == account_id).first()
    user = db.query(User).filter(User.id == user_id).first()
    if not account or not user:
        raise ValueError("Account or user not found")
    if user not in account.owners:
        account.owners.append(user)
        db.commit()


def remove_owner(db: Session, account_id: str, user_id: str) -> None:
    account = db.query(Account).filter(Account.id == account_id).first()
    user = db.query(User).filter(User.id == user_id).first()
    if account and user and user in account.owners:
        account.owners.remove(user)
        db.commit()


def get_accounts_for_user(db: Session, user: User) -> list[Account]:
    # Eager-load backup_policies + recoveries so the /accounts list page can
    # render the Repository pill and nested recovery rows without N+1.
    eager = (selectinload(Account.backup_policies), selectinload(Account.recoveries))
    if user.role == UserRole.admin:
        return db.query(Account).options(*eager).all()
    owned = {a.id for a in user.accounts}
    via_groups = (
        db.query(Account.id)
        .join(account_groups, Account.id == account_groups.c.account_id)
        .join(group_members, account_groups.c.group_id == group_members.c.group_id)
        .filter(group_members.c.user_id == user.id)
        .all()
    )
    group_ids = {row[0] for row in via_groups}
    all_ids = owned | group_ids
    if not all_ids:
        return []
    return db.query(Account).options(*eager).filter(Account.id.in_(all_ids)).all()


def get_account(db: Session, account_id: str, user: User) -> Account | None:
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        return None
    if user.role == UserRole.admin:
        return account
    if user in account.owners:
        return account
    via_group = (
        db.query(account_groups.c.account_id)
        .join(group_members, account_groups.c.group_id == group_members.c.group_id)
        .filter(
            account_groups.c.account_id == account_id,
            group_members.c.user_id == user.id,
        )
        .first()
    )
    if via_group:
        return account
    return None


def get_account_for_modify(db: Session, account_id: str, user: User) -> Account | None:
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        return None
    if user.role == UserRole.admin:
        return account
    if user in account.owners:
        return account
    return None


def is_account_owner(user: User, account: Account) -> bool:
    return user in account.owners


_UPDATABLE_ACCOUNT_FIELDS = {
    "name",
    "email_address",
    "imap_host",
    "imap_port",
    "sync_schedule",
    "credentials",
    "provider",
    "tls_type",
    "extra_config",
    "enabled",
    "suspended",
    "imap_user",
}


def update_account(db: Session, account_id: str, user: User, **kwargs) -> Account | None:
    account = get_account_for_modify(db, account_id, user)
    if not account:
        return None
    if "credentials" in kwargs and kwargs["credentials"] is not None:
        kwargs["credentials"] = encrypt_credentials(kwargs["credentials"], settings.secret_key)
    for key, value in kwargs.items():
        if key in _UPDATABLE_ACCOUNT_FIELDS:
            setattr(account, key, value)
    db.commit()
    db.refresh(account)
    refresh_scheduler()
    return account


def delete_account(db: Session, account_id: str, delete_files: bool = False) -> bool:
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        return False
    maildir_path = account.maildir_path if delete_files else None
    db.delete(account)
    db.commit()
    refresh_scheduler()
    if maildir_path:
        import shutil

        shutil.rmtree(maildir_path, ignore_errors=True)
    return True


def get_account_credentials(db: Session, account_id: str) -> str | None:
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account or not account.credentials:
        return None
    return decrypt_credentials(account.credentials, settings.secret_key)
