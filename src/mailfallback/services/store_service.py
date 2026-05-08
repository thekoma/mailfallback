import contextlib
import os
import re

from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.models import Account, MailStore, User


def sanitize_email(email: str) -> str:
    return re.sub(r"[^a-z0-9]", "_", email.lower().strip())


def get_default_store(db: Session) -> MailStore | None:
    """Return the store marked as default, or None."""
    return db.query(MailStore).filter(MailStore.is_default.is_(True)).first()


def set_default_store(db: Session, store_id: str) -> MailStore | None:
    """Mark a store as default, clearing the flag on all others."""
    db.query(MailStore).filter(MailStore.is_default.is_(True)).update({"is_default": False})
    store = db.query(MailStore).filter(MailStore.id == store_id).first()
    if not store:
        return None
    store.is_default = True
    db.commit()
    db.refresh(store)
    return store


def ensure_default_store(db: Session) -> MailStore:
    """Return the default store, creating one on first boot if none exist."""
    store = get_default_store(db)
    if store:
        return store
    store = MailStore(
        name="default",
        path=settings.bootstrap_store_path.rstrip("/"),
        is_default=True,
    )
    db.add(store)
    db.commit()
    db.refresh(store)
    return store


def derive_maildir_path(store_path: str, account_id: str) -> str:
    return f"{store_path.rstrip('/')}/{account_id}"


def create_store(db: Session, name: str, path: str) -> MailStore:
    clean_path = path.rstrip("/")
    with contextlib.suppress(OSError):
        os.makedirs(clean_path, exist_ok=True)
    store = MailStore(name=name, path=clean_path)
    db.add(store)
    db.commit()
    db.refresh(store)
    return store


def list_stores(db: Session) -> list[MailStore]:
    return db.query(MailStore).all()


def get_store(db: Session, store_id: str) -> MailStore | None:
    return db.query(MailStore).filter(MailStore.id == store_id).first()


_UPDATABLE_STORE_FIELDS = {"name", "path", "enabled", "is_default"}


def update_store(db: Session, store_id: str, **kwargs) -> MailStore | None:
    store = db.query(MailStore).filter(MailStore.id == store_id).first()
    if not store:
        return None
    for key, value in kwargs.items():
        if key in _UPDATABLE_STORE_FIELDS:
            setattr(store, key, value)
    db.commit()
    db.refresh(store)
    return store


def get_store_contents(db: Session, store_id: str) -> dict:
    """List all accounts and users whose data lives on this store."""
    accounts_on_store = db.query(Account).filter(Account.store_id == store_id).all()
    users_on_store = db.query(User).filter(User.store_id == store_id).all()
    return {
        "accounts": [
            {"id": a.id, "name": a.name, "email_address": a.email_address}
            for a in accounts_on_store
        ],
        "users": [{"id": u.id, "username": u.username} for u in users_on_store],
    }


def delete_store(db: Session, store_id: str) -> tuple[bool, str | None]:
    store = db.query(MailStore).filter(MailStore.id == store_id).first()
    if not store:
        return False, "Store not found"
    contents = get_store_contents(db, store_id)
    if contents["accounts"] or contents["users"]:
        return False, "Store is not empty — migrate all accounts and user homes before deleting"
    db.delete(store)
    db.commit()
    return True, None


def get_user_store(db: Session, user: User) -> MailStore | None:
    return db.query(MailStore).filter(MailStore.id == user.store_id).first()


def get_allowed_stores(db: Session, user: User) -> list[MailStore]:
    if user.role.value == "admin":
        return db.query(MailStore).filter(MailStore.enabled.is_(True)).all()
    return [s for s in user.allowed_stores if s.enabled]


def set_allowed_stores(db: Session, user_id: str, store_ids: list[str]) -> str | None:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return None
    if user.migrating:
        return "Cannot change stores while migration is in progress"
    stores = db.query(MailStore).filter(MailStore.id.in_(store_ids)).all()
    new_ids = {s.id for s in stores}
    if user.store_id not in new_ids and stores:
        return "Cannot remove the user's current home store — migrate the user first"
    user.allowed_stores = stores
    db.commit()
    return None


def get_selectable_stores(db: Session, user: User) -> list[MailStore] | None:
    stores = get_allowed_stores(db, user)
    return stores if len(stores) > 1 else None


def is_path_available(db: Session, candidate: str) -> tuple[bool, str | None]:
    """Check if a maildir path is available. Returns (ok, conflict_reason)."""
    existing = db.query(Account.maildir_path).all()
    candidate_norm = candidate.rstrip("/") + "/"

    for (path,) in existing:
        path_norm = path.rstrip("/") + "/"
        if candidate_norm == path_norm:
            return False, f"Path already in use: {path}"
        if candidate_norm.startswith(path_norm):
            return False, f"Path is a subdirectory of existing: {path}"
        if path_norm.startswith(candidate_norm):
            return False, f"Path is a parent of existing: {path}"

    return True, None


def get_orphaned_dirs(db: Session, store_id: str) -> list[dict]:
    """Find directories on disk that don't match any account in the DB."""
    store = db.query(MailStore).filter(MailStore.id == store_id).first()
    if not store or not os.path.isdir(store.path):
        return []

    account_ids = {a.id for a in db.query(Account.id).filter(Account.store_id == store_id).all()}

    orphans = []
    for entry in os.scandir(store.path):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if entry.name not in account_ids:
            size = (
                sum(f.stat().st_size for f in os.scandir(entry.path) if f.is_file())
                if os.path.isdir(entry.path)
                else 0
            )
            orphans.append({"name": entry.name, "path": entry.path, "size": size})
    return orphans


def delete_orphaned_dirs(db: Session, store_id: str) -> int:
    """Remove all orphaned directories from a store. Returns count deleted."""
    import shutil

    orphans = get_orphaned_dirs(db, store_id)
    for orphan in orphans:
        shutil.rmtree(orphan["path"], ignore_errors=True)
    return len(orphans)


def check_maildir_exists(path: str) -> tuple[bool, int]:
    """Check if a maildir path exists on disk and count files. Returns (exists, file_count)."""
    import os

    path = path.rstrip("/")
    if not os.path.isdir(path):
        return False, 0
    count = sum(1 for entry in os.scandir(path) if entry.is_file() or entry.is_dir())
    return True, count
