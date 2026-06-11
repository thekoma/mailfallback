"""Message preview — headers + body snippet, from live Maildir or snapshot.

No IMAP session: live files are read straight from disk via the index
locator (folder_path + maildir_filename); snapshot-only messages come out
of restic via dump_file. Snippets are capped — this is a peek, not a reader.
"""

import logging
import os
import re
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser

from sqlalchemy.orm import Session

from mailfallback.models import (
    Account,
    BackupPolicy,
    MailIndexAttachment,
    MailIndexMessage,
    SnapshotMessage,
)
from mailfallback.services import restic_service
from mailfallback.services.index_service import maildir_folder_bases

logger = logging.getLogger(__name__)

SNIPPET_CHARS = 2048
_TAG_RE = re.compile(r"<[^>]+>")


def _locate_live_file(account: Account, row: MailIndexMessage) -> str | None:
    """Find the message file on disk, tolerating flag-suffix renames."""
    bases = maildir_folder_bases(account.maildir_path, row.folder_path)
    for base in bases:
        for sub in ("cur", "new"):
            candidate = os.path.join(base, sub, row.maildir_filename)
            if os.path.exists(candidate):
                return candidate
    # Flags may have changed the suffix since the last index walk — match on
    # the stable prefix instead.
    prefix = row.maildir_filename.split(":2,")[0]
    for base in bases:
        for sub in ("cur", "new"):
            d = os.path.join(base, sub)
            if not os.path.isdir(d):
                continue
            for fn in os.listdir(d):
                if fn.split(":2,")[0] == prefix:
                    return os.path.join(d, fn)
    return None


def _snapshot_bytes(
    db: Session, account: Account, row: MailIndexMessage
) -> tuple[bytes, str] | None:
    """Raw message bytes from the newest snapshot that contains the message.

    Returns (raw, snapshot_short_id) or None. Best-effort: restic failures
    (list_snapshots raises, dump_file returns None) degrade to None.
    """
    policy_row = db.query(BackupPolicy).filter(BackupPolicy.account_id == account.id).first()
    if not policy_row:
        return None
    snap_ids = {
        sid
        for (sid,) in db.query(SnapshotMessage.snapshot_id).filter(
            SnapshotMessage.account_id == account.id,
            SnapshotMessage.message_id_hash == row.message_id_hash,
        )
    }
    if not snap_ids:
        return None
    try:
        snaps = restic_service.list_snapshots(policy_row.destination, account.id)
    except Exception:
        logger.warning("Preview: list_snapshots failed for %s", account.id, exc_info=True)
        return None
    for s in sorted(snaps, key=lambda s: s.get("time", ""), reverse=True):
        sid = s.get("short_id") or s.get("id", "")[:8]
        if sid not in snap_ids:
            continue
        for base in maildir_folder_bases(account.maildir_path, row.folder_path):
            for sub in ("cur", "new"):
                raw = restic_service.dump_file(
                    policy_row.destination,
                    account.id,
                    sid,
                    os.path.join(base, sub, row.maildir_filename),
                )
                if raw:
                    return raw, sid
    return None


def _body_snippet(msg: EmailMessage) -> str:
    """Plain-text snippet of the message body, capped at SNIPPET_CHARS."""
    try:
        text_part = msg.get_body(preferencelist=("plain", "html"))
        if text_part is None:
            return ""
        content = text_part.get_content()
        if text_part.get_content_type() == "text/html":
            content = _TAG_RE.sub(" ", content)
    except Exception:  # arbitrary inbound MIME — never let a peek raise
        return ""
    return " ".join(content.split())[:SNIPPET_CHARS]


def get_preview(db: Session, account: Account, message_id_hash: bytes) -> dict | None:
    """Headers + body snippet + attachment list for one indexed message.

    Source order: live Maildir file while the row is alive, otherwise the
    newest snapshot containing the message. Returns None when the message is
    unknown or its bytes are unreachable everywhere.
    """
    row = (
        db.query(MailIndexMessage)
        .filter(
            MailIndexMessage.account_id == account.id,
            MailIndexMessage.message_id_hash == message_id_hash,
        )
        .first()
    )
    if not row:
        return None

    raw = None
    source = "live"
    if row.deleted_at is None:
        path = _locate_live_file(account, row)
        if path:
            try:
                with open(path, "rb") as f:
                    raw = f.read()
            except OSError:
                raw = None
    if raw is None:
        found = _snapshot_bytes(db, account, row)
        if found:
            raw, sid = found
            source = f"snapshot:{sid}"
    if raw is None:
        return None

    msg = BytesParser(policy=policy.default).parsebytes(raw)
    atts = (
        db.query(MailIndexAttachment)
        .filter(
            MailIndexAttachment.account_id == account.id,
            MailIndexAttachment.message_id_hash == message_id_hash,
        )
        .order_by(MailIndexAttachment.part_index)
        .all()
    )
    return {
        "subject": row.subject,
        "from_addr": row.from_addr,
        "from_name": row.from_name,
        "to_addrs": row.to_addrs or [],
        "date_sent": row.date_sent.isoformat() if row.date_sent else None,
        "folder_path": row.folder_path,
        "alive_in_live": row.deleted_at is None,
        "source": source,
        "body_snippet": _body_snippet(msg),
        "attachments": [
            {"filename": a.filename, "ext": a.ext, "size_bytes": a.size_bytes} for a in atts
        ],
    }
