"""Mail Index service — build + maintain the per-message metadata catalog.

Public functions:
- upsert_message_set(db, account_id) -> int
- record_snapshot / prune_snapshot / backfill_snapshots — snapshot bitmap upkeep
- backfill_attachments(db, account_id) -> int — attachment rows for pre-era index rows
- backfill_attachment_content(db, account_id) -> int — Tika text for NULL rows

The service owns reads from the live Maildir filesystem. Header upserts use
email.parser.BytesHeaderParser (headers only); on first insert of a message
the file gets one full MIME walk (_parse_attachments) for attachment metadata
(plus Tika text extraction when tika_enabled).
Maildir files are immutable, so attachments are never re-parsed on later walks.

Known limitations:
- Messages without a Message-Id header are silently skipped (the index PK
  requires it). This is rare for received mail; affects mostly drafts
  and malformed messages.
"""

import hashlib
import logging
import os
import threading
import time
from datetime import UTC, datetime
from email import policy
from email.parser import BytesHeaderParser, BytesParser
from email.utils import getaddresses, parsedate_to_datetime

import httpx
from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.models import (
    Account,
    MailIndexAttachment,
    MailIndexMessage,
    MailIndexRebuildStatus,
)
from mailfallback.services import restic_service

logger = logging.getLogger(__name__)

# Maximum rows touched per transaction during the live Maildir walk.
# Bounded so big mailboxes (150k+ messages) don't lock the index for minutes.
BATCH_SIZE = 1000

# Tika extraction caps (restore spec): parts above the byte cap are never
# sent to Tika; extracted text is stored truncated to TIKA_TEXT_CAP
# CHARACTERS (a str slice, not bytes — approximate for multibyte text).
TIKA_MAX_PART_BYTES = 20 * 1024 * 1024
TIKA_TEXT_CAP = 204_800

# Per-walk Tika accounting seam: _extract_attachment_text bumps these
# thread-local counters on every attempt; the walk drivers
# (upsert_message_set, backfill_attachments, backfill_attachment_content)
# reset them on entry and emit ONE INFO summary on exit when any part was
# attempted. This keeps _parse_attachments' contract (list[dict] | None)
# untouched. Thread-local because a walk is synchronous within its thread,
# so concurrent walks (scheduler threads) keep independent counters.
_TIKA_STATS = threading.local()


def _reset_tika_stats() -> None:
    _TIKA_STATS.attempts = 0
    _TIKA_STATS.misses = 0
    _TIKA_STATS.elapsed = 0.0


def _log_tika_stats(context: str) -> None:
    attempts = getattr(_TIKA_STATS, "attempts", 0)
    if not attempts:
        return
    logger.info(
        "Tika extraction (%s): %d parts attempted, %d misses, %.1fs",
        context,
        attempts,
        _TIKA_STATS.misses,
        _TIKA_STATS.elapsed,
    )


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


def _extract_attachment_text(payload: bytes, content_type: str) -> str | None:
    """Tika extraction for one part, with per-walk accounting. Never raises.

    Thin wrapper over _tika_put that bumps the thread-local walk counters
    (attempts / misses / elapsed) — the seam behind _log_tika_stats.
    """
    start = time.monotonic()
    text = _tika_put(payload, content_type)
    _TIKA_STATS.attempts = getattr(_TIKA_STATS, "attempts", 0) + 1
    _TIKA_STATS.misses = getattr(_TIKA_STATS, "misses", 0) + (1 if text is None else 0)
    _TIKA_STATS.elapsed = getattr(_TIKA_STATS, "elapsed", 0.0) + (time.monotonic() - start)
    return text


def _tika_put(payload: bytes, content_type: str) -> str | None:
    """Plain-text extraction of one attachment part via Apache Tika. Never raises.

    PUT {tika_url}/tika/ mirrors the URL shape Dovecot's fts decoder uses
    (config_generator: `fts_decoder_tika_url = {tika_url}/tika/`) — proven
    to work against this Tika container. Non-200, transport errors and
    empty/whitespace-only output all collapse to None at DEBUG level:
    extraction misses are routine (encrypted PDFs, exotic formats) and
    must never fail indexing or sync. Stored text is capped at
    TIKA_TEXT_CAP characters (not bytes).
    """
    try:
        resp = httpx.put(
            f"{settings.tika_url}/tika/",
            content=payload,
            headers={
                "Accept": "text/plain",
                "Content-Type": content_type or "application/octet-stream",
            },
            timeout=10.0,
        )
    except Exception:
        logger.debug("Tika extraction failed (transport)", exc_info=True)
        return None
    if resp.status_code != 200:
        logger.debug("Tika extraction failed: HTTP %s", resp.status_code)
        return None
    text = resp.text[:TIKA_TEXT_CAP]
    if not text.strip():
        return None
    return text


