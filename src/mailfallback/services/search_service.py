"""Search service — query mail_index with optional deep (body) search."""

import contextlib
import logging
import re
import time
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
    deep: bool = False,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    """Phase 1 (always): Postgres index query over subject/from/to.

    Deep search (deep=True): also run a full-folder Dovecot body search over the
    in-scope accounts' live folders and union the matches into the query via
    `message_id_hash IN body_hashes`. Live-only; bounded by a soft timeout that
    surfaces as `partial`.

    Returns: {results, total, page, page_size, partial}
    """
    empty = {"results": [], "total": 0, "page": page, "page_size": page_size, "partial": False}
    visible = _accessible_account_ids(db, user)
    if not visible:
        return empty
    scope = [a for a in account_ids if a in visible] if account_ids else visible
    if not scope:
        return empty

    body_hashes: set[bytes] = set()
    partial = False
    if deep and query:
        from mailfallback.config import settings

        deadline = time.monotonic() + getattr(settings, "deep_search_timeout_seconds", 10)
        body_hashes, partial = _dovecot_body_search(db, scope, query, deadline)

    q = db.query(MailIndexMessage).filter(MailIndexMessage.account_id.in_(scope))
    if not include_deleted:
        q = q.filter(MailIndexMessage.deleted_at.is_(None))
    # NULL date_sent is treated as "unknown date" and kept in the result set
    # for any range — otherwise messages whose Date: header didn't parse
    # disappear from any date-filtered search.
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
        if db.bind.dialect.name == "postgresql":
            text_match = MailIndexMessage.tsv.op("@@")(func.plainto_tsquery("simple", query))
        else:
            pat = f"%{query}%"
            text_match = (
                (MailIndexMessage.subject.ilike(pat))
                | (MailIndexMessage.from_addr.ilike(pat))
                | (MailIndexMessage.from_name.ilike(pat))
            )
        if body_hashes:
            q = q.filter(text_match | MailIndexMessage.message_id_hash.in_(body_hashes))
        else:
            q = q.filter(text_match)
    q = q.order_by(MailIndexMessage.date_sent.desc().nullslast())

    total = q.count()
    rows = q.offset((page - 1) * page_size).limit(page_size).all()

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
                "body_matched": (r.message_id_hash in body_hashes) if deep else None,
            }
        )

    return {
        "results": results,
        "total": total,
        "page": page,
        "page_size": page_size,
        "partial": partial,
    }


def _dovecot_body_search(
    db: Session,
    account_ids: list[str],
    keyword: str,
    deadline: float,
) -> tuple[set[bytes], bool]:
    """Full-folder Dovecot body search across the live folders of each account.

    Returns (matched_message_id_hashes, partial). `partial` is True when the
    monotonic `deadline` was reached before all folders were searched.

    Live-only: folders are taken from index rows with deleted_at IS NULL, so
    snapshot-only mail is never body-searched (Dovecot does not serve it).
    Errors per account/folder are swallowed — they must never fail the search.
    """
    from mailfallback.routers.restore import (
        _connect_dovecot_for_account,
        _sanitize_imap_string,
        account_namespace_prefix,
    )
    from mailfallback.services.dovecot_auth import delete_temp_imap_user
    from mailfallback.services.index_service import _hash_message_id

    matched: set[bytes] = set()
    quoted_kw = _sanitize_imap_string(keyword)
    if not quoted_kw:
        return matched, False

    for account_id in account_ids:
        if time.monotonic() > deadline:
            return matched, True
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            continue
        folders = [
            f[0]
            for f in db.query(MailIndexMessage.folder_path)
            .filter(
                MailIndexMessage.account_id == account_id,
                MailIndexMessage.deleted_at.is_(None),
            )
            .distinct()
            .all()
        ]
        if not folders:
            continue
        try:
            conn, temp_user = _connect_dovecot_for_account(db, account)
        except Exception:
            logger.warning("Deep search: Dovecot connect failed for %s", account_id, exc_info=True)
            continue
        try:
            ns = account_namespace_prefix(account)
            for folder in folders:
                if time.monotonic() > deadline:
                    return matched, True
                target = f'"{ns}{_sanitize_imap_string(folder)}"'
                typ, _ = conn.select(target, readonly=True)
                if typ != "OK":
                    continue
                typ, data = conn.uid("SEARCH", "BODY", f'"{quoted_kw}"')
                if typ != "OK" or not data or not data[0]:
                    continue
                uids = data[0].decode().split()
                if not uids:
                    continue
                for i in range(0, len(uids), 500):
                    batch = uids[i : i + 500]
                    typ, fdata = conn.uid(
                        "FETCH", ",".join(batch), "(BODY[HEADER.FIELDS (MESSAGE-ID)])"
                    )
                    if typ != "OK" or not fdata:
                        continue
                    for item in fdata:
                        msgid = _parse_message_id_from_fetch(item)
                        if msgid:
                            matched.add(_hash_message_id(msgid))
        finally:
            with contextlib.suppress(Exception):
                conn.logout()
            with contextlib.suppress(Exception):
                delete_temp_imap_user(db, temp_user)
    return matched, False


_MESSAGE_ID_RE = re.compile(rb"message-id:\s*(<[^>\r\n]*>)", re.IGNORECASE)


def _parse_message_id_from_fetch(item: Any) -> str | None:
    """Extract the bare Message-Id from one imaplib FETCH response item.

    Matched items are (metadata, payload) tuples; separators (e.g. b')') are not.
    """
    if not isinstance(item, tuple) or len(item) < 2:
        return None
    payload = item[1]
    if not isinstance(payload, (bytes, bytearray)):
        return None
    m = _MESSAGE_ID_RE.search(payload)
    return m.group(1).decode("ascii", errors="replace") if m else None
