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

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.dependencies import Principal, get_db, require_scope
from mailfallback.models import Account, MailIndexMessage
from mailfallback.routers.restore import RESOLVE_UIDS_MAX_IDS
from mailfallback.services import app_credential_service, preview_service, search_service
from mailfallback.services.sync_worker import submit_sync_job

logger = logging.getLogger(__name__)

# Documented-only: this scheme never runs any auth logic of its own
# (auto_error=False means it neither raises nor is required to return
# anything usable) — it exists solely so FastAPI records a bearer security
# scheme against every route in this router for /openapi.json, which is what
# makes /docs show this surface as authenticated and gives it an "Authorize"
# button. get_current_principal (via require_scope) remains the only thing
# that actually verifies a token; do not wire this scheme's return value into
# any route logic.
_bearer_scheme = HTTPBearer(auto_error=False)

router = APIRouter(prefix="/api/v1/agent", tags=["agent"], dependencies=[Depends(_bearer_scheme)])

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
    # True/False when the request had deep=true and this hit's body was (or
    # was not) part of the match; None when deep search was not requested, so
    # a caller can't mistake "we didn't check" for "it didn't match".
    body_matched: bool | None = None


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

    model_config = ConfigDict(extra="forbid")

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
    model_config = ConfigDict(extra="forbid")

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


_SYNC = require_scope(app_credential_service.SCOPE_SYNC_TRIGGER)

# Reuses the UI's cap rather than defining a second number — the underlying
# helper issues one serial UID SEARCH per id against one temp Dovecot user
# inside the request, with no deadline, so the limit is about how many
# sequential round trips a single request may hold a worker thread for, not
# about which caller is asking.
RESOLVE_COORDS_MAX_IDS = RESOLVE_UIDS_MAX_IDS


class ImapCoordsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str
    message_ids: list[str]


class ImapCoordsResponseOut(BaseModel):
    resolved: dict[str, list[str]]
    missing: list[str]
    # True only when Dovecot itself could not be reached: every id in
    # `missing` was never actually checked, as opposed to checked and not
    # found. A caller should retry rather than conclude the messages are gone.
    imap_unavailable: bool = False


class SyncJobOut(BaseModel):
    job_id: str
    account_id: str
    status: str
    source: str
    already_queued: bool = False
    requested_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failure_kind: str | None = None


