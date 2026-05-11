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

logger = logging.getLogger(__name__)


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
        for row in alive:
            if row.message_id_hash not in seen_hashes:
                row.deleted_at = now
                touched += 1
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
