# src/mailfallback/mcp_server.py
"""The MCP server: eight tools over the same services the REST API uses.

Mounted at /mcp over streamable HTTP and authenticated with the same access
token as IMAP and the REST API. Three properties of this file are load-bearing:

- **Every tool is declared ``def``, never ``async def``.** The SDK runs a sync
  tool in a worker thread and an async tool on the event loop; these tools do
  blocking SQLAlchemy I/O, so an async one would stall the whole application's
  event loop under load — a fault that passes every test and only appears in
  production.
- **Tools authorise by reading the verified token's scopes**, the same
  ``mail:read`` / ``sync:trigger`` split the REST surface enforces with
  ``require_scope``. There is no MCP-specific permission model.
- **Each tool owns its database session** and closes it. Nothing here runs
  inside FastAPI's dependency injection.
"""

import base64
import logging
from contextlib import contextmanager
from datetime import datetime
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.routes import create_protected_resource_routes
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_REQUEST
from pydantic import AnyHttpUrl, BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session
from starlette.applications import Starlette
from starlette.routing import Route

from mailfallback.config import settings as _settings
from mailfallback.db import SessionLocal
from mailfallback.mcp_auth import MfbTokenVerifier
from mailfallback.models import (
    Account,
    JobStatus,
    MailIndexAttachment,
    MailIndexMessage,
    SyncJob,
    User,
)
from mailfallback.routers.agent import (
    RESOLVE_COORDS_MAX_IDS,
    AttachmentSearchResponseOut,
    ImapCoordsResponseOut,
    MailboxOut,
    MessageOut,
    SearchResponseOut,
    SyncJobOut,
)
from mailfallback.services import (
    app_credential_service,
    preview_service,
    search_service,
    sync_service,
)
from mailfallback.services.audit_service import log_action
from mailfallback.services.sync_worker import submit_sync_job
from mailfallback.version import __version__

logger = logging.getLogger(__name__)

MCP_PATH = "/mcp"

# Base64 inflates by a third and the whole result travels inside one
# JSON-RPC response, so the raw cap must sit well under the transport's own
# limits. Hitting it does not dead-end the caller — the error names the
# message's folder and points at imap_coords + a live IMAP fetch instead.
MCP_ATTACHMENT_MAX_BYTES = 5 * 1024 * 1024


class MailboxListOut(BaseModel):
    """``list_mailboxes``' envelope — REST returns a bare list, but every MCP
    tool here returns a mapping, so this wraps ``MailboxOut`` the same way the
    tool body already does."""

    mailboxes: list[MailboxOut]


class AttachmentDownloadOut(BaseModel):
    """``download_attachment``'s envelope. No REST counterpart to reuse: the
    REST route streams raw bytes instead of returning JSON."""

    filename: str | None = None
    size_bytes: int
    source: str
    content_base64: str


def _require_scope(scope: str) -> str:
    """Return the caller's MFB user id, or raise if the scope is missing.

    Mirrors dependencies.require_scope for the MCP surface. "Not authenticated"
    is the one genuinely protocol-level condition here, so it alone is raised
    as ``MCPError`` — the SDK re-raises that as a top-level JSON-RPC error. A
    missing scope is a tool-EXECUTION failure, not a malformed request: it is
    raised as a plain exception so the SDK instead delivers it as an
    ``is_error`` ``CallToolResult``, the path a model is guaranteed to read.
    """
    token = get_access_token()
    if token is None:  # pragma: no cover — the middleware refuses first
        raise MCPError(code=INVALID_REQUEST, message="Not authenticated")
    if scope not in token.scopes:
        raise PermissionError(f"This access token lacks the {scope} scope")
    return token.subject


def build_mcp_server(settings) -> MCPServer | None:
    """The configured server, or None when MCP is switched off.

    ``token_verifier`` and ``auth`` must be given together or the SDK raises.
    ``issuer_url`` is required even though MFB is not an OAuth issuer: it is set
    to MFB's own public URL, which is honest about who serves the resource and
    is what a client sees in the protected-resource metadata.
    """
    if not settings.mcp_enabled:
        return None
    if not settings.mcp_public_url:
        logger.error("MAILFALLBACK_MCP_ENABLED is set but MCP_PUBLIC_URL is empty; MCP is off")
        return None

    base = settings.mcp_public_url.rstrip("/")
    return MCPServer(
        "MailFallBack",
        version=__version__,
        instructions=(
            "Read-only access to the mailboxes this token's owner can see. "
            "Search across mailboxes, read messages and attachments, resolve "
            "IMAP coordinates, and trigger a sync."
        ),
        token_verifier=MfbTokenVerifier(),
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(base),
            resource_server_url=AnyHttpUrl(f"{base}{MCP_PATH}"),
            # Authentication only; each tool checks its own scope, so a
            # sync-only token must still be able to reach sync_now.
            required_scopes=[],
        ),
    )


