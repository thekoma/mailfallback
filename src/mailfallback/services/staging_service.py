"""Per-user staging area — copy-in, reconcile, quota, lifecycle.

The staging Maildir ({dovecot_home}/staging) is the source of truth for
contents: webmail deletions remove files and reconcile() drops their rows.
Rows carry origin (account + folder) for push-to-origin and the byte
accounting that backs the quota. One area per user; TTL from creation.

Orphan files (a file on disk with no StagingMessage row — e.g. the source
account was deleted and its rows CASCADEd away) are tolerated: reconcile()
only iterates rows, so it never crashes on them; they are swept together
with everything else by empty() and cleanup_expired() only.
"""

import logging
import os
import re
import shutil
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.models import (
    Account,
    MailIndexMessage,
    StagingArea,
    StagingMessage,
    User,
    UserRole,
)
from mailfallback.services.index_service import maildir_filename_prefix
from mailfallback.services.search_service import _accessible_account_ids

logger = logging.getLogger(__name__)


class StagingQuotaExceededError(Exception):
    pass


def _safe_username(username: str) -> str:
    return re.sub(r"[^a-zA-Z0-9@._-]", "_", username)


def staging_dir(user: User) -> str:
    """{store}/.dovecot-home/{username}/staging — same home the userdb serves.

    Single source of truth for the staging Maildir location: the Dovecot
    userdb endpoint publishes exactly this string as the namespace mail_path,
    so the construction must stay byte-identical to the home it serves
    (rstrip + sanitization included), or webmail silently shows an empty
    Staging/ folder.
    """
    store_path = user.store.path.rstrip("/")
    return f"{store_path}/.dovecot-home/{_safe_username(user.username)}/staging"


def _ensure_maildir(path: str) -> None:
    for sub in ("cur", "new", "tmp"):
        os.makedirs(os.path.join(path, sub), exist_ok=True)


def get_status(db: Session, user: User) -> dict:
    """Current staging state for the user; reconciles with disk first."""
    area = db.query(StagingArea).filter(StagingArea.user_id == user.id).first()
    if not area:
        return {
            "exists": False,
            "count": 0,
            "bytes_used": 0,
            "expires_at": None,
            "max_bytes": settings.staging_max_bytes,
        }
    reconcile(db, user, area)
    count = db.query(StagingMessage).filter(StagingMessage.staging_id == area.id).count()
    return {
        "exists": True,
        "count": count,
        "bytes_used": area.bytes_used,
        "expires_at": area.expires_at.isoformat(),
        "max_bytes": area.max_bytes,
    }


def _get_or_create_area(db: Session, user: User) -> StagingArea:
    area = db.query(StagingArea).filter(StagingArea.user_id == user.id).first()
    if area:
        return area
    area = StagingArea(
        user_id=user.id,
        expires_at=datetime.now(UTC) + timedelta(minutes=settings.staging_ttl_minutes),
        max_bytes=settings.staging_max_bytes,
    )
    db.add(area)
    db.flush()
    return area


def _message_bytes(db: Session, account: Account, row: MailIndexMessage) -> bytes | None:
    """Live file first (index locator, both INBOX bases, prefix fallback),
    else newest snapshot via restic dump — same strategy as preview_service."""
    from mailfallback.services.preview_service import _locate_live_file, _snapshot_bytes

    if row.deleted_at is None:
        path = _locate_live_file(account, row)
        if path:
            try:
                with open(path, "rb") as f:
                    return f.read()
            except OSError:
                pass
    found = _snapshot_bytes(db, account, row)
    return found[0] if found else None


