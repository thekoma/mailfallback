# src/mailfallback/mcp_auth.py
"""Bridge the MCP SDK's TokenVerifier onto MFB's access tokens.

One credential model serves three surfaces: a user creates a token in their
profile and uses the same string as an IMAP password, as an HTTP bearer token,
and here. This module is the whole of the MCP-specific auth code — the checks
themselves live in app_credential_service, so MCP cannot drift away from the
other two surfaces.
"""

from collections.abc import Callable

import anyio
from mcp.server.auth.provider import AccessToken, TokenVerifier
from sqlalchemy.orm import Session

from mailfallback.db import SessionLocal
from mailfallback.services import app_credential_service


class MfbTokenVerifier(TokenVerifier):
    """Verify an MFB access token for the MCP server.

    ``session_factory`` exists so tests can inject their own session factory;
    production uses SessionLocal. It must be a FACTORY, not a session: this
    runs in a worker thread, and one SQLAlchemy Session shared across threads
    is not safe.
    """

    def __init__(self, session_factory: Callable[[], Session] | None = None):
        self._session_factory = session_factory or SessionLocal

    async def verify_token(self, token: str) -> AccessToken | None:
        # The interface is async and this runs inside the authentication
        # middleware, on the event loop — but the verification is blocking
        # SQLAlchemy. Off it goes to a worker thread; doing it inline would
        # stall every other request while a token is checked.
        return await anyio.to_thread.run_sync(self._verify_sync, token)

    def _verify_sync(self, token: str) -> AccessToken | None:
        db = self._session_factory()
        try:
            result, cred = app_credential_service.verify_credential(
                db,
                username=None,
                token=token,
                required_scope=None,  # each tool checks its own scope
                kind="mcp",
            )
            if result is not app_credential_service.VerifyResult.ok or cred is None:
                return None
            return AccessToken(
                token=token,
                client_id=cred.user.username,
                scopes=sorted(cred.scope_set),
                # The MFB user id, so a tool can load the caller without
                # re-verifying the token.
                subject=cred.user.id,
            )
        finally:
            # Whoever opens a session owns closing it, regardless of who
            # supplied the factory: a caller-injected factory (the next
            # task's request-scoped SessionLocal, or a test's) leaks a
            # connection per call otherwise.
            db.close()
