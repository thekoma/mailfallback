"""Search service — query mail_index with optional deep (body) search."""

import contextlib
import logging
import re
import time
from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, literal_column, null, text
from sqlalchemy.orm import Query, Session

from mailfallback.models import (
    ATTACHMENTS_FTS_EXPR,
    Account,
    MailIndexAttachment,
    MailIndexMessage,
    SnapshotMessage,
    User,
    UserRole,
    account_groups,
    account_owners,
    group_members,
)

logger = logging.getLogger(__name__)

# ts_headline options for attachment content snippets. The [[[ / ]]] markers
# are a contract with the workspace JS: it splits on them and builds text
# nodes + <mark> elements — snippets are NEVER rendered as HTML (the content
# comes from hostile mail attachments).
ATTACHMENT_HEADLINE_OPTS = "StartSel=[[[,StopSel=]]],MaxWords=18,MinWords=8"


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
    include_all: bool = False,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    """Phase 1 (always): Postgres index query over subject/from/to.

    Deep search (deep=True): also run a full-folder Dovecot body search over the
    in-scope accounts' live folders and union the matches into the query via
    `message_id_hash IN body_hashes`. Live-only; bounded by a soft timeout that
    surfaces as `partial`.

    Privacy default: scope is the user's accessible accounts (ownership OR
    groups) — admins included. `include_all=True` widens an ADMIN's scope to
    every account; callers must audit that escalation (the API layer logs
    `restore.search_all`). Non-admins: include_all is ignored.

    Returns: {results, total, page, page_size, partial}
    """
    empty = {"results": [], "total": 0, "page": page, "page_size": page_size, "partial": False}
    visible = _accessible_account_ids(db, user)
    if include_all and user.role == UserRole.admin:
        visible = [a_id for (a_id,) in db.query(Account.id).all()]
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
        # Explicit columns, never the entity: content_text (Tika-extracted,
        # up to 200 KB per row) must not be dragged into every search page.
        att_rows = (
            db.query(
                MailIndexAttachment.account_id,
                MailIndexAttachment.message_id_hash,
                MailIndexAttachment.filename,
                MailIndexAttachment.ext,
                MailIndexAttachment.size_bytes,
            )
            .filter(
                MailIndexAttachment.account_id.in_(scope),
                MailIndexAttachment.message_id_hash.in_(hashes),
            )
            .order_by(MailIndexAttachment.part_index)
            .all()
        )
        # part_index order = MIME order, so UI chips match the message layout
        atts_by_msg: dict[tuple[str, bytes], list[dict[str, Any]]] = {}
        for acc_id, msg_hash, filename, ext, size_bytes in att_rows:
            atts_by_msg.setdefault((acc_id, msg_hash), []).append(
                {"filename": filename, "ext": ext, "size_bytes": size_bytes}
            )
    else:
        snap_by_msg = {}
        atts_by_msg = {}

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
                "message_id_hash": r.message_id_hash.hex(),
                "has_attachments": r.has_attachments,
                "attachments": atts_by_msg.get((r.account_id, r.message_id_hash), []),
            }
        )

    return {
        "results": results,
        "total": total,
        "page": page,
        "page_size": page_size,
        "partial": partial,
    }


