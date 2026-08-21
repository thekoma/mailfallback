"""The MCP token verifier: the same access token, through the SDK's interface."""

import anyio
import pytest

from mailfallback.mcp_auth import MfbTokenVerifier
from mailfallback.models import AppCredential, UserRole
from mailfallback.services import app_credential_service as svc
from mailfallback.services.user_service import create_user


@pytest.fixture
def mcp_user(db_session, default_store):
    return create_user(
        db_session, "mcpuser", "mcppass123456", UserRole.user, store_id=default_store.id
    )


def _verify(token, session_factory):
    """Drive the async verifier from a sync test."""
    verifier = MfbTokenVerifier(session_factory=session_factory)
    return anyio.run(verifier.verify_token, token)


def test_a_valid_token_verifies_and_carries_its_scopes(db_session, mcp_user):
    _, token = svc.create_credential(
        db_session, mcp_user, name="agent", scopes=[svc.SCOPE_MAIL_READ, svc.SCOPE_SYNC_TRIGGER]
    )

    out = _verify(token, lambda: db_session)

    assert out is not None
    assert out.client_id == "mcpuser"
    assert sorted(out.scopes) == ["mail:read", "sync:trigger"]
    assert out.subject == mcp_user.id
    # The token itself is echoed back by the interface; it must not be logged
    # or stored anywhere else — asserted here so the field's purpose is explicit.
    assert out.token == token


def test_a_garbage_token_does_not_verify(db_session, mcp_user):
    assert _verify("not-a-token", lambda: db_session) is None
    assert _verify("mfb_deadbeef_nope", lambda: db_session) is None


def test_a_revoked_token_does_not_verify(db_session, mcp_user):
    cred, token = svc.create_credential(
        db_session, mcp_user, name="doomed", scopes=[svc.SCOPE_MAIL_READ]
    )
    svc.revoke_credential(db_session, mcp_user, cred.id)

    assert _verify(token, lambda: db_session) is None


def test_a_disabled_users_token_does_not_verify(db_session, mcp_user):
    _, token = svc.create_credential(db_session, mcp_user, name="t", scopes=[svc.SCOPE_MAIL_READ])
    mcp_user.enabled = False
    db_session.commit()

    assert _verify(token, lambda: db_session) is None


def test_an_imap_only_token_verifies_but_carries_only_that_scope(db_session, mcp_user):
    """Authentication succeeds; authorisation is each tool's job. An imap-only
    token must reach the server and then be refused by every tool."""
    _, token = svc.create_credential(db_session, mcp_user, name="t", scopes=[svc.SCOPE_IMAP])

    out = _verify(token, lambda: db_session)

    assert out is not None
    assert out.scopes == ["imap"]


def test_verifying_records_the_mcp_kind(db_session, mcp_user):
    _, token = svc.create_credential(db_session, mcp_user, name="t", scopes=[svc.SCOPE_MAIL_READ])

    _verify(token, lambda: db_session)

    cred = db_session.query(AppCredential).one()
    assert cred.last_used_kind == "mcp"
    assert cred.last_used_at is not None
