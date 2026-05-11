"""Search service — query mail_index with optional Phase 2 body filter."""

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
    if range_start:
        q = q.filter(MailIndexMessage.date_sent >= range_start)
    if range_end:
        q = q.filter(MailIndexMessage.date_sent <= range_end)
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
                "body_matched": None,  # Phase 2 fills this in (Task 9)
            }
        )

    return {
        "results": results,
        "total": total,
        "page": page,
        "page_size": page_size,
        "phase2_skipped_count": 0,
    }
