"""Mail Index service — build + maintain the per-message metadata catalog.

Public functions:
- upsert_message_set(db, account_id) -> int

Future tasks add: record_snapshot, prune_snapshot, backfill_snapshots.

The service owns reads from the live Maildir filesystem (header-only via
email.parser.BytesHeaderParser — body is never touched).

Known limitations:
- Messages without a Message-Id header are silently skipped (the index PK
  requires it). This is rare for received mail; affects mostly drafts
  and malformed messages.
"""

import hashlib
import logging
import os
from datetime import UTC, datetime
from email import policy
from email.parser import BytesHeaderParser
from email.utils import getaddresses, parsedate_to_datetime

from sqlalchemy.orm import Session

from mailfallback.models import (
    Account,
    MailIndexMessage,
    MailIndexRebuildStatus,
)
from mailfallback.services import restic_service

logger = logging.getLogger(__name__)

# Maximum rows touched per transaction during the live Maildir walk.
# Bounded so big mailboxes (150k+ messages) don't lock the index for minutes.
BATCH_SIZE = 1000


def _hash_message_id(message_id: str) -> bytes:
    """SHA-1 of the bare Message-Id (without angle brackets)."""
    bare = message_id.strip().lstrip("<").rstrip(">")
    return hashlib.sha1(bare.encode("utf-8", errors="replace"), usedforsecurity=False).digest()


def _parse_headers(path: str) -> dict | None:
    """Read just the headers from a Maildir file. Returns None if no Message-Id."""
    parser = BytesHeaderParser(policy=policy.default)
    try:
        with open(path, "rb") as f:
            msg = parser.parse(f)
    except OSError:
        return None
    msgid = msg.get("Message-Id") or msg.get("Message-ID")
    if not msgid:
        return None
    msgid = str(msgid).strip()
    date_sent = None
    raw_date = msg.get("Date")
    if raw_date:
        try:
            date_sent = parsedate_to_datetime(str(raw_date))
            if date_sent and date_sent.tzinfo is None:
                date_sent = date_sent.replace(tzinfo=UTC)
        except (TypeError, ValueError):
            date_sent = None
    from_pair = getaddresses([str(msg.get("From", ""))])
    from_name, from_addr = from_pair[0] if from_pair else ("", "")
    to_addrs = [a for _, a in getaddresses([str(msg.get("To", ""))]) if a]
    return {
        "message_id": msgid,
        "message_id_hash": _hash_message_id(msgid),
        "date_sent": date_sent,
        "from_addr": from_addr or None,
        "from_name": from_name or None,
        "subject": str(msg.get("Subject", "")) or None,
        "to_addrs": to_addrs or None,
        "size_bytes": os.path.getsize(path),
    }


def _walk_maildir(maildir_root: str):
    """Yield (folder_path, filename, full_path) for every Maildir mail file."""
    for dirpath, _, filenames in os.walk(maildir_root):
        if os.path.basename(dirpath) not in ("cur", "new"):
            continue
        rel = os.path.relpath(dirpath, maildir_root)
        # rel is like "cur" (top-level INBOX) or "Sent/cur" or "[Gmail]/All Mail/cur"
        folder = "INBOX" if rel in ("cur", "new") else (os.path.dirname(rel) or "INBOX")
        for fn in filenames:
            yield folder, fn, os.path.join(dirpath, fn)


def upsert_message_set(db: Session, account_id: str) -> int:
    """Walk the account's live Maildir, upsert every mail's headers, mark
    rows missing-from-disk as deleted. Returns count of rows touched.
    """
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise ValueError(f"Account {account_id} not found")

    rs = (
        db.query(MailIndexRebuildStatus)
        .filter(MailIndexRebuildStatus.account_id == account_id)
        .first()
    )
    if rs is None:
        rs = MailIndexRebuildStatus(account_id=account_id, state="live_indexing")
        db.add(rs)
    else:
        rs.state = "live_indexing"
    db.commit()

    seen_hashes: set[bytes] = set()
    touched = 0
    try:
        for folder, filename, full_path in _walk_maildir(account.maildir_path):
            parsed = _parse_headers(full_path)
            if not parsed:
                continue
            seen_hashes.add(parsed["message_id_hash"])
            existing = (
                db.query(MailIndexMessage)
                .filter(
                    MailIndexMessage.account_id == account_id,
                    MailIndexMessage.message_id_hash == parsed["message_id_hash"],
                )
                .first()
            )
            now = datetime.now(UTC)
            if existing:
                existing.last_seen_at = now
                existing.deleted_at = None
                existing.folder_path = folder
                existing.maildir_filename = filename
            else:
                db.add(
                    MailIndexMessage(
                        account_id=account_id,
                        folder_path=folder,
                        maildir_filename=filename,
                        **parsed,
                    )
                )
            touched += 1
            # Bound transaction size for big mailboxes (150k+ messages)
            if touched % BATCH_SIZE == 0:
                db.commit()
        # Mark missing rows as deleted
        alive = (
            db.query(MailIndexMessage)
            .filter(
                MailIndexMessage.account_id == account_id,
                MailIndexMessage.deleted_at.is_(None),
            )
            .all()
        )
        now = datetime.now(UTC)
        deleted_in_batch = 0
        for row in alive:
            if row.message_id_hash not in seen_hashes:
                row.deleted_at = now
                touched += 1
                deleted_in_batch += 1
                if deleted_in_batch % BATCH_SIZE == 0:
                    db.commit()
        rs.state = "idle"
        rs.last_indexed_at = now
        rs.last_error = None
    except Exception as e:
        rs.state = "failed"
        rs.last_error = str(e)
        logger.exception("upsert_message_set failed for %s", account_id)
        raise
    finally:
        db.commit()
    return touched