def mcp_asgi_app(server: MCPServer, settings) -> Starlette:
    """The ASGI app to mount at MCP_PATH.

    ``streamable_http_path="/"`` because the app is mounted AT /mcp — leaving
    the default would serve /mcp/mcp. The host allowlist is not optional: the
    SDK's DNS-rebinding protection rejects anything not addressed to localhost
    with 421 or 403, so a deployment behind a real hostname must list it.
    """
    from urllib.parse import urlparse

    host = urlparse(settings.mcp_public_url).netloc or "localhost"
    bare = host.split(":", 1)[0]
    return server.streamable_http_app(
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=settings.mcp_dns_rebinding_protection,
            allowed_hosts=[host, f"{bare}:*", "localhost", "localhost:*", "127.0.0.1:*"],
            allowed_origins=[settings.mcp_public_url.rstrip("/")],
        ),
    )


def protected_resource_metadata_routes(server: MCPServer) -> list[Route]:
    """The RFC 9728 protected-resource-metadata route, registered at the
    FastAPI ROOT app rather than only inside the mounted ``/mcp`` app.

    ``resource_server_url`` is ``{base}/mcp``, so the SDK computes the
    metadata URL it puts in ``WWW-Authenticate`` as root-relative:
    ``{base}/.well-known/oauth-protected-resource/mcp`` (RFC 9728 §3.1 inserts
    the well-known segment right after the host, not after the resource
    path). But the mounted app registers this same route relative to ITS OWN
    root, so left alone it only actually answers at
    ``{base}/mcp/.well-known/oauth-protected-resource/mcp`` — a 404 at the
    URL the server itself advertises. Re-registering the SDK's own route
    generator here (rather than hand-writing the JSON) keeps the content
    whatever the SDK would have served, and answers at the URL that is
    actually advertised.

    Takes the already-built ``server`` rather than ``settings`` so this
    reads the SAME ``AuthSettings`` the mounted app's own copy of this route
    was built from (``build_mcp_server``'s ``AuthSettings``). Re-deriving
    ``resource_url``/``authorization_servers``/``scopes_supported`` from
    ``settings`` a second time would agree today only by coincidence — change
    ``required_scopes`` or ``issuer_url`` there and the root route would
    silently diverge from the nested one.
    """
    auth = server.settings.auth
    if auth is None or auth.resource_server_url is None:
        raise ValueError(
            "protected_resource_metadata_routes requires a server built with auth configured"
        )
    return create_protected_resource_routes(
        resource_url=auth.resource_server_url,
        authorization_servers=[auth.issuer_url],
        scopes_supported=auth.required_scopes or [],
    )


_server: MCPServer | None = None
_asgi_app: Starlette | None = None


def get_server(settings) -> MCPServer | None:
    """Build the server once and register the tools on it."""
    global _server
    if _server is None:
        _server = build_mcp_server(settings)
        if _server is not None:
            _register_tools(_server)
    return _server


def get_asgi_app(settings) -> Starlette | None:
    """The ASGI app to mount at MCP_PATH, built at most once per process.

    ``MCPServer.streamable_http_app()`` allocates a NEW
    ``StreamableHTTPSessionManager`` on every call and reassigns it onto the
    lowlevel server, while the Starlette app it returns closes over that one
    specific manager instance, whose ``run()`` is single-use. A second call
    in the same process would leave an earlier app's mount pointing at a
    manager whose ``run()`` never executes. Caching here means correctness
    does not depend on how many times ``create_app()`` is called.
    """
    global _asgi_app
    server = get_server(settings)
    if server is None:
        return None
    if _asgi_app is None:
        _asgi_app = mcp_asgi_app(server, settings)
    return _asgi_app


@contextmanager
def _caller(scope: str):
    """Yield (db, user) for a tool call, or raise if the scope is missing.

    Not FastAPI's dependency injection: MCP tools run outside it, so each tool
    owns its session and this is where it gets closed.
    """
    user_id = _require_scope(scope)
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if user is None:  # pragma: no cover — the token verified moments ago
            raise LookupError("Caller no longer exists")
        yield db, user
    finally:
        db.close()