def _parse_attachments(path: str) -> list[dict] | None:
    """Full MIME walk of one Maildir file. Returns attachment metadata rows.

    An attachment is a non-multipart leaf with a filename (Content-Disposition
    or Content-Type name param — policy.default decodes RFC 2047/2231).
    `part_index` numbers ALL non-multipart leaves in walk order, so a later
    re-walk can address the same part without ambiguity.
    `size_bytes` is None when the decoded size is unknown (message/* parts,
    malformed CTE) — never a fake 0. Returns None on unreadable/unparsable files.
    When tika_enabled, decodable parts up to TIKA_MAX_PART_BYTES also get
    `content_text` extracted via Tika (None on any miss — never an error).
    """
    parser = BytesParser(policy=policy.default)
    try:
        with open(path, "rb") as f:
            msg = parser.parse(f)
    except (OSError, ValueError):
        return None
    out: list[dict] = []
    part_index = 0
    extract = settings.tika_enabled
    try:
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            part_index += 1
            filename = part.get_filename()
            if not filename:
                continue
            try:
                # None for non-decodable parts (e.g. message/rfc822)
                payload = part.get_payload(decode=True)
            except Exception:  # malformed CTE — keep the row, size unknown
                payload = None
            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            content_type = part.get_content_type()
            content_text = None
            if extract and payload is not None and len(payload) <= TIKA_MAX_PART_BYTES:
                content_text = _extract_attachment_text(payload, content_type)
            out.append(
                {
                    "part_index": part_index,
                    "filename": filename,
                    "ext": ext,
                    "size_bytes": len(payload) if payload is not None else None,
                    "content_type": content_type,
                    "content_text": content_text,
                }
            )
    except Exception:
        logger.warning("Attachment parse failed for %s", path, exc_info=True)
        return None
    return out


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


def maildir_folder_bases(maildir_path: str, folder_path: str) -> tuple[str, ...]:
    """Candidate filesystem base directories for an indexed folder_path.

    "INBOX" is ambiguous: production mbsync writes a real INBOX/ subdirectory
    (`Inbox {path}/INBOX` in mbsync_config), but _walk_maildir also maps bare
    top-level cur/new to folder_path="INBOX". Callers reconstructing a file
    path from index coordinates must therefore try BOTH bases, in this order.
    Every other folder (including "INBOX/Sub") maps 1:1 to its subdirectory.
    """
    if folder_path == "INBOX":
        return (os.path.join(maildir_path, "INBOX"), maildir_path)
    return (os.path.join(maildir_path, folder_path),)