def record_snapshot(db: Session, account_id: str, snapshot_id: str) -> int:
    """Bulk INSERT a snapshot_messages row for every alive message in the
    account. Idempotent via INSERT ... ON CONFLICT DO NOTHING.
    Returns count of rows actually inserted.
    """
    from mailfallback.models import MailIndexMessage, SnapshotMessage

    alive = (
        db.query(MailIndexMessage.message_id_hash)
        .filter(
            MailIndexMessage.account_id == account_id,
            MailIndexMessage.deleted_at.is_(None),
        )
        .all()
    )
    if not alive:
        return 0

    rows = [
        {"snapshot_id": snapshot_id, "account_id": account_id, "message_id_hash": h[0]}
        for h in alive
    ]
    if db.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = pg_insert(SnapshotMessage).values(rows).on_conflict_do_nothing()
    else:
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        stmt = sqlite_insert(SnapshotMessage).values(rows).on_conflict_do_nothing()
    result = db.execute(stmt)
    db.commit()
    return result.rowcount or 0


def prune_snapshot(db: Session, snapshot_id: str) -> int:
    """DELETE all snapshot_messages rows for the given snapshot_id. Returns count."""
    from mailfallback.models import SnapshotMessage

    deleted = (
        db.query(SnapshotMessage)
        .filter(SnapshotMessage.snapshot_id == snapshot_id)
        .delete(synchronize_session=False)
    )
    db.commit()
    return deleted


def _filename_prefix(filename: str) -> str:
    """Return the stable prefix of a Maildir filename (everything before the
    flag suffix). E.g. '1234.M5.host:2,RS' -> '1234.M5.host:2,'.
    """
    if ":2," in filename:
        return filename.split(":2,")[0] + ":2,"
    return filename


def backfill_snapshots(db: Session, account_id: str):
    """For each restic snapshot, set snapshot_messages bits for messages
    whose Maildir filename appears in the snapshot file list.

    Yields progress dicts: {snapshot_id, total, processed, bits_inserted}.
    """
    from mailfallback.models import (
        BackupPolicy,
        MailIndexMessage,
        MailIndexRebuildStatus,
        SnapshotMessage,
    )

    backup = db.query(BackupPolicy).filter(BackupPolicy.account_id == account_id).first()
    if not backup:
        raise ValueError(f"Account {account_id} has no backup policy")

    # Build a lookup: filename_prefix -> message_id_hash for all alive messages
    alive = (
        db.query(MailIndexMessage.message_id_hash, MailIndexMessage.maildir_filename)
        .filter(
            MailIndexMessage.account_id == account_id,
            MailIndexMessage.deleted_at.is_(None),
        )
        .all()
    )
    prefix_to_hash = {_filename_prefix(fn): h for h, fn in alive}

    snaps = restic_service.list_snapshots(backup.destination, account_id)

    rs = (
        db.query(MailIndexRebuildStatus)
        .filter(MailIndexRebuildStatus.account_id == account_id)
        .first()
    )
    if rs:
        rs.state = "snap_backfilling"
        rs.backfill_progress = 0
        rs.backfill_total = len(snaps)
        db.commit()

    try:
        for i, s in enumerate(snaps):
            sid = s.get("short_id") or s.get("id", "")[:8]
            if not sid:
                continue
            seen_hashes: set[bytes] = set()
            for path in restic_service.list_files(backup.destination, account_id, sid):
                if "/cur/" not in path and "/new/" not in path:
                    continue
                fn = path.rsplit("/", 1)[-1]
                h = prefix_to_hash.get(_filename_prefix(fn))
                if h:
                    seen_hashes.add(h)
            inserted = 0
            if seen_hashes:
                rows = [
                    {"snapshot_id": sid, "account_id": account_id, "message_id_hash": h}
                    for h in seen_hashes
                ]
                if db.bind.dialect.name == "postgresql":
                    from sqlalchemy.dialects.postgresql import insert as pg_insert

                    stmt = pg_insert(SnapshotMessage).values(rows).on_conflict_do_nothing()
                else:
                    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

                    stmt = sqlite_insert(SnapshotMessage).values(rows).on_conflict_do_nothing()
                result = db.execute(stmt)
                inserted = result.rowcount or 0
                db.commit()
            if rs:
                rs.backfill_progress = i + 1
                db.commit()
            yield {
                "snapshot_id": sid,
                "total": len(snaps),
                "processed": i + 1,
                "bits_inserted": inserted,
            }
        if rs:
            rs.state = "idle"
            rs.last_error = None
            db.commit()
    except Exception as e:
        if rs:
            rs.state = "failed"
            rs.last_error = str(e)
            db.commit()
        raise