def _build_attachment_query(
    db: Session,
    *,
    scope: list[str],
    query: str,
    content_mode: bool,
    exts: list[str] | None,
    min_size: int | None,
    max_size: int | None,
    dialect_name: str,
    range_start: datetime | None = None,
    range_end: datetime | None = None,
) -> Query:
    """Build the attachment search query: explicit columns, JOIN to messages.

    `dialect_name` is a parameter instead of being read off `db` so tests can
    compile the PostgreSQL variant of the statement without a PG server.
    """
    terms = query.split()
    if content_mode and terms and dialect_name == "postgresql":
        # Inline regconfig literal: SQLAlchemy types a plain "simple" string
        # as REGCONFIG, which has no literal renderer (breaks literal_binds
        # compiles) — and the inline form matches the PG-docs style anyway.
        regconfig = literal_column("'simple'")
        ts_query = func.plainto_tsquery(regconfig, query)
        snippet_col = func.ts_headline(
            regconfig,
            func.coalesce(MailIndexAttachment.content_text, ""),
            ts_query,
            ATTACHMENT_HEADLINE_OPTS,
        ).label("content_snippet")
        # Filename-only matches must not get a headline (it would render the
        # first words of unrelated content) — track content hits explicitly.
        content_matched_col = (
            func.to_tsvector(regconfig, func.coalesce(MailIndexAttachment.content_text, ""))
            .op("@@")(ts_query)
            .label("content_matched")
        )
    else:
        # Also hit with content_mode + empty query (the default UI state once
        # the content toggle lands): no per-row ts_headline/to_tsvector cost
        # when there is nothing to highlight.
        snippet_col = null().label("content_snippet")
        content_matched_col = null().label("content_matched")

    q = (
        db.query(
            MailIndexAttachment.account_id,
            MailIndexAttachment.message_id_hash,
            MailIndexAttachment.part_index,
            MailIndexAttachment.filename,
            MailIndexAttachment.ext,
            MailIndexAttachment.size_bytes,
            MailIndexMessage.subject,
            MailIndexMessage.from_addr,
            MailIndexMessage.folder_path,
            MailIndexMessage.date_sent,
            MailIndexMessage.deleted_at,
            snippet_col,
            content_matched_col,
        )
        .join(
            MailIndexMessage,
            (MailIndexMessage.account_id == MailIndexAttachment.account_id)
            & (MailIndexMessage.message_id_hash == MailIndexAttachment.message_id_hash),
        )
        .filter(MailIndexAttachment.account_id.in_(scope))
    )

    if terms:
        if content_mode and dialect_name == "postgresql":
            # PG matches expression indexes structurally: the WHERE clause
            # must use models.ATTACHMENTS_FTS_EXPR verbatim or the GIN index
            # (idx_attachments_fts) degrades to a seq scan.
            match = text(f"{ATTACHMENTS_FTS_EXPR} @@ plainto_tsquery('simple', :fts_q)").bindparams(
                fts_q=query
            )
        elif content_mode:
            # SQLite fallback: every term must hit filename OR content_text.
            match = and_(
                *(
                    MailIndexAttachment.filename.ilike(f"%{t}%")
                    | MailIndexAttachment.content_text.ilike(f"%{t}%")
                    for t in terms
                )
            )
        else:
            match = and_(*(MailIndexAttachment.filename.ilike(f"%{t}%") for t in terms))
        q = q.filter(match)

    if exts:
        q = q.filter(MailIndexAttachment.ext.in_([e.lower().lstrip(".") for e in exts]))
    # NULL size_bytes is excluded by SQL comparison semantics when a size
    # filter is set — and included when no size filter is given.
    if min_size is not None:
        q = q.filter(MailIndexAttachment.size_bytes >= min_size)
    if max_size is not None:
        q = q.filter(MailIndexAttachment.size_bytes <= max_size)
    # Date range on the JOINED message — NULL date_sent is "unknown date" and
    # stays in the result set for any range (the search_messages semantics:
    # a message whose Date: header didn't parse must not disappear).
    if range_start:
        q = q.filter(
            (MailIndexMessage.date_sent >= range_start) | MailIndexMessage.date_sent.is_(None)
        )
    if range_end:
        q = q.filter(
            (MailIndexMessage.date_sent <= range_end) | MailIndexMessage.date_sent.is_(None)
        )

    return q.order_by(
        MailIndexMessage.date_sent.desc().nullslast(),
        MailIndexAttachment.part_index,
    )


