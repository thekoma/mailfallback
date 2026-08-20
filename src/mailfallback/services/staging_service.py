"""Per-user staging area — copy-in, reconcile, quota, lifecycle.

The staging Maildir ({dovecot_home}/staging) is the source of truth for
contents: webmail deletions remove files and reconcile() drops their rows.
Rows carry origin (account + folder) for push-to-origin and the byte
accounting that backs the quota. One area per user; the TTL runs from
creation BY DESIGN (no extend-on-activity — the workspace shows the expiry
up front and an add to an expired area starts a fresh one).

Orphan files (a file on disk with no StagingMessage row — e.g. the source
account was deleted and its rows CASCADEd away) are tolerated: reconcile()
only iterates rows, so it never crashes on them; they are swept together
with everything else by empty() and cleanup_expired() only.
"""

import contextlib
import logging
import os
import re
import shutil
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
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

# restic dump truncates SILENTLY at its max_bytes — fine for previews, fatal
# for staging (a truncated message would later be pushed upstream as "good").
# 100 MiB sits above any provider's message-size cap; a dump that FILLS this
# cap is presumed truncated and refused (counted failed), belt and braces.
STAGING_DUMP_MAX_BYTES = 104_857_600


class StagingQuotaExceededError(Exception):
    pass


def _safe_username(username: str) -> str:
    return re.sub(r"[^a-zA-Z0-9@._-]", "_", username)


def staging_dir(user: User) -> str:
    """{store}/.dovecot-home/{username}/root-inbox/Staging — the "Staging" mailbox.

    Single source of truth for the staging Maildir location. It sits INSIDE the
    mfb_root namespace's mail_path ({home}/root-inbox, set by the Lua userdb),
    so Dovecot lists it as the plain mailbox "Staging" with no namespace of its
    own. Two reasons it must live here:

    - Dovecot's ACL `mailbox` filters match the namespace-INTERNAL name, with
      the namespace prefix stripped. Behind a dedicated "Staging/" namespace the
      mailbox was seen by the ACL as "INBOX", so `mailbox Staging` never matched
      and the global read-only grant applied: flag changes and expunges were
      accepted on the wire and silently dropped.
    - With `mailbox_list_layout = fs` a namespace's mailboxes are subdirectories
      of its mail_path. A Maildir at the namespace root is not a listable
      mailbox at all, so staged messages had no IMAP mailbox to appear in.

    Keep the construction byte-identical to the home the userdb serves (rstrip
    and sanitisation included), or webmail silently shows an empty folder.
    """
    store_path = user.store.path.rstrip("/")
    return f"{store_path}/.dovecot-home/{_safe_username(user.username)}/root-inbox/Staging"


def _legacy_staging_dir(user: User) -> str:
    """Pre-move location: {home}/staging, its own "Staging/" Dovecot namespace.

    Areas created before the move left a Maildir here. Nothing reads it; it is
    purged alongside the current one so the move leaves no orphans behind.
    """
    store_path = user.store.path.rstrip("/")
    return f"{store_path}/.dovecot-home/{_safe_username(user.username)}/staging"


def _ensure_maildir(path: str) -> None:
    for sub in ("cur", "new", "tmp"):
        os.makedirs(os.path.join(path, sub), exist_ok=True)


def _is_expired(area: StagingArea) -> bool:
    exp = area.expires_at
    if exp.tzinfo is None:  # SQLite round-trips naive datetimes; values are UTC
        exp = exp.replace(tzinfo=UTC)
    return exp <= datetime.now(UTC)


def _remove_staging_dir(user: User) -> None:
    for sdir in (staging_dir(user), _legacy_staging_dir(user)):
        if os.path.isdir(sdir):
            shutil.rmtree(sdir, ignore_errors=True)
            if os.path.isdir(sdir):
                logger.warning("Staging dir %s not fully removed; leftovers remain on disk", sdir)


