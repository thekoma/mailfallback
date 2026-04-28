# src/mailfallback/services/account_service.py
from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.models import Account, User, UserRole
from mailfallback.security import decrypt_credentials, encrypt_credentials


def create_account(
    db: Session,
    name: str,
    imap_host: str,
    imap_port: int,
    auth_type: str,
    maildir_path: str,
    credentials: str | None = None,
    sync_schedule: str = "0 */6 * * *",
) -> Account:
    encrypted_creds = None
    if credentials:
        encrypted_creds = encrypt_credentials(credentials, settings.secret_key)
    account = Account(
        name=name,
        imap_host=imap_host,
        imap_port=imap_port,
        auth_type=auth_type,
        credentials=encrypted_creds,
        maildir_path=maildir_path,
        sync_schedule=sync_schedule,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
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
    if user.role == UserRole.admin:
        return db.query(Account).all()
    return user.accounts


def get_account(db: Session, account_id: str, user: User) -> Account | None:
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        return None
    if user.role != UserRole.admin and user not in account.owners:
        return None
    return account


def update_account(db: Session, account_id: str, user: User, **kwargs) -> Account | None:
    account = get_account(db, account_id, user)
    if not account:
        return None
    if "credentials" in kwargs and kwargs["credentials"] is not None:
        kwargs["credentials"] = encrypt_credentials(kwargs["credentials"], settings.secret_key)
    for key, value in kwargs.items():
        setattr(account, key, value)
    db.commit()
    db.refresh(account)
    return account


def delete_account(db: Session, account_id: str) -> bool:
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        return False
    db.delete(account)
    db.commit()
    return True


def get_account_credentials(db: Session, account_id: str) -> str | None:
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account or not account.credentials:
        return None
    return decrypt_credentials(account.credentials, settings.secret_key)