class _NoAccess(LookupError):
    """Raised by ``_mcp_account`` when the caller cannot see this account.

    A dedicated subclass rather than a plain ``LookupError``: ``sync_status``
    below needs to catch exactly this "no access" condition and re-word it,
    but ``KeyError``/``IndexError`` are ALSO ``LookupError`` subclasses — a
    bare ``except LookupError`` there would relabel a genuine bug raised
    inside ``_mcp_account`` (or ``_accessible_account_ids``) as "No such
    job" instead of surfacing it. Catching this subclass instead means only
    the condition this class documents gets swallowed.
    """


def _mcp_account(db: Session, user: User, account_id: str) -> Account:
    """The account, or an error that does not reveal whether it exists.

    A plain exception, not ``MCPError``: this is a tool-execution failure the
    SDK must deliver as an ``is_error`` result, not a protocol-level error.
    """
    if account_id not in search_service._accessible_account_ids(db, user):
        raise _NoAccess("No such mailbox")
    account = db.query(Account).filter(Account.id == account_id).first()
    if account is None:
        raise _NoAccess("No such mailbox")
    return account


def _mcp_hash(value: str) -> bytes:
    """A message_id_hash argument as bytes, or an error naming the field."""
    try:
        return bytes.fromhex(value)
    except ValueError:
        raise ValueError("Invalid message_id_hash") from None


def _attachment_cap_error(
    db: Session, account: Account, msg_hash: bytes, size_bytes: int
) -> ValueError:
    """The over-cap error for one attachment: names its folder, points at imap_coords.

    Shared by both cap checks in ``download_attachment`` so the wording (and
    the folder lookup) does not drift between the cheap early exit and the
    authoritative post-extraction one. A plain ``ValueError``, not
    ``MCPError``: this message's entire purpose is to redirect the calling
    model to ``imap_coords``, which only happens if it arrives as an
    ``is_error`` result's text — a top-level JSON-RPC error is not
    guaranteed to reach the model at all.
    """
    msg_row = (
        db.query(MailIndexMessage)
        .filter(
            MailIndexMessage.account_id == account.id,
            MailIndexMessage.message_id_hash == msg_hash,
        )
        .first()
    )
    folder = msg_row.folder_path if msg_row else "unknown"
    return ValueError(
        f"Attachment too large to return over MCP ({size_bytes} bytes, "
        f"cap is {MCP_ATTACHMENT_MAX_BYTES}). It lives in {folder!r} — "
        "resolve IMAP coordinates with imap_coords and fetch it over IMAP."
    )