def upsert_message_set(db: Session, account_id: str) -> int:
    """Walk the account's live Maildir and reconcile the index with disk.

    Cost model: one bulk SELECT of the account's rows; files whose
    (folder, filename) coordinates are already indexed are skipped without
    opening them (Maildir files are content-immutable — every change is a
    rename). Rows are written only for real changes: new mail, relocated
    files (flag renames / folder moves), reappearing mail, disappeared mail.
    Returns the count of rows written.
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

    # One bulk read instead of one SELECT per message.
    by_file: dict[tuple[str, str], bytes] = {}
    by_hash: dict[bytes, tuple[str, str, bool]] = {}
    for h, folder, fn, deleted_at in (
        db.query(
            MailIndexMessage.message_id_hash,
            MailIndexMessage.folder_path,
            MailIndexMessage.maildir_filename,
            MailIndexMessage.deleted_at,
        )
        .filter(MailIndexMessage.account_id == account_id)
        .yield_per(BATCH_SIZE)
    ):
        by_file[(folder, fn)] = h
        by_hash[h] = (folder, fn, deleted_at is not None)

    seen_hashes: set[bytes] = set()
    seen_files: set[tuple[str, str]] = set()
    relocations: dict[bytes, tuple[str, str]] = {}
    touched = 0
    _reset_tika_stats()
    try:
        for folder, filename, full_path in _walk_maildir(account.maildir_path):
            key = (folder, filename)
            seen_files.add(key)
            known = by_file.get(key)
            if known is not None:
                # Content-immutable file already indexed: nothing to do.
                seen_hashes.add(known)
                continue
            parsed = _parse_headers(full_path)
            if not parsed:
                continue
            h = parsed["message_id_hash"]
            seen_hashes.add(h)
            if h in by_hash:
                # Known message under new coordinates (flag rename, folder
                # move, or an additional duplicate copy) — decide after the
                # walk, when seen_files is complete.
                relocations.setdefault(h, key)
                continue
            by_hash[h] = (folder, filename, False)
            by_file[key] = h
            atts = _parse_attachments(full_path)
            now = datetime.now(UTC)
            db.add(
                MailIndexMessage(
                    account_id=account_id,
                    folder_path=folder,
                    maildir_filename=filename,
                    has_attachments=bool(atts),
                    # parse failure (None) stays NULL so the backfill
                    # (attachments_indexed_at IS NULL) retries the file
                    attachments_indexed_at=now if atts is not None else None,
                    **parsed,
                )
            )
            for a in atts or []:
                db.add(
                    MailIndexAttachment(
                        account_id=account_id,
                        message_id_hash=h,
                        **a,
                    )
                )
            touched += 1
            # Bound transaction size for big mailboxes (150k+ messages)
            if touched % BATCH_SIZE == 0:
                db.commit()

        now = datetime.now(UTC)

        # Relocations and un-deletes: write only rows whose stored pointer is
        # stale or whose deleted flag must flip. A stored pointer that still
        # exists on disk stays put (Gmail keeps duplicate copies; rewriting
        # the pointer every run would ping-pong between them).
        for h in seen_hashes:
            stored = by_hash.get(h)
            if stored is None:
                continue
            folder, fn, is_deleted = stored
            pointer_stale = (folder, fn) not in seen_files
            new_coords = relocations.get(h)
            if pointer_stale and new_coords is None:
                # Every known copy vanished but the hash was seen: the seen
                # copy IS one of the walked files, so it can only be here if
                # it matched by_file — pointer can't be stale. Defensive skip.
                continue
            if pointer_stale or is_deleted:
                row = (
                    db.query(MailIndexMessage)
                    .filter(
                        MailIndexMessage.account_id == account_id,
                        MailIndexMessage.message_id_hash == h,
                    )
                    .first()
                )
                if row is None:
                    continue
                if pointer_stale:
                    row.folder_path, row.maildir_filename = new_coords
                row.deleted_at = None
                row.last_seen_at = now
                touched += 1
                if touched % BATCH_SIZE == 0:
                    db.commit()

        # Soft-delete: alive rows whose hash was not seen anywhere on disk.
        deleted_in_batch = 0
        for h, (_folder, _fn, is_deleted) in by_hash.items():
            if is_deleted or h in seen_hashes:
                continue
            row = (
                db.query(MailIndexMessage)
                .filter(
                    MailIndexMessage.account_id == account_id,
                    MailIndexMessage.message_id_hash == h,
                )
                .first()
            )
            if row is None:
                continue
            row.deleted_at = now
            row.last_seen_at = now
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
        # One summary per walk, even on partial failure; silent when no
        # part was attempted (tika disabled, or no new attachments).
        _log_tika_stats("index walk")
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


def maildir_filename_prefix(filename: str) -> str:
    """Return the stable prefix of a Maildir filename (everything before the
    flag suffix). E.g. '1234.M5.host:2,RS' -> '1234.M5.host:2,'.

    Flags change on read/seen renames while the prefix stays put — every
    filename comparison across time (index row vs live dir, index row vs
    snapshot listing) must apply this helper to BOTH sides.
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
    prefix_to_hash = {maildir_filename_prefix(fn): h for h, fn in alive}

    snaps = restic_service.list_snapshots(backup.destination, account_id)

    # Find already-processed snapshots so we can skip them on resume.
    # The previous behavior re-issued restic ls for every snapshot; harmless
    # for correctness (ON CONFLICT DO NOTHING) but expensive on remote repos.
    already_done = {
        sid
        for (sid,) in db.query(SnapshotMessage.snapshot_id)
        .filter(SnapshotMessage.account_id == account_id)
        .distinct()
    }

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
            if sid in already_done:
                # Already processed — skip the restic ls call entirely
                if rs:
                    rs.backfill_progress = i + 1
                    db.commit()
                yield {
                    "snapshot_id": sid,
                    "total": len(snaps),
                    "processed": i + 1,
                    "bits_inserted": 0,
                    "skipped": True,
                }
                continue
            seen_hashes: set[bytes] = set()
            for path in restic_service.list_files(backup.destination, account_id, sid):
                if "/cur/" not in path and "/new/" not in path:
                    continue
                fn = path.rsplit("/", 1)[-1]
                h = prefix_to_hash.get(maildir_filename_prefix(fn))
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
                "skipped": False,
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