@router.get("/messages/{account_id}/{message_id_hash}/attachments/{part_index}")
def download_attachment(
    account_id: str,
    message_id_hash: str,
    part_index: int,
    request: Request,
    principal: Principal = Depends(_READ),
    db: Session = Depends(get_db),
):
    """Raw attachment bytes.

    No response model: the body is the file. ALWAYS
    application/octet-stream plus nosniff — a hostile HTML or SVG attachment
    must download, never execute on our origin. Every download is audited,
    like the UI's equivalent.
    """
    from mailfallback.routers import restore
    from mailfallback.services.audit_service import log_action

    account = _agent_account(db, principal, account_id)
    msg_hash = _hash_from_hex(message_id_hash)
    payload, filename, source = restore.extract_attachment_bytes(db, account, msg_hash, part_index)
    log_action(
        db,
        user=principal.user,
        action="attachment.download",
        resource_type="attachment",
        resource_id=account.id,
        resource_name=filename,
        details={
            "message_id_hash": message_id_hash,
            "part_index": part_index,
            "source": source,
            "via": "agent_api",
        },
        ip_address=request.client.host if request.client else None,
    )
    return Response(
        content=payload,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": restore._attachment_disposition(filename),
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/imap-coords", response_model=ImapCoordsResponseOut)
def imap_coords(
    req: ImapCoordsRequest,
    principal: Principal = Depends(_READ),
    db: Session = Depends(get_db),
):
    """Message-Ids to live IMAP folder keys and UIDs.

    This is the bridge to the IMAP path: search here, then FETCH over IMAP with
    an existing client. Keys are namespace-prefixed exactly as Dovecot
    publishes them, so they can be SELECTed as-is. Ids beyond
    RESOLVE_COORDS_MAX_IDS are ignored entirely — neither resolved nor
    reported missing.

    Returns 200 with ``imap_unavailable: true`` on a Dovecot connect failure,
    unlike the UI's equivalent route (routers/restore.py:api_resolve_uids),
    which turns the same condition into a 502. That is deliberate, not
    inconsistent: this caller is a PROGRAM that can inspect the flag and
    retry, so a soft signal is more useful than an exception it would just
    have to catch and re-decode. The UI's caller is a person about to be
    handed a rendered sentence, who cannot tell "checked and gone" apart from
    "could not check" if both come back as success.
    """
    from mailfallback.routers import restore

    account = _agent_account(db, principal, req.account_id)
    return restore.resolve_uids_for_account(db, account, req.message_ids[:RESOLVE_COORDS_MAX_IDS])


@router.post("/sync/{account_id}", response_model=SyncJobOut)
def trigger_sync(
    account_id: str,
    request: Request,
    principal: Principal = Depends(_SYNC),
    db: Session = Depends(get_db),
):
    """Queue a sync. Idempotent in practice: if one is already pending or
    running, that job is returned with ``already_queued: true`` rather than an
    error, so a polling agent needs no special case for the ordinary state.

    Guards mirror routers/sync.py's single-account trigger exactly — same
    status code, same messages — with ONE deliberate divergence: there, a
    self-recovering pause (budget/throttle/transient) is a warn-and-override,
    because a human triggering it can weigh burning the provider's daily
    quota against getting fresh mail now. An agent cannot weigh that trade-off
    on the account owner's behalf, so here a pause is a plain refusal instead.
    Do not "fix" this to match the UI — it is intentional.
    """
    from mailfallback.models import JobStatus, SyncJob
    from mailfallback.services import sync_service
    from mailfallback.services.audit_service import log_action

    account = _agent_account(db, principal, account_id)

    if account.suspended:
        raise HTTPException(status_code=409, detail="Sync blocked: account is suspended")
    if account.migrating:
        raise HTTPException(status_code=409, detail="Sync blocked: account migration in progress")
    for owner in account.owners:
        if owner.migrating:
            raise HTTPException(status_code=409, detail="Sync blocked: user migration in progress")
    if account.sync_paused_until is not None or account.pause_reason is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Sync blocked: paused ({account.pause_reason or 'unknown'}); "
                "an agent cannot override a self-recovering pause"
            ),
        )

    job = sync_service.create_sync_job(db, account.id, source="agent")
    already = False
    if job is None:
        already = True
        job = (
            db.query(SyncJob)
            .filter(
                SyncJob.account_id == account.id,
                SyncJob.status.in_([JobStatus.pending, JobStatus.running]),
            )
            .order_by(SyncJob.requested_at.desc())
            .first()
        )
        if job is None:  # raced to completion between the two queries
            raise HTTPException(status_code=409, detail="Could not queue a sync; retry")
    else:
        # Only the newly-created-job branch submits — an already-running job
        # must never be resubmitted to the executor.
        submit_sync_job(job.id)
        log_action(
            db,
            user=principal.user,
            action="account.sync",
            resource_type="account",
            resource_id=account.id,
            resource_name=account.email_address,
            details={"via": "agent_api", "job_id": job.id},
            ip_address=request.client.host if request.client else None,
        )
    return SyncJobOut(
        job_id=job.id,
        account_id=job.account_id,
        status=job.status.value,
        source=job.source,
        already_queued=already,
        requested_at=job.requested_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        failure_kind=job.failure_kind,
    )


@router.get("/sync/jobs/{job_id}", response_model=SyncJobOut)
def sync_job_status(
    job_id: str,
    principal: Principal = Depends(_SYNC),
    db: Session = Depends(get_db),
):
    """Status of one sync job, scoped to the caller's own mailboxes."""
    from mailfallback.services import sync_service

    job = sync_service.get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    # 404 rather than 403: a job id must not confirm a mailbox the caller
    # cannot see. The detail text below is deliberately the SAME string as
    # the missing-job branch above — a distinct message would recreate the
    # oracle the status code is hiding, letting a caller tell "this id is
    # garbage" apart from "this id is a real job you cannot see" by wording
    # alone. Do not make these more specific again.
    try:
        _agent_account(db, principal, job.account_id)
    except HTTPException:
        raise HTTPException(status_code=404, detail="Job not found") from None
    return SyncJobOut(
        job_id=job.id,
        account_id=job.account_id,
        status=job.status.value,
        source=job.source,
        requested_at=job.requested_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        failure_kind=job.failure_kind,
    )