def _register_tools(mcp: MCPServer) -> None:
    from mcp.types import ToolAnnotations

    _READ_ONLY = ToolAnnotations(read_only_hint=True)

    @mcp.tool(annotations=_READ_ONLY)
    def list_mailboxes() -> MailboxListOut:
        """The mailboxes this token's owner can search, with what is indexed in each.

        ``indexed_messages`` and ``folders`` describe what is in the search
        index right now, not a live provider-side message count — that is
        the question this tool answers: what can be searched here.
        """
        with _caller(app_credential_service.SCOPE_MAIL_READ) as (db, user):
            visible = search_service._accessible_account_ids(db, user)
            if not visible:
                return MailboxListOut(mailboxes=[])
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

            return MailboxListOut.model_validate(
                {
                    "mailboxes": [
                        {
                            "account_id": a.id,
                            "name": a.name,
                            "email_address": a.email_address,
                            "provider": a.provider,
                            "last_sync_at": a.last_sync_at.isoformat() if a.last_sync_at else None,
                            "indexed_messages": counts.get(a.id, 0),
                            "folders": sorted(folders.get(a.id, [])),
                        }
                        for a in accounts
                    ]
                }
            )

    @mcp.tool(annotations=_READ_ONLY)
    def search_mail(
        query: str = "",
        account_ids: list[str] | None = None,
        range_start: datetime | None = None,
        range_end: datetime | None = None,
        include_deleted: bool = True,
        snapshot_id: str | None = None,
        deep: bool = False,
        page: Annotated[int, Field(ge=1)] = 1,
        page_size: Annotated[int, Field(ge=1, le=200)] = 50,
    ) -> SearchResponseOut:
        """Indexed search across every mailbox this token's owner can see.

        A hit's ``message_id_hash`` plus an attachment's ``part_index`` are
        what ``download_attachment`` takes to fetch that attachment's bytes.
        ``deep=true`` additionally runs a Dovecot body search over live
        folders; when that search times out the response comes back with
        ``partial: true`` rather than an error — it means the results may be
        incomplete, not that nothing matched.
        """
        with _caller(app_credential_service.SCOPE_MAIL_READ) as (db, user):
            return SearchResponseOut.model_validate(
                search_service.search_messages(
                    db,
                    user=user,
                    query=query,
                    account_ids=account_ids,
                    range_start=range_start,
                    range_end=range_end,
                    include_deleted=include_deleted,
                    snapshot_id=snapshot_id,
                    deep=deep,
                    include_all=False,
                    page=page,
                    page_size=page_size,
                )
            )

    @mcp.tool(annotations=_READ_ONLY)
    def search_attachments(
        query: str = "",
        account_ids: list[str] | None = None,
        exts: list[str] | None = None,
        min_size: int | None = None,
        max_size: int | None = None,
        include_content: bool = False,
        range_start: datetime | None = None,
        range_end: datetime | None = None,
        page: Annotated[int, Field(ge=1)] = 1,
        page_size: Annotated[int, Field(ge=1, le=200)] = 50,
    ) -> AttachmentSearchResponseOut:
        """Search attachments by filename, and by extracted content when
        ``include_content`` is set and content search is enabled.

        A hit's ``message_id_hash`` plus its ``part_index`` are what
        ``download_attachment`` takes to fetch that attachment's bytes.
        ``content_search_available`` says whether ``include_content`` means
        anything right now — without it there is no way to tell "no matches"
        apart from "content search is switched off".
        """
        with _caller(app_credential_service.SCOPE_MAIL_READ) as (db, user):
            result = search_service.search_attachments(
                db,
                user=user,
                query=query,
                account_ids=account_ids,
                include_all=False,
                exts=exts,
                min_size=min_size,
                max_size=max_size,
                include_content=include_content,
                range_start=range_start,
                range_end=range_end,
                page=page,
                page_size=page_size,
            )
            result["content_search_available"] = _settings.tika_enabled
            return AttachmentSearchResponseOut.model_validate(result)

    @mcp.tool(annotations=_READ_ONLY)
    def get_message(account_id: str, message_id_hash: str) -> MessageOut:
        """Headers, a body snippet and the attachment list for one message.

        ``account_id`` and ``message_id_hash`` are the pair a search hit
        returns. Served from the live Maildir while the message is alive
        there, otherwise from the newest snapshot that holds it — ``source``
        says which.
        """
        with _caller(app_credential_service.SCOPE_MAIL_READ) as (db, user):
            account = _mcp_account(db, user, account_id)
            out = preview_service.get_preview(db, account, _mcp_hash(message_id_hash))
            if out is None:
                raise LookupError("Message not found")
            return MessageOut.model_validate(out)

    @mcp.tool(annotations=_READ_ONLY)
    def download_attachment(
        account_id: str, message_id_hash: str, part_index: int
    ) -> AttachmentDownloadOut:
        """One attachment's bytes, base64-encoded.

        ``account_id``, ``message_id_hash`` and ``part_index`` are the triple
        a search hit returns. A part over ``MCP_ATTACHMENT_MAX_BYTES`` is
        refused rather than served: resolve IMAP coordinates for the message
        with ``imap_coords`` and fetch it over IMAP instead.
        """
        from mailfallback.routers import restore

        with _caller(app_credential_service.SCOPE_MAIL_READ) as (db, user):
            account = _mcp_account(db, user, account_id)
            msg_hash = _mcp_hash(message_id_hash)

            # The index's size_bytes is a HINT, not authority: it can be
            # stale or NULL (e.g. re-synced since indexing). It is cheap
            # enough to check first because it saves a multi-MB disk (or
            # restic snapshot) read for anything already known to be over
            # cap. The post-extraction length check below is the real
            # gate and must stay — deleting it as "redundant" would let a
            # stale/missing index row smuggle an oversized part past the
            # cap.
            att = (
                db.query(MailIndexAttachment)
                .filter(
                    MailIndexAttachment.account_id == account.id,
                    MailIndexAttachment.message_id_hash == msg_hash,
                    MailIndexAttachment.part_index == part_index,
                )
                .first()
            )
            if (
                att is not None
                and att.size_bytes is not None
                and att.size_bytes > MCP_ATTACHMENT_MAX_BYTES
            ):
                raise _attachment_cap_error(db, account, msg_hash, att.size_bytes)

            payload, filename, source = restore.extract_attachment_bytes(
                db, account, msg_hash, part_index
            )
            if len(payload) > MCP_ATTACHMENT_MAX_BYTES:
                raise _attachment_cap_error(db, account, msg_hash, len(payload))
            log_action(
                db,
                user=user,
                action="attachment.download",
                resource_type="attachment",
                resource_id=account.id,
                resource_name=filename,
                details={
                    "message_id_hash": message_id_hash,
                    "part_index": part_index,
                    "source": source,
                    "via": "mcp",
                },
                ip_address=None,
            )
            return AttachmentDownloadOut(
                filename=filename,
                size_bytes=len(payload),
                source=source,
                content_base64=base64.b64encode(payload).decode("ascii"),
            )

    @mcp.tool(annotations=_READ_ONLY)
    def imap_coords(account_id: str, message_ids: list[str]) -> ImapCoordsResponseOut:
        """Message-Ids to live IMAP folder keys and UIDs.

        The bridge to the IMAP path: search here, then FETCH over IMAP with
        an existing client. Ids beyond ``RESOLVE_COORDS_MAX_IDS`` are ignored
        entirely — neither resolved nor reported missing. ``imap_unavailable``
        means Dovecot itself could not be reached: every id in ``missing`` was
        never actually checked, so the right response is a retry, not
        concluding the mail is gone.
        """
        from mailfallback.routers import restore

        with _caller(app_credential_service.SCOPE_MAIL_READ) as (db, user):
            account = _mcp_account(db, user, account_id)
            return ImapCoordsResponseOut.model_validate(
                restore.resolve_uids_for_account(db, account, message_ids[:RESOLVE_COORDS_MAX_IDS])
            )

    @mcp.tool(annotations=ToolAnnotations(read_only_hint=False))
    def sync_now(account_id: str) -> SyncJobOut:
        """Queue and run a sync for one mailbox.

        Idempotent in practice: if a sync is already pending or running, that
        job is returned with ``already_queued: true`` instead of an error.
        Refused (never queued) when the account is suspended, migrating, an
        owner is migrating, or the account carries a self-recovering pause
        (budget/throttle/transient) — unlike the UI, which may warn and
        override a pause, an agent cannot weigh burning the provider's daily
        quota on the account owner's behalf, so here a pause is a plain
        refusal.
        """
        with _caller(app_credential_service.SCOPE_SYNC_TRIGGER) as (db, user):
            account = _mcp_account(db, user, account_id)

            if account.suspended:
                raise ValueError("Sync blocked: account is suspended")
            if account.migrating:
                raise ValueError("Sync blocked: account migration in progress")
            for owner in account.owners:
                if owner.migrating:
                    raise ValueError("Sync blocked: user migration in progress")
            if account.sync_paused_until is not None or account.pause_reason is not None:
                raise ValueError(
                    f"Sync blocked: paused ({account.pause_reason or 'unknown'}); "
                    "an agent cannot override a self-recovering pause"
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
                    raise RuntimeError("Could not queue a sync; retry")
            else:
                # Only the newly-created-job branch submits — an already-running
                # job must never be resubmitted to the executor.
                submit_sync_job(job.id)
                log_action(
                    db,
                    user=user,
                    action="account.sync",
                    resource_type="account",
                    resource_id=account.id,
                    resource_name=account.email_address,
                    details={"via": "mcp", "job_id": job.id},
                    ip_address=None,
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

    @mcp.tool(annotations=_READ_ONLY)
    def sync_status(job_id: str) -> SyncJobOut:
        """Status of one sync job, scoped to the caller's own mailboxes.

        An unknown job id and another user's job are refused with the SAME
        "No such job" text — deliberately, mirroring
        routers/agent.py:sync_job_status's two identical 404s. Do not give
        these two branches different wording again: a caller who owns
        neither id must not be able to tell "this job never existed" apart
        from "this job exists but isn't yours" by the message alone.
        """
        with _caller(app_credential_service.SCOPE_SYNC_TRIGGER) as (db, user):
            job = sync_service.get_job(db, job_id)
            if job is None:
                raise LookupError("No such job")
            try:
                account = _mcp_account(db, user, job.account_id)
            except _NoAccess:
                raise LookupError("No such job") from None
            return SyncJobOut(
                job_id=job.id,
                account_id=account.id,
                status=job.status.value,
                source=job.source,
                requested_at=job.requested_at,
                started_at=job.started_at,
                completed_at=job.completed_at,
                failure_kind=job.failure_kind,
            )