def add_messages(
    db: Session,
    user: User,
    items: list[tuple[str, bytes]],
    include_all: bool = False,
) -> dict:
    """Copy messages into the user's staging Maildir. items = [(account_id, hash)].

    Quota is checked BEFORE any copy. Returns {staged, skipped, failed}.
    Idempotent per (account, hash): already-staged messages are skipped.
    include_all lets an ADMIN stage from accounts outside their own scope
    (the API layer audits those calls); non-admins always stay scoped.
    """
    visible = set(_accessible_account_ids(db, user))
    area = _get_or_create_area(db, user)
    sdir = staging_dir(user)
    _ensure_maildir(sdir)
    reconcile(db, user, area)

    existing = {
        (m.source_account_id, m.message_id_hash)
        for m in db.query(StagingMessage).filter(StagingMessage.staging_id == area.id)
    }

    to_stage: list[tuple[Account, MailIndexMessage, bytes]] = []
    failed = 0
    for account_id, h in items:
        if account_id not in visible and not (include_all and user.role == UserRole.admin):
            raise ValueError(f"Account {account_id} not accessible")
        if (account_id, h) in existing:
            continue
        account = db.query(Account).filter(Account.id == account_id).first()
        row = (
            db.query(MailIndexMessage)
            .filter(
                MailIndexMessage.account_id == account_id,
                MailIndexMessage.message_id_hash == h,
            )
            .first()
        )
        if not account or not row:
            failed += 1
            continue
        raw = _message_bytes(db, account, row)
        if raw is None:
            failed += 1
            continue
        to_stage.append((account, row, raw))

    incoming = sum(len(raw) for _, _, raw in to_stage)
    if area.max_bytes and area.bytes_used + incoming > area.max_bytes:
        raise StagingQuotaExceededError(
            f"Staging quota exceeded: {area.bytes_used + incoming} > {area.max_bytes} bytes"
        )

    staged = 0
    now = datetime.now(UTC)
    for account, row, raw in to_stage:
        fname = f"{int(now.timestamp())}.s{staged}.{row.message_id_hash.hex()[:12]}:2,"
        try:
            with open(os.path.join(sdir, "cur", fname), "wb") as f:
                f.write(raw)
        except OSError:
            logger.warning("Staging copy failed for %s", row.message_id_hash.hex(), exc_info=True)
            failed += 1
            continue
        db.add(
            StagingMessage(
                staging_id=area.id,
                source_account_id=account.id,
                message_id_hash=row.message_id_hash,
                original_folder=row.folder_path,
                staged_filename=fname,
                size_bytes=len(raw),
            )
        )
        area.bytes_used += len(raw)
        staged += 1
    db.commit()
    return {"staged": staged, "skipped": len(items) - staged - failed, "failed": failed}


def reconcile(db: Session, user: User, area: StagingArea) -> int:
    """Drop rows whose file vanished (webmail deletion); recompute bytes_used.
    Filenames are matched by stable prefix — Dovecot renames on flag changes."""
    sdir = staging_dir(user)
    on_disk: dict[str, str] = {}
    for sub in ("cur", "new"):
        d = os.path.join(sdir, sub)
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            on_disk[maildir_filename_prefix(fn)] = fn
    dropped = 0
    total = 0
    for m in db.query(StagingMessage).filter(StagingMessage.staging_id == area.id).all():
        actual = on_disk.get(maildir_filename_prefix(m.staged_filename))
        if actual is None:
            db.delete(m)
            dropped += 1
        else:
            if actual != m.staged_filename:
                m.staged_filename = actual
            total += m.size_bytes
    area.bytes_used = total
    db.commit()
    return dropped


def empty(db: Session, user: User) -> None:
    """Remove the staging Maildir and the area row (cascade removes rows)."""
    area = db.query(StagingArea).filter(StagingArea.user_id == user.id).first()
    sdir = staging_dir(user)
    if os.path.isdir(sdir):
        shutil.rmtree(sdir, ignore_errors=True)
    if area:
        db.delete(area)  # cascade removes rows
        db.commit()


def cleanup_expired(db: Session) -> int:
    """Scheduler entrypoint — purge expired areas (files + rows). Always on."""
    expired = db.query(StagingArea).filter(StagingArea.expires_at <= datetime.now(UTC)).all()
    for area in expired:
        user = db.query(User).filter(User.id == area.user_id).first()
        if user:
            sdir = staging_dir(user)
            if os.path.isdir(sdir):
                shutil.rmtree(sdir, ignore_errors=True)
        db.delete(area)
    db.commit()
    return len(expired)