def backfill_attachments(db: Session, account_id: str) -> int:
    """Parse attachments for alive rows that pre-date the attachment index.

    Resumable: only rows with attachments_indexed_at IS NULL are processed.
    Two failure modes are deliberately distinct:
    - Maildir file NOT FOUND (deleted, or flag-suffix renamed since the last
      walk): the row is skipped WITHOUT setting the marker — the next
      upsert_message_set refreshes maildir_filename or sets deleted_at, so
      the pending set converges instead of baking has_attachments=False.
    - File present but unparsable/unreadable: the marker IS set, so one bad
      file cannot wedge the backfill — it just stays without attachment rows.
    Idempotent per message: delete-and-reinsert its attachment rows.
    Returns the number of rows marked processed (skipped rows not counted).
    """
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise ValueError(f"Account {account_id} not found")

    pending = (
        db.query(MailIndexMessage)
        .filter(
            MailIndexMessage.account_id == account_id,
            MailIndexMessage.deleted_at.is_(None),
            MailIndexMessage.attachments_indexed_at.is_(None),
        )
        .all()
    )
    processed = 0
    now = datetime.now(UTC)
    _reset_tika_stats()
    for row in pending:
        path = None
        for base in maildir_folder_bases(account.maildir_path, row.folder_path):
            for sub in ("cur", "new"):
                candidate = os.path.join(base, sub, row.maildir_filename)
                if os.path.exists(candidate):
                    path = candidate
                    break
            if path:
                break
        if path is None:
            # File gone (or flag-suffix renamed): leave the row pending so the
            # next upsert_message_set reconciles it. Re-checking later costs
            # only the os.path.exists probes above.
            continue
        atts = _parse_attachments(path)
        db.query(MailIndexAttachment).filter(
            MailIndexAttachment.account_id == account_id,
            MailIndexAttachment.message_id_hash == row.message_id_hash,
        ).delete(synchronize_session=False)
        for a in atts or []:
            db.add(
                MailIndexAttachment(
                    account_id=account_id,
                    message_id_hash=row.message_id_hash,
                    **a,
                )
            )
        row.has_attachments = bool(atts)
        row.attachments_indexed_at = now
        processed += 1
        if processed % BATCH_SIZE == 0:
            db.commit()
    db.commit()
    _log_tika_stats("attachment backfill")
    return processed


def backfill_attachment_content(db: Session, account_id: str) -> int:
    """Extract Tika text for attachment rows still missing content_text.

    Operator-invoked (`mfb index backfill-attachments <id> --content-only`);
    refuses outright when Tika is disabled. Visits each alive message that
    has at least one attachment row with content_text IS NULL, locates its
    live file (exact filename, then flag-rename prefix fallback — the
    preview locator), re-walks it with _parse_attachments (which extracts
    now that Tika is on) and fills ONLY the still-NULL rows, matched by
    part_index. Missing files are skipped WITHOUT marking anything — the
    next upsert_message_set reconciles them, same contract as the metadata
    backfill above.

    Returns the number of attachment ROWS filled (not messages). Rows whose
    extraction misses stay NULL, so the backfill is resumable by
    construction — which also means persistent misses (e.g. encrypted PDFs)
    are retried on every run; acceptable for an operator-invoked command.
    Commits every BATCH_SIZE messages.
    """
    if not settings.tika_enabled:
        raise ValueError(
            "Tika is disabled (MAILFALLBACK_TIKA_ENABLED=false) — content extraction is unavailable"
        )
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise ValueError(f"Account {account_id} not found")

    # Deferred import: preview_service imports index_service helpers at
    # module level, so the reverse import must stay function-local.
    from mailfallback.services.preview_service import _locate_live_file

    pending = (
        db.query(MailIndexMessage)
        .filter(
            MailIndexMessage.account_id == account_id,
            MailIndexMessage.deleted_at.is_(None),
            db.query(MailIndexAttachment)
            .filter(
                MailIndexAttachment.account_id == MailIndexMessage.account_id,
                MailIndexAttachment.message_id_hash == MailIndexMessage.message_id_hash,
                MailIndexAttachment.content_text.is_(None),
            )
            .exists(),
        )
        .all()
    )
    filled = 0
    _reset_tika_stats()
    for visited, row in enumerate(pending, start=1):
        path = _locate_live_file(account, row)
        # path None: file gone — skip without marking; the next walk reconciles
        if path is not None:
            atts = _parse_attachments(path)
            extracted = {
                a["part_index"]: a["content_text"] for a in atts or [] if a["content_text"]
            }
            if extracted:
                null_rows = (
                    db.query(MailIndexAttachment)
                    .filter(
                        MailIndexAttachment.account_id == account_id,
                        MailIndexAttachment.message_id_hash == row.message_id_hash,
                        MailIndexAttachment.content_text.is_(None),
                    )
                    .all()
                )
                for att in null_rows:
                    text = extracted.get(att.part_index)
                    if text is not None:
                        att.content_text = text
                        filled += 1
        if visited % BATCH_SIZE == 0:
            db.commit()
    db.commit()
    _log_tika_stats("content backfill")
    return filled
