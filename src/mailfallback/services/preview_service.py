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
from mailfallback.services.index_service import maildir_filename_prefix, maildir_folder_bases

logger = logging.getLogger(__name__)

SNIPPET_CHARS = 2048
# Newest matching snapshots probed with the exact current filename before
# falling back to one prefix-based locate (see _snapshot_bytes).
MAX_SNAPSHOT_ATTEMPTS = 3
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
    prefix = maildir_filename_prefix(row.maildir_filename)
    for base in bases:
        for sub in ("cur", "new"):
            d = os.path.join(base, sub)
            if not os.path.isdir(d):
                continue
            for fn in os.listdir(d):
                if maildir_filename_prefix(fn) == prefix:
                    return os.path.join(d, fn)
    return None


def _snapshot_bytes(
    db: Session,
    account: Account,
    row: MailIndexMessage,
    max_bytes: int = restic_service.DUMP_MAX_BYTES,
) -> tuple[bytes, str] | None:
    """Raw message bytes from the newest snapshot that contains the message.

    max_bytes caps each restic dump, which truncates SILENTLY at the cap.
    The default suits previews (parse a peek); staging passes its own larger
    cap and treats a cap-sized result as truncated.

    Filenames drift: snapshot bits are prefix-matched at backfill time, and
    webmail reads rename the live file (the write-seen ACL adds flags) while
    snapshots are immutable — so a snapshot may hold the message under an
    OLD name that exact dumps of the CURRENT row.maildir_filename never hit.
    Strategy: exact-name dumps against the MAX_SNAPSHOT_ATTEMPTS newest
    matching snapshots, then ONE prefix-based locate (restic ls) on the
    newest matching snapshot. Returns (raw, snapshot_short_id) or None;
    best-effort — any restic failure degrades to None, never raises.
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
        # list_snapshots returns newest-first (documented contract)
        snaps = restic_service.list_snapshots(policy_row.destination, account.id)
        matching = [
            sid
            for snap in snaps
            if (sid := snap.get("short_id") or snap.get("id", "")[:8]) in snap_ids
        ]
        for sid in matching[:MAX_SNAPSHOT_ATTEMPTS]:
            for base in maildir_folder_bases(account.maildir_path, row.folder_path):
                for sub in ("cur", "new"):
                    raw = restic_service.dump_file(
                        policy_row.destination,
                        account.id,
                        sid,
                        os.path.join(base, sub, row.maildir_filename),
                        max_bytes=max_bytes,
                    )
                    if raw:
                        return raw, sid
        if matching:
            # Exact name missed everywhere — assume rename drift and locate
            # the message by its stable prefix in the newest matching snapshot.
            sid = matching[0]
            prefix = maildir_filename_prefix(row.maildir_filename)
            for path in restic_service.list_files(policy_row.destination, account.id, sid):
                if "/cur/" not in path and "/new/" not in path:
                    continue
                if maildir_filename_prefix(path.rsplit("/", 1)[-1]) != prefix:
                    continue
                raw = restic_service.dump_file(
                    policy_row.destination, account.id, sid, path, max_bytes=max_bytes
                )
                if raw:
                    return raw, sid
    except Exception:
        logger.warning("Preview: snapshot lookup failed for %s", account.id, exc_info=True)
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
                    # Same cap as snapshot dumps — preview parses a peek,
                    # truncated MIME is acceptable.
                    raw = f.read(restic_service.DUMP_MAX_BYTES)
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
