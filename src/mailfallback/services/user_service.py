# src/mailfallback/services/user_service.py
import logging
import os
import re
import socket
import time
from email.utils import formatdate

from sqlalchemy.orm import Session

from mailfallback.models import Repository, User, UserRole
from mailfallback.security import hash_password, verify_password

logger = logging.getLogger(__name__)


MIN_PASSWORD_LENGTH = 8


def create_user(
    db: Session,
    username: str,
    password: str,
    role: UserRole = UserRole.user,
    *,
    store_id: str,
) -> User:
    user = User(
        username=username, password_hash=hash_password(password), role=role, store_id=store_id
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    try:
        create_welcome_email(user.store.path, username)
    except Exception:
        logger.warning("Failed to create welcome email for %s", username, exc_info=True)
    return user


def authenticate_user(db: Session, username: str, password: str) -> User | None:
    user = db.query(User).filter(User.username == username).first()
    if not user or not user.password_hash:
        return None
    if not user.enabled:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def get_user_by_id(db: Session, user_id: str) -> User | None:
    return db.query(User).filter(User.id == user_id).first()


def list_users(db: Session) -> list[User]:
    return db.query(User).all()


def change_password(db: Session, user_id: str, new_password: str) -> bool:
    if len(new_password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return False
    user.password_hash = hash_password(new_password)
    db.commit()
    return True


_UPDATABLE_USER_FIELDS = {"username", "role", "enabled", "store_id"}


def update_user(db: Session, user_id: str, **kwargs) -> User | None:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return None
    if "store_id" in kwargs and user.migrating:
        kwargs.pop("store_id")
    for key, value in kwargs.items():
        if key in _UPDATABLE_USER_FIELDS:
            setattr(user, key, value)
    db.commit()
    db.refresh(user)
    return user


def delete_user(db: Session, user_id: str) -> bool:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return False
    db.delete(user)
    db.commit()
    return True


def set_allowed_repositories(db: Session, user_id: str, repository_ids: list[str]) -> str | None:
    """Replace the user's allowed-repositories set. Returns an error string or None."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return "User not found"
    repositories = db.query(Repository).filter(Repository.id.in_(repository_ids)).all()
    user.allowed_repositories = repositories
    db.commit()
    return None


def ensure_admin_exists(db: Session, default_store_id: str) -> None:
    admin = db.query(User).filter(User.role == UserRole.admin).first()
    if not admin:
        create_user(
            db,
            username="admin",
            password="changeme1234!",  # pragma: allowlist secret
            role=UserRole.admin,
            store_id=default_store_id,
        )


def _sanitize_path_component(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9@._-]", "_", name)


def create_welcome_email(store_path: str, username: str) -> None:
    safe_username = _sanitize_path_component(username)
    inbox_new = os.path.join(store_path, ".dovecot-home", safe_username, "root-inbox", "new")
    os.makedirs(inbox_new, exist_ok=True)

    # Also create cur/ and tmp/ for valid Maildir
    for sub in ("cur", "tmp"):
        os.makedirs(
            os.path.join(store_path, ".dovecot-home", safe_username, "root-inbox", sub),
            exist_ok=True,
        )

    timestamp = int(time.time())
    hostname = socket.gethostname()
    filename = f"{timestamp}.welcome.{hostname}:2,"

    if os.path.exists(os.path.join(inbox_new, filename)):
        return

    msg = f"""\
From: MailFallBack <noreply@mailfallback.local>
To: {username}@mailfallback.local
Subject: Welcome to MailFallBack
Date: {formatdate(localtime=True)}
MIME-Version: 1.0
Content-Type: text/html; charset=utf-8
Message-ID: <welcome-{username}-{timestamp}@mailfallback.local>

<!DOCTYPE html>
<html>
<body style="font-family: -apple-system, sans-serif; max-width: 600px;
 margin: 0 auto; padding: 20px; color: #333;">
<h2>Welcome to MailFallBack</h2>

<p>This is your <strong>email backup inbox</strong>. Here's how it works:</p>

<h3>What you see here</h3>
<p>Each email account you add to MailFallBack appears as a separate folder group
in the sidebar. For example:</p>
<ul>
<li><strong>Work (you@company.com)/</strong> &mdash; your work email backup</li>
<li><strong>Personal (you@gmail.com)/</strong> &mdash; your personal email backup</li>
</ul>

<h3>Read-only access</h3>
<p>This is a <strong>backup viewer</strong>. You can read your emails but you cannot
delete, move, or send messages. Your original mailboxes are not affected.</p>

<h3>How syncing works</h3>
<p>MailFallBack periodically downloads new emails from your IMAP servers.
You can configure sync schedules and trigger manual syncs from the
<a href="/">MailFallBack dashboard</a>.</p>

<h3>This inbox</h3>
<p>This root inbox is intentionally empty &mdash; it exists only to anchor the
IMAP folder structure. Your actual emails are in the account folders below.</p>

<p style="color: #888; font-size: 0.9em; margin-top: 30px;">
&mdash; MailFallBack
</p>
</body>
</html>
"""

    fd = os.open(os.path.join(inbox_new, filename), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(msg)
    logger.info("Welcome email created for %s", username)
