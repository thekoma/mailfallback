import logging
import os
import secrets
import shutil
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from mailfallback.models import MailStore, User, account_owners
from mailfallback.security import hash_password
from mailfallback.services.store_service import (
    DOVECOT_HOME_DIRNAME,
    remove_dovecot_home,
    sanitize_path_component,
)

logger = logging.getLogger(__name__)

TEMP_USER_PREFIX = "_restore_"


def _temp_user_filter():
    """Match usernames literally starting with TEMP_USER_PREFIX.

    autoescape is not optional here: the prefix is "_restore_" and "_" is a
    single-character wildcard in SQL LIKE, so the bare pattern also selects
    real accounts like "arestore_x" — names create_user accepts, because its
    own guard is a literal Python startswith. Unescaped, the hourly cleanup
    deleted those users, and now it would delete their mail too.
    """
    return User.username.startswith(TEMP_USER_PREFIX, autoescape=True)


def create_temp_imap_user(db: Session, account_ids: list[str]) -> tuple[str, str]:
    username = f"{TEMP_USER_PREFIX}{uuid.uuid4().hex[:8]}"
    password = secrets.token_urlsafe(32)

    default_store = db.query(MailStore).filter(MailStore.is_default.is_(True)).first()
    if not default_store:
        default_store = db.query(MailStore).first()

    user = User(
        username=username,
        password_hash=hash_password(password),
        store_id=default_store.id,
        enabled=True,
    )
    db.add(user)
    db.flush()

    for acct_id in account_ids:
        db.execute(account_owners.insert().values(account_id=acct_id, user_id=user.id))
    db.commit()

    return username, password


def delete_temp_imap_user(db: Session, username: str) -> None:
    if not username.startswith(TEMP_USER_PREFIX):
        return
    user = db.query(User).filter(User.username == username).first()
    if not user:
        return
    db.execute(account_owners.delete().where(account_owners.c.user_id == user.id))
    remove_dovecot_home(db, user)
    db.delete(user)
    db.commit()


def cleanup_temp_imap_users(db: Session) -> int:
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    temp_users = db.query(User).filter(_temp_user_filter(), User.created_at < cutoff).all()
    if not temp_users:
        return 0
    for user in temp_users:
        db.execute(account_owners.delete().where(account_owners.c.user_id == user.id))
        remove_dovecot_home(db, user)
        db.delete(user)
    db.commit()
    logger.info("Cleaned up %d orphaned restore users", len(temp_users))
    return len(temp_users)


def sweep_orphaned_restore_homes(db: Session) -> int:
    """Delete _restore_* Dovecot homes that no longer have a user row.

    Needed on top of the teardown fix: the homes already on disk predate it
    (74 of 78 in production when #240 was measured) and nothing else will ever
    collect them.

    Scoped hard to the _restore_ prefix. Sweeping every home without a matching
    row would be a far more useful-sounding rule and a far more dangerous one —
    a database restore still running, or a half-finished migration, presents
    exactly as "real user home, no row", and the directory would be gone before
    anyone noticed. MFB mints the _restore_ prefix itself and reserves it in
    create_user, so those directories are the only ones it can claim to own.
    """
    live = {
        sanitize_path_component(username)
        for (username,) in db.query(User.username).filter(_temp_user_filter())
    }

    removed = 0
    for store in db.query(MailStore).all():
        root = f"{store.path.rstrip('/')}/{DOVECOT_HOME_DIRNAME}"
        if not os.path.isdir(root):
            continue
        for entry in os.scandir(root):
            if not entry.is_dir() or not entry.name.startswith(TEMP_USER_PREFIX):
                continue
            if entry.name in live:
                continue
            shutil.rmtree(entry.path, ignore_errors=True)
            if not os.path.isdir(entry.path):
                removed += 1
    if removed:
        logger.info("Swept %d orphaned restore home directories", removed)
    return removed
