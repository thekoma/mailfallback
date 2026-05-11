"""Search service — query mail_index with optional Phase 2 body filter."""

import contextlib
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from mailfallback.models import (
    Account,
    MailIndexMessage,
    SnapshotMessage,
    User,
    account_groups,
    account_owners,
    group_members,
)

logger = logging.getLogger(__name__)


def _accessible_account_ids(db: Session, user: User) -> list[str]:
    """Account IDs visible to the user via direct ownership OR group membership."""
    owned = (
        db.query(Account.id)
        .join(account_owners, account_owners.c.account_id == Account.id)
        .filter(account_owners.c.user_id == user.id)
    )
    via_groups = (
        db.query(Account.id)
        .join(account_groups, account_groups.c.account_id == Account.id)
        .join(group_members, group_members.c.group_id == account_groups.c.group_id)
        .filter(group_members.c.user_id == user.id)
    )
    return [r[0] for r in owned.union(via_groups).all()]


def search_messages(
    db: Session,
    *,
    user: User,
    query: str,
    account_ids: list[str] | None = None,
    range_start: datetime | None = None,
    range_end: datetime | None = None,
    include_deleted: bool = True,
    snapshot_id: str | None = None,
    body: bool = False,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    """Phase 1 (always): Postgres index query.
    Phase 2 (if body=True): Dovecot SEARCH body filter on candidates (Task 9).

    Returns: {results, total, page, page_size, phase2_skipped_count}
    """
    visible = _accessible_account_ids(db, user)
    if not visible:
        return {
            "results": [],
            "total": 0,
            "page": page,
            "page_size": page_size,
            "phase2_skipped_count": 0,
        }
    scope = [a for a in account_ids if a in visible] if account_ids else visible
    if not scope:
        return {
            "results": [],
            "total": 0,
            "page": page,
            "page_size": page_size,
            "phase2_skipped_count": 0,
        }

    q = db.query(MailIndexMessage).filter(MailIndexMessage.account_id.in_(scope))
    if not include_deleted:
        q = q.filter(MailIndexMessage.deleted_at.is_(None))
    # NULL date_sent is treated as "unknown date" and kept in the result set
    # for any range — otherwise messages whose Date: header didn't parse
    # disappear from any date-filtered search. Discovered via T10's wrapper
    # test: the workspace UI passes a wide year range and expects messages
    # without a parsed date to still match.
    if range_start:
        q = q.filter(
            (MailIndexMessage.date_sent >= range_start) | MailIndexMessage.date_sent.is_(None)
        )
    if range_end:
        q = q.filter(
            (MailIndexMessage.date_sent <= range_end) | MailIndexMessage.date_sent.is_(None)
        )
    if snapshot_id:
        q = q.join(
            SnapshotMessage,
            (SnapshotMessage.account_id == MailIndexMessage.account_id)
            & (SnapshotMessage.message_id_hash == MailIndexMessage.message_id_hash),
        ).filter(SnapshotMessage.snapshot_id == snapshot_id)
    if query:
        # Use tsvector match on Postgres; fall back to ILIKE on SQLite (tests).
        if db.bind.dialect.name == "postgresql":
            q = q.filter(MailIndexMessage.tsv.op("@@")(func.plainto_tsquery("simple", query)))
        else:
            pat = f"%{query}%"
            q = q.filter(
                (MailIndexMessage.subject.ilike(pat))
                | (MailIndexMessage.from_addr.ilike(pat))
                | (MailIndexMessage.from_name.ilike(pat))
            )
    q = q.order_by(MailIndexMessage.date_sent.desc().nullslast())

    total = q.count()
    rows = q.offset((page - 1) * page_size).limit(page_size).all()

    # Build snapshot membership lookup for the result set
    if rows:
        hashes = [r.message_id_hash for r in rows]
        snap_rows = (
            db.query(
                SnapshotMessage.account_id,
                SnapshotMessage.message_id_hash,
                SnapshotMessage.snapshot_id,
            )
            .filter(SnapshotMessage.message_id_hash.in_(hashes))
            .all()
        )
        snap_by_msg: dict[tuple[str, bytes], list[str]] = {}
        for acc, h, sid in snap_rows:
            snap_by_msg.setdefault((acc, h), []).append(sid)
    else:
        snap_by_msg = {}

    body_matched_set: set[bytes] = set()
    phase2_skipped = 0
    if body and query and rows:
        from mailfallback.config import settings

        cap = getattr(settings, "search_body_candidate_cap", 500)
        if len(rows) > cap:
            phase2_skipped = len(rows) - cap
            candidates_rows = rows[:cap]
        else:
            candidates_rows = rows
        by_account: dict[str, list[tuple[bytes, str, str, str]]] = {}
        for r in candidates_rows:
            if r.deleted_at is not None:
                continue  # snapshot-only — skip Phase 2 (documented v1 limitation)
            by_account.setdefault(r.account_id, []).append(
                (r.message_id_hash, r.folder_path, r.maildir_filename, r.message_id)
            )
        for acc_id, cands in by_account.items():
            body_matched_set.update(_dovecot_filter_body(db, acc_id, cands, query))

    results = []
    for r in rows:
        results.append(
            {
                "message_id": r.message_id,
                "account_id": r.account_id,
                "subject": r.subject,
                "from_addr": r.from_addr,
                "from_name": r.from_name,
                "to_addrs": r.to_addrs or [],
                "date_sent": r.date_sent.isoformat() if r.date_sent else None,
                "folder_path": r.folder_path,
                "alive_in_live": r.deleted_at is None,
                "snapshots": sorted(snap_by_msg.get((r.account_id, r.message_id_hash), [])),
                "body_matched": (r.message_id_hash in body_matched_set) if body else None,
            }
        )

    return {
        "results": results,
        "total": total,
        "page": page,
        "page_size": page_size,
        "phase2_skipped_count": phase2_skipped,
    }


def _dovecot_filter_body(
    db: Session,
    account_id: str,
    candidates: list[tuple[bytes, str, str, str]],
    keyword: str,
) -> set[bytes]:
    """Return the subset of candidate message_id_hash values whose body matches keyword.

    candidates: list of (hash, folder, maildir_filename, message_id) tuples
    Per-candidate: SEARCH HEADER Message-Id to get UID, then SEARCH UID BODY to confirm.
    Two SEARCH calls per candidate, bounded by Phase 1 cap.

    Errors here MUST NOT fail the whole search — return empty set on Dovecot failure.
    """
    if not candidates:
        return set()
    from mailfallback.models import Account
    from mailfallback.routers.restore import (
        _connect_dovecot_for_account,
        _sanitize_imap_string,
        account_namespace_prefix,
    )
    from mailfallback.services.dovecot_auth import delete_temp_imap_user

    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        return set()
    matched: set[bytes] = set()
    try:
        conn, temp_user = _connect_dovecot_for_account(db, account)
    except Exception:
        logger.warning("Phase 2: Dovecot connect failed for %s", account_id, exc_info=True)
        return set()
    try:
        ns = account_namespace_prefix(account)
        # Group candidates by folder for fewer SELECTs
        by_folder: dict[str, list[tuple[bytes, str]]] = {}
        msgid_by_hash: dict[bytes, str] = {h: msgid for h, _, _, msgid in candidates}
        for h, folder, filename, _msgid in candidates:
            by_folder.setdefault(folder, []).append((h, filename))
        for folder, items in by_folder.items():
            target = f'"{ns}{folder}"'
            typ, _ = conn.select(target, readonly=True)
            if typ != "OK":
                continue
            for h, _filename in items:
                msgid = msgid_by_hash.get(h)
                if not msgid:
                    continue
                quoted_id = _sanitize_imap_string(msgid)
                typ, data = conn.uid("SEARCH", "HEADER", "Message-Id", f'"{quoted_id}"')
                if typ != "OK" or not data or not data[0]:
                    continue
                uids = data[0].decode().split()
                if not uids:
                    continue
                uid = uids[0]
                quoted_kw = _sanitize_imap_string(keyword)
                typ, data = conn.uid("SEARCH", "UID", uid, "BODY", f'"{quoted_kw}"')
                if typ == "OK" and data and data[0] and uid in data[0].decode().split():
                    matched.add(h)
    finally:
        with contextlib.suppress(Exception):
            conn.logout()
        with contextlib.suppress(Exception):
            delete_temp_imap_user(db, temp_user)
    return matched
