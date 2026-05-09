import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from mailfallback.models import MailStore, User, account_owners
from mailfallback.security import hash_password

logger = logging.getLogger(__name__)

TEMP_USER_PREFIX = "_restore_"


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
    db.delete(user)
    db.commit()


def cleanup_temp_imap_users(db: Session) -> int:
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    temp_users = (
        db.query(User)
        .filter(User.username.like(f"{TEMP_USER_PREFIX}%"), User.created_at < cutoff)
        .all()
    )
    if not temp_users:
        return 0
    for user in temp_users:
        db.execute(account_owners.delete().where(account_owners.c.user_id == user.id))
        db.delete(user)
    db.commit()
    logger.info("Cleaned up %d orphaned restore users", len(temp_users))
    return len(temp_users)