def search_attachments(
    db: Session,
    *,
    user: User,
    query: str = "",
    account_ids: list[str] | None = None,
    include_all: bool = False,
    exts: list[str] | None = None,
    min_size: int | None = None,
    max_size: int | None = None,
    include_content: bool = False,
    range_start: datetime | None = None,
    range_end: datetime | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    """Search attachment rows by filename — and by extracted content when
    `include_content` is set AND Tika is enabled (otherwise filename-only).

    `range_start`/`range_end` filter on the containing message's date_sent,
    NULL-tolerant like `search_messages` (unknown dates kept in any range).

    Scope rules are identical to `search_messages`: accessible accounts
    (ownership OR groups) for everyone; `include_all=True` widens an ADMIN's
    scope to every account and callers must audit that escalation. Non-admins:
    include_all is ignored.

    Returns: {results, total, page, page_size}
    """
    from mailfallback.config import settings

    empty = {"results": [], "total": 0, "page": page, "page_size": page_size}
    visible = _accessible_account_ids(db, user)
    if include_all and user.role == UserRole.admin:
        visible = [a_id for (a_id,) in db.query(Account.id).all()]
    if not visible:
        return empty
    scope = [a for a in account_ids if a in visible] if account_ids else visible
    if not scope:
        return empty

    content_mode = include_content and settings.tika_enabled
    q = _build_attachment_query(
        db,
        scope=scope,
        query=query,
        content_mode=content_mode,
        exts=exts,
        min_size=min_size,
        max_size=max_size,
        dialect_name=db.bind.dialect.name,
        range_start=range_start,
        range_end=range_end,
    )
    total = q.count()
    rows = q.offset((page - 1) * page_size).limit(page_size).all()

    snap_by_msg: dict[tuple[str, bytes], list[str]] = {}
    if rows:
        hashes = list({r.message_id_hash for r in rows})
        snap_rows = (
            db.query(
                SnapshotMessage.account_id,
                SnapshotMessage.message_id_hash,
                SnapshotMessage.snapshot_id,
            )
            .filter(SnapshotMessage.message_id_hash.in_(hashes))
            .all()
        )
        for acc, h, sid in snap_rows:
            snap_by_msg.setdefault((acc, h), []).append(sid)

    results = []
    for r in rows:
        alive = r.deleted_at is None
        snapshots = sorted(snap_by_msg.get((r.account_id, r.message_id_hash), []))
        results.append(
            {
                "account_id": r.account_id,
                "message_id_hash": r.message_id_hash.hex(),
                "part_index": r.part_index,
                "filename": r.filename,
                "ext": r.ext,
                "size_bytes": r.size_bytes,
                "content_snippet": r.content_snippet if r.content_matched else None,
                "subject": r.subject,
                "from_addr": r.from_addr,
                "folder_path": r.folder_path,
                "date_sent": r.date_sent.isoformat() if r.date_sent else None,
                "alive_in_live": alive,
                "snapshots": snapshots,
                "has_live_or_snapshot": alive or bool(snapshots),
            }
        )

    return {"results": results, "total": total, "page": page, "page_size": page_size}


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
                try:
                    target = f'"{ns}{_sanitize_imap_string(folder)}"'
                    typ, _ = conn.select(target, readonly=True)
                    if typ != "OK":
                        continue
                    if quoted_kw.isascii():
                        typ, data = conn.uid("SEARCH", "BODY", f'"{quoted_kw}"')
                    else:
                        # imaplib encodes command args as ASCII; a non-ASCII
                        # keyword must go as a UTF-8 literal with an explicit
                        # CHARSET (RFC 3501 §6.4.4).
                        conn.literal = quoted_kw.encode("utf-8")
                        typ, data = conn.uid("SEARCH", "CHARSET", "UTF-8", "BODY")
                    if typ != "OK" or not data or not data[0]:
                        continue
                    uids = data[0].decode().split()
                    if not uids:
                        continue
                    for i in range(0, len(uids), 500):
                        if time.monotonic() > deadline:
                            return matched, True
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
                except Exception:
                    logger.warning(
                        "Deep search: folder search failed for %s in %s",
                        folder,
                        account_id,
                        exc_info=True,
                    )
                    continue
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