def get_status(db: Session, user: User) -> dict:
    """Current staging state for the user; reconciles with disk first.

    An expired-but-unswept area reports exists=False and is left untouched
    (no reconcile on corpses) — the cleanup job or the next add replaces it.
    """
    area = db.query(StagingArea).filter(StagingArea.user_id == user.id).first()
    if not area or _is_expired(area):
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
    """Return the user's live area, replacing an expired one (fresh TTL).

    Callers must keep the transaction free of unrelated pending changes:
    the unique(user_id) race recovery below rolls the session back.
    """
    area = db.query(StagingArea).filter(StagingArea.user_id == user.id).first()
    if area is not None:
        if not _is_expired(area):
            return area
        # Expired but not yet swept by the scheduler: files and rows belong
        # to the dead area — clear both and start over with a fresh TTL.
        _remove_staging_dir(user)
        db.delete(area)
        db.flush()
    area = StagingArea(
        user_id=user.id,
        expires_at=datetime.now(UTC) + timedelta(minutes=settings.staging_ttl_minutes),
        max_bytes=settings.staging_max_bytes,
    )
    db.add(area)
    try:
        db.flush()
    except IntegrityError:
        # Two concurrent first-adds raced on unique(user_id) — the other
        # request won; adopt its area.
        db.rollback()
        area = db.query(StagingArea).filter(StagingArea.user_id == user.id).first()
    return area


def _message_bytes(db: Session, account: Account, row: MailIndexMessage) -> bytes | None:
    """Live file first (index locator, both INBOX bases, prefix fallback),
    else newest snapshot via restic dump — same strategy as preview_service,
    but uncapped on live reads and with the staging cap on dumps: a cap-sized
    dump is presumed truncated and rejected rather than staged corrupt."""
    from mailfallback.services.preview_service import _locate_live_file, _snapshot_bytes

    if row.deleted_at is None:
        path = _locate_live_file(account, row)
        if path:
            try:
                with open(path, "rb") as f:
                    return f.read()
            except OSError:
                logger.debug(
                    "Live read failed for %s; falling back to snapshot",
                    row.message_id_hash.hex(),
                    exc_info=True,
                )
    found = _snapshot_bytes(db, account, row, max_bytes=STAGING_DUMP_MAX_BYTES)
    if found is None:
        return None
    raw = found[0]
    if len(raw) >= STAGING_DUMP_MAX_BYTES:
        logger.warning(
            "Refusing to stage %s: snapshot dump filled the %d-byte cap (presumed truncated)",
            row.message_id_hash.hex(),
            STAGING_DUMP_MAX_BYTES,
        )
        return None
    return raw


