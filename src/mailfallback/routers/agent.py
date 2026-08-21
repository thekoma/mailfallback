# src/mailfallback/routers/agent.py
"""The agent-facing API: /api/v1/agent.

This is the only versioned, externally contracted surface in the codebase.
Everything under /api/restore is the UI's private contract and is deliberately
not reused here — its payloads follow HTMX form semantics and its names follow
screens rather than capabilities.

Two invariants hold for every route in this file:

- **No admin escalation.** ``include_all`` does not appear in any request model
  and is never passed to a service as anything but False. An agent sees its
  user's mailboxes and nothing else, whatever role that user holds.
- **Declared response models.** The services underneath return internal dicts
  whose shape follows internal needs; forwarding them verbatim would leak that
  drift to external clients on every refactor. The model is the contract.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.dependencies import Principal, get_db, require_scope
from mailfallback.models import Account, MailIndexMessage
from mailfallback.services import app_credential_service, preview_service, search_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])

_READ = require_scope(app_credential_service.SCOPE_MAIL_READ)


class MailboxOut(BaseModel):
    account_id: str
    name: str
    email_address: str | None = None
    provider: str | None = None
    last_sync_at: datetime | None = None
    indexed_messages: int
    folders: list[str]


class MessageAttachmentOut(BaseModel):
    filename: str | None = None
    ext: str | None = None
    size_bytes: int | None = None
    part_index: int


class SearchHitOut(BaseModel):
    account_id: str
    message_id: str
    message_id_hash: str
    subject: str | None = None
    from_addr: str | None = None
    from_name: str | None = None
    to_addrs: list[str] = Field(default_factory=list)
    date_sent: datetime | None = None
    folder_path: str
    alive_in_live: bool
    snapshots: list[str] = Field(default_factory=list)
    has_attachments: bool = False
    attachments: list[MessageAttachmentOut] = Field(default_factory=list)


class SearchResponseOut(BaseModel):
    results: list[SearchHitOut]
    total: int
    page: int
    page_size: int
    partial: bool


class SearchAttachmentOut(BaseModel):
    account_id: str
    message_id: str
    message_id_hash: str
    part_index: int
    filename: str | None = None
    ext: str | None = None
    size_bytes: int | None = None
    content_snippet: str | None = None
    subject: str | None = None
    from_addr: str | None = None
    folder_path: str
    date_sent: datetime | None = None
    alive_in_live: bool
    snapshots: list[str] = Field(default_factory=list)


class AttachmentSearchResponseOut(BaseModel):
    results: list[SearchAttachmentOut]
    total: int
    page: int
    page_size: int
    content_search_available: bool


class MessageOut(BaseModel):
    subject: str | None = None
    from_addr: str | None = None
    from_name: str | None = None
    to_addrs: list[str] = Field(default_factory=list)
    date_sent: datetime | None = None
    folder_path: str
    alive_in_live: bool
    source: str
    body_snippet: str
    attachments: list[MessageAttachmentOut] = Field(default_factory=list)


class SearchRequest(BaseModel):
    """Note the absence of include_all — see the module docstring."""

    query: str = ""
    account_ids: list[str] | None = None
    range_start: datetime | None = None
    range_end: datetime | None = None
    include_deleted: bool = True
    snapshot_id: str | None = None
    deep: bool = False
    page: int = Field(1, ge=1)
    page_size: int = Field(50, ge=1, le=200)


class AttachmentSearchRequest(BaseModel):
    query: str = ""
    account_ids: list[str] | None = None
    exts: list[str] | None = None
    min_size: int | None = None
    max_size: int | None = None
    include_content: bool = False
    range_start: datetime | None = None
    range_end: datetime | None = None
    page: int = Field(1, ge=1)
    page_size: int = Field(50, ge=1, le=200)


def _agent_account(db: Session, principal: Principal, account_id: str) -> Account:
    """The account, or 404 — never 403.

    A 403 would confirm the account exists to a caller who cannot see it. This
    is deliberately NOT restore._workspace_account_for_user: that helper takes
    an include_all escalation parameter, which must not exist on this surface.
    """
    if account_id not in search_service._accessible_account_ids(db, principal.user):
        raise HTTPException(status_code=404, detail="Mailbox not found")
    account = db.query(Account).filter(Account.id == account_id).first()
    if account is None:
        raise HTTPException(status_code=404, detail="Mailbox not found")
    return account


def _hash_from_hex(value: str) -> bytes:
    try:
        return bytes.fromhex(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid message_id_hash") from None


@router.get("/mailboxes", response_model=list[MailboxOut])
def list_mailboxes(principal: Principal = Depends(_READ), db: Session = Depends(get_db)):
    """The mailboxes this caller can search, with what is indexed in each.

    ``indexed_messages`` and ``folders`` come from the index rather than the
    Account row — they answer "what can I actually search here", which a
    provider-side message count would not.
    """
    visible = search_service._accessible_account_ids(db, principal.user)
    if not visible:
        return []
    accounts = db.query(Account).filter(Account.id.in_(visible)).all()

    counts = dict(
        db.query(MailIndexMessage.account_id, func.count())
        .filter(MailIndexMessage.account_id.in_(visible))
        .filter(MailIndexMessage.deleted_at.is_(None))
        .group_by(MailIndexMessage.account_id)
        .all()
    )
    folders: dict[str, list[str]] = {}
    for account_id, folder in (
        db.query(MailIndexMessage.account_id, MailIndexMessage.folder_path)
        .filter(MailIndexMessage.account_id.in_(visible))
        .filter(MailIndexMessage.deleted_at.is_(None))
        .distinct()
        .all()
    ):
        folders.setdefault(account_id, []).append(folder)

    return [
        MailboxOut(
            account_id=a.id,
            name=a.name,
            email_address=a.email_address,
            provider=a.provider,
            last_sync_at=a.last_sync_at,
            indexed_messages=counts.get(a.id, 0),
            folders=sorted(folders.get(a.id, [])),
        )
        for a in accounts
    ]


@router.post("/search", response_model=SearchResponseOut)
def search(
    req: SearchRequest,
    principal: Principal = Depends(_READ),
    db: Session = Depends(get_db),
):
    """Indexed search across every mailbox the caller can see.

    ``deep=true`` additionally runs a Dovecot body search over live folders,
    bounded by ``deep_search_timeout_seconds``; a timeout surfaces as
    ``partial: true`` rather than an error or a silent truncation.
    """
    return search_service.search_messages(
        db,
        user=principal.user,
        query=req.query,
        account_ids=req.account_ids,
        range_start=req.range_start,
        range_end=req.range_end,
        include_deleted=req.include_deleted,
        snapshot_id=req.snapshot_id,
        deep=req.deep,
        include_all=False,
        page=req.page,
        page_size=req.page_size,
    )


@router.post("/search-attachments", response_model=AttachmentSearchResponseOut)
def search_attachments(
    req: AttachmentSearchRequest,
    principal: Principal = Depends(_READ),
    db: Session = Depends(get_db),
):
    """Search attachments by filename, and by extracted text when Tika is on.

    ``content_search_available`` tells the caller whether ``include_content``
    means anything right now — without it an agent cannot distinguish "no
    matches" from "content search is switched off".
    """
    result = search_service.search_attachments(
        db,
        user=principal.user,
        query=req.query,
        account_ids=req.account_ids,
        include_all=False,
        exts=req.exts,
        min_size=req.min_size,
        max_size=req.max_size,
        include_content=req.include_content,
        range_start=req.range_start,
        range_end=req.range_end,
        page=req.page,
        page_size=req.page_size,
    )
    result["content_search_available"] = settings.tika_enabled
    return result


@router.get("/messages/{account_id}/{message_id_hash}", response_model=MessageOut)
def get_message(
    account_id: str,
    message_id_hash: str,
    principal: Principal = Depends(_READ),
    db: Session = Depends(get_db),
):
    """Headers, a body snippet and the attachment list for one message.

    Served from the live Maildir while the message is alive there, otherwise
    from the newest snapshot that holds it — ``source`` says which.
    """
    account = _agent_account(db, principal, account_id)
    out = preview_service.get_preview(db, account, _hash_from_hex(message_id_hash))
    if out is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return out
