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

from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_REQUEST
from pydantic import AnyHttpUrl
from starlette.applications import Starlette

from mailfallback.db import (
    SessionLocal,  # noqa: F401 -- unused until Task 3's tools open their own sessions
)
from mailfallback.mcp_auth import MfbTokenVerifier
from mailfallback.services import app_credential_service
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


def _register_tools(mcp: MCPServer) -> None:
    from mcp.types import ToolAnnotations

    @mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
    def ping() -> str:
        """Confirm the server is reachable and the token is accepted."""
        _require_scope(app_credential_service.SCOPE_MAIL_READ)
        return "ok"