def add_messages(
    db: Session,
    user: User,
    items: list[tuple[str, bytes]],
    include_all: bool = False,
) -> dict:
    """Copy messages into the user's staging Maildir. items = [(account_id, hash)].

    Validation and the quota check run BEFORE anything is created or copied:
    a rejected call leaves no area, no Maildir and no burned TTL behind.
    Returns {staged, skipped, failed}. Idempotent per (account, hash) —
    across calls and within one batch. include_all lets an ADMIN stage from
    accounts outside their own scope (the API layer audits those calls);
    non-admins always stay scoped.
    """
    visible = set(_accessible_account_ids(db, user))
    area = db.query(StagingArea).filter(StagingArea.user_id == user.id).first()
    if area is not None and _is_expired(area):
        area = None  # corpse: swept and replaced by _get_or_create_area below

    existing: set[tuple[str, bytes]] = set()
    if area is not None:
        reconcile(db, user, area)
        existing = {
            (m.source_account_id, m.message_id_hash)
            for m in db.query(StagingMessage).filter(StagingMessage.staging_id == area.id)
        }

    # TODO: to_stage holds every resolved raw message in RAM until the quota
    # check; bounded in practice by the API layer's batch cap — revisit if
    # that cap grows.
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
        existing.add((account_id, h))  # in-batch duplicates stage once

    incoming = sum(len(raw) for _, _, raw in to_stage)
    used = area.bytes_used if area is not None else 0
    limit = area.max_bytes if area is not None else settings.staging_max_bytes
    if limit and used + incoming > limit:
        raise StagingQuotaExceededError(
            f"Staging quota exceeded: {used + incoming} > {limit} bytes"
        )

    if not to_stage:
        return {"staged": 0, "skipped": len(items) - failed, "failed": failed}

    area = _get_or_create_area(db, user)
    sdir = staging_dir(user)
    _ensure_maildir(sdir)

    staged = 0
    now = datetime.now(UTC)
    for account, row, raw in to_stage:
        # Random token (not a positional counter): unique even when two
        # requests stage in the same second.
        fname = f"{int(now.timestamp())}.{uuid4().hex[:8]}.{row.message_id_hash.hex()[:12]}:2,"
        tmp_file = os.path.join(sdir, "tmp", fname)
        try:
            with open(tmp_file, "wb") as f:
                f.write(raw)
            # tmp/ -> cur/ rename is atomic (Maildir convention): a concurrent
            # webmail FETCH must never see a half-written file.
            os.rename(tmp_file, os.path.join(sdir, "cur", fname))
        except OSError:
            logger.warning("Staging copy failed for %s", row.message_id_hash.hex(), exc_info=True)
            failed += 1
            with contextlib.suppress(OSError):
                os.remove(tmp_file)
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
    Filenames are matched by stable prefix — Dovecot renames on flag changes.
    Commits only when something actually changed."""
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
    renamed = False
    for m in db.query(StagingMessage).filter(StagingMessage.staging_id == area.id).all():
        actual = on_disk.get(maildir_filename_prefix(m.staged_filename))
        if actual is None:
            db.delete(m)
            dropped += 1
        else:
            if actual != m.staged_filename:
                m.staged_filename = actual
                renamed = True
            total += m.size_bytes
    if dropped or renamed or area.bytes_used != total:
        area.bytes_used = total
        db.commit()
    return dropped


def push(
    db: Session,
    user: User,
    destination: str,
    folder_mode: str,
    custom_folder: str | None = None,
) -> dict:
    """Create one staging_push RestoreJob per target account.

    destination: "origin" (each message back to its source account) or an
    account id override (the API layer validates access). folder_mode:
    "original" (per-row origin folder), "restored" (everything into
    Restored/<today>) or "custom" (everything into custom_folder VERBATIM —
    user-named, hygiene-validated by the API layer). reconcile() runs FIRST
    so webmail deletions win; the per-target manifest {destination_folder:
    [staged_filename, ...]} rides in the job's selected_uids JSON column.
    Rows and files stay staged — the worker removes them only after
    confirmed delivery.

    Returns {"job_ids": [...], "skipped_targets": [...]}: skipped_targets
    lists accounts that could not take a job (busy with a pending/running
    one, no credentials, suspended, migrating). Their messages stay staged,
    and the caller can surface the partial submission instead of silently
    shipping a short job list.
    """
    from mailfallback.services.restore_service import create_restore_job
    from mailfallback.services.restore_worker import submit_restore_job

    # Defense in depth below the endpoint validation: a custom push without a
    # usable path must fail loudly, never group manifests under "None".
    if folder_mode == "custom" and not (custom_folder and custom_folder.strip()):
        raise ValueError("custom folder_mode requires a custom_folder")

    area = db.query(StagingArea).filter(StagingArea.user_id == user.id).first()
    if not area or _is_expired(area):
        return {"job_ids": [], "skipped_targets": []}
    reconcile(db, user, area)
    rows = db.query(StagingMessage).filter(StagingMessage.staging_id == area.id).all()
    if not rows:
        return {"job_ids": [], "skipped_targets": []}

    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    by_target: dict[str, dict[str, list[str]]] = {}
    for m in rows:
        target_id = m.source_account_id if destination == "origin" else destination
        if folder_mode == "original":
            folder = m.original_folder
        elif folder_mode == "custom":
            folder = custom_folder
        else:
            folder = f"Restored/{stamp}"
        by_target.setdefault(target_id, {}).setdefault(folder, []).append(m.staged_filename)

    job_ids: list[str] = []
    skipped_targets: list[str] = []
    for target_id, manifest in by_target.items():
        job = create_restore_job(
            db,
            source_account_id=target_id,
            target_account_id=target_id,
            restore_mode="staging_push",
            requested_by=user.id,
            selected_uids=manifest,
        )
        if job is None:
            logger.warning("Staging push: no job created for target %s", target_id)
            skipped_targets.append(target_id)
            continue
        submit_restore_job(job.id)
        job_ids.append(job.id)
    return {"job_ids": job_ids, "skipped_targets": skipped_targets}


def empty(db: Session, user: User) -> None:
    """Remove the staging Maildir and the area row (cascade removes rows)."""
    area = db.query(StagingArea).filter(StagingArea.user_id == user.id).first()
    _remove_staging_dir(user)
    if area:
        db.delete(area)  # cascade removes rows
        db.commit()


def cleanup_expired(db: Session) -> int:
    """Scheduler entrypoint — purge expired areas (files + rows). Always on."""
    expired = db.query(StagingArea).filter(StagingArea.expires_at <= datetime.now(UTC)).all()
    for area in expired:
        user = db.query(User).filter(User.id == area.user_id).first()
        if user:
            _remove_staging_dir(user)
        db.delete(area)
    db.commit()
    return len(expired)
