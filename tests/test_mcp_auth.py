"""The MCP token verifier: the same access token, through the SDK's interface."""

import anyio
import pytest
from sqlalchemy.orm import sessionmaker

from mailfallback.mcp_auth import MfbTokenVerifier
from mailfallback.models import AppCredential, UserRole
from mailfallback.services import app_credential_service as svc
from mailfallback.services.user_service import create_user


@pytest.fixture
def mcp_user(db_session, default_store):
    return create_user(
        db_session, "mcpuser", "mcppass123456", UserRole.user, store_id=default_store.id
    )


def _verify(token, db_session):
    """Drive the async verifier from a sync test.

    Injects a factory that opens a NEW session on the same engine/connection
    as ``db_session`` (StaticPool, so they share the underlying SQLite
    connection) rather than handing over the fixture's own live session —
    the verifier closes whatever session it opens, and closing the fixture's
    shared session out from under it would break the fixture and any
    post-call assertions against ``db_session``.
    """
    verifier = MfbTokenVerifier(session_factory=sessionmaker(bind=db_session.bind))
    result = anyio.run(verifier.verify_token, token)
    # The verifier committed through its own, separate session. The row is
    # physically written (same connection), but db_session's identity map
    # still holds the pre-call instance it loaded earlier and won't refresh
    # already-loaded attributes on its own — expire it so the assertions
    # that follow see what was actually written, not a stale cached copy.
    db_session.expire_all()
    return result


def test_a_valid_token_verifies_and_carries_its_scopes(db_session, mcp_user):
    _, token = svc.create_credential(
        db_session, mcp_user, name="agent", scopes=[svc.SCOPE_MAIL_READ, svc.SCOPE_SYNC_TRIGGER]
    )

    out = _verify(token, db_session)

    assert out is not None
    assert out.client_id == "mcpuser"
    assert sorted(out.scopes) == ["mail:read", "sync:trigger"]
    assert out.subject == mcp_user.id
    # The token itself is echoed back by the interface; it must not be logged
    # or stored anywhere else — asserted here so the field's purpose is explicit.
    assert out.token == token


def test_a_garbage_token_does_not_verify(db_session, mcp_user):
    assert _verify("not-a-token", db_session) is None
    assert _verify("mfb_deadbeef_nope", db_session) is None


def test_a_revoked_token_does_not_verify(db_session, mcp_user):
    cred, token = svc.create_credential(
        db_session, mcp_user, name="doomed", scopes=[svc.SCOPE_MAIL_READ]
    )
    svc.revoke_credential(db_session, mcp_user, cred.id)

    assert _verify(token, db_session) is None


def test_a_disabled_users_token_does_not_verify(db_session, mcp_user):
    _, token = svc.create_credential(db_session, mcp_user, name="t", scopes=[svc.SCOPE_MAIL_READ])
    mcp_user.enabled = False
    db_session.commit()

    assert _verify(token, db_session) is None


def test_an_imap_only_token_verifies_but_carries_only_that_scope(db_session, mcp_user):
    """Authentication succeeds; authorisation is each tool's job. An imap-only
    token must reach the server and then be refused by every tool."""
    _, token = svc.create_credential(db_session, mcp_user, name="t", scopes=[svc.SCOPE_IMAP])

    out = _verify(token, db_session)

    assert out is not None
    assert out.scopes == ["imap"]


def test_verifying_records_the_mcp_kind(db_session, mcp_user):
    _, token = svc.create_credential(db_session, mcp_user, name="t", scopes=[svc.SCOPE_MAIL_READ])

    _verify(token, db_session)

    cred = db_session.query(AppCredential).one()
    assert cred.last_used_kind == "mcp"
    assert cred.last_used_at is not None


def test_verify_token_closes_every_session_it_opens(db_session, mcp_user):
    """The leak: a caller-injected factory (the shape the next task's
    request-scoped dependency uses) must have every session it hands out
    closed, not just the default SessionLocal path.

    Wraps the factory to keep the actual Session objects it produced, then
    asserts each one's ``close`` was called — a direct check on the thing
    that leaks, rather than inferring it from ORM session state.
    """
    _, token = svc.create_credential(db_session, mcp_user, name="t", scopes=[svc.SCOPE_MAIL_READ])

    real_factory = sessionmaker(bind=db_session.bind)
    opened = []

    def tracking_factory():
        session = real_factory()
        original_close = session.close
        session.close = lambda: (setattr(session, "_test_closed", True), original_close())
        session._test_closed = False
        opened.append(session)
        return session

    verifier = MfbTokenVerifier(session_factory=tracking_factory)
    for _ in range(3):
        result = anyio.run(verifier.verify_token, token)
        assert result is not None

    assert len(opened) == 3
    assert all(s._test_closed for s in opened)
