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

import logging
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_REQUEST
from pydantic import AnyHttpUrl
from sqlalchemy import func
from sqlalchemy.orm import Session
from starlette.applications import Starlette

from mailfallback.config import settings as _settings
from mailfallback.db import SessionLocal
from mailfallback.mcp_auth import MfbTokenVerifier
from mailfallback.models import Account, MailIndexMessage, User
from mailfallback.services import app_credential_service, preview_service, search_service
from mailfallback.version import __version__

logger = logging.getLogger(__name__)

MCP_PATH = "/mcp"


def _require_scope(scope: str) -> str:
    """Return the caller's MFB user id, or raise if the scope is missing.

    Mirrors dependencies.require_scope for the MCP surface. Raising MCPError
    surfaces to the client as a tool error rather than a transport failure.
    """
    token = get_access_token()
    if token is None:  # pragma: no cover — the middleware refuses first
        raise MCPError(code=INVALID_REQUEST, message="Not authenticated")
    if scope not in token.scopes:
        raise MCPError(code=INVALID_REQUEST, message=f"This access token lacks the {scope} scope")
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


_server: MCPServer | None = None


def get_server(settings) -> MCPServer | None:
    """Build the server once and register the tools on it."""
    global _server
    if _server is None:
        _server = build_mcp_server(settings)
        if _server is not None:
            _register_tools(_server)
    return _server


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
            raise MCPError(code=INVALID_REQUEST, message="Caller no longer exists")
        yield db, user
    finally:
        db.close()


def _mcp_account(db: Session, user: User, account_id: str) -> Account:
    """The account, or an error that does not reveal whether it exists."""
    if account_id not in search_service._accessible_account_ids(db, user):
        raise MCPError(code=INVALID_REQUEST, message="No such mailbox")
    account = db.query(Account).filter(Account.id == account_id).first()
    if account is None:
        raise MCPError(code=INVALID_REQUEST, message="No such mailbox")
    return account


def _mcp_hash(value: str) -> bytes:
    """A message_id_hash argument as bytes, or an error naming the field."""
    try:
        return bytes.fromhex(value)
    except ValueError:
        raise MCPError(code=INVALID_REQUEST, message="Invalid message_id_hash") from None


def _register_tools(mcp: MCPServer) -> None:
    from mcp.types import ToolAnnotations

    _READ_ONLY = ToolAnnotations(read_only_hint=True)

    @mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
    def ping() -> str:
        """Confirm the server is reachable and the token is accepted."""
        _require_scope(app_credential_service.SCOPE_MAIL_READ)
        return "ok"

    @mcp.tool(annotations=_READ_ONLY)
    def list_mailboxes() -> dict[str, Any]:
        """The mailboxes this token's owner can search, with what is indexed in each.

        ``indexed_messages`` and ``folders`` describe what is in the search
        index right now, not a live provider-side message count — that is
        the question this tool answers: what can be searched here.
        """
        with _caller(app_credential_service.SCOPE_MAIL_READ) as (db, user):
            visible = search_service._accessible_account_ids(db, user)
            if not visible:
                return {"mailboxes": []}
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

            return {
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

    @mcp.tool(annotations=_READ_ONLY)
    def search_mail(
        query: str = "",
        account_ids: list[str] | None = None,
        range_start: datetime | None = None,
        range_end: datetime | None = None,
        include_deleted: bool = True,
        snapshot_id: str | None = None,
        deep: bool = False,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        """Indexed search across every mailbox this token's owner can see.

        A hit's ``message_id_hash`` plus an attachment's ``part_index`` are
        what ``download_attachment`` takes to fetch that attachment's bytes.
        ``deep=true`` additionally runs a Dovecot body search over live
        folders; when that search times out the response comes back with
        ``partial: true`` rather than an error — it means the results may be
        incomplete, not that nothing matched.
        """
        with _caller(app_credential_service.SCOPE_MAIL_READ) as (db, user):
            return search_service.search_messages(
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
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
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
            return result

    @mcp.tool(annotations=_READ_ONLY)
    def get_message(account_id: str, message_id_hash: str) -> dict[str, Any]:
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
                raise MCPError(code=INVALID_REQUEST, message="Message not found")
            return out
