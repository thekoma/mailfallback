"""Mounting the MCP app: the wiring, independent of any tool's behaviour."""

import pytest
from fastapi.testclient import TestClient

import mailfallback.config as cfg
from mailfallback.app import create_app
from mailfallback.dependencies import get_db
from mailfallback.models import UserRole
from mailfallback.services import app_credential_service as svc
from mailfallback.services.user_service import create_user


@pytest.fixture
def mcp_app(db_session, monkeypatch):
    """An app with MCP switched on, pointed at a localhost public URL.

    The token verifier must see the TEST database, and it runs in a worker
    thread (`anyio.to_thread.run_sync`), so it is given a factory that opens a
    NEW session on the test engine rather than the fixture's shared Session —
    a single SQLAlchemy Session is not safe to use from two threads.
    """
    import tempfile

    from sqlalchemy.orm import sessionmaker

    import mailfallback.app as app_module
    import mailfallback.db as db_module
    import mailfallback.mcp_auth as mcp_auth
    import mailfallback.mcp_server as ms

    engine = db_session.get_bind()
    test_sessionmaker = sessionmaker(bind=engine)
    monkeypatch.setattr(mcp_auth, "SessionLocal", test_sessionmaker)
    monkeypatch.setattr(ms, "SessionLocal", test_sessionmaker)
    monkeypatch.setattr(ms, "_server", None)  # rebuild per test
    monkeypatch.setattr(cfg.settings, "mcp_enabled", True, raising=False)
    monkeypatch.setattr(cfg.settings, "mcp_public_url", "http://127.0.0.1:8000", raising=False)
    # create_app() (unlike the `app` fixture) is called directly here, so it
    # needs its own writable confs_path -- otherwise generate_all_configs()
    # tries to write under the real /confs and fails on a read-only fs.
    monkeypatch.setattr(cfg.settings, "confs_path", tempfile.mkdtemp())
    # These tests use `with TestClient(mcp_app)`, which — unlike the bare
    # `client` fixture used elsewhere — actually runs the app's lifespan (it
    # has to, to enter the MCP session manager). The lifespan's own startup
    # work opens a session through `mailfallback.app.SessionLocal`, which by
    # default points at the real (unreachable, in tests) Postgres host — so
    # it needs the same test-engine swap as the other two SessionLocals.
    monkeypatch.setattr(app_module, "SessionLocal", test_sessionmaker)
    # The lifespan also fires a background thread (fts reindex, on the first
    # boot into a fresh confs_path) that does its own late
    # `from mailfallback.db import SessionLocal` -- patch the root so that
    # thread doesn't crash trying to reach the real Postgres host either.
    monkeypatch.setattr(db_module, "SessionLocal", test_sessionmaker)

    application = create_app()
    application.dependency_overrides[get_db] = lambda: db_session
    return application


def test_the_mcp_endpoint_is_absent_when_disabled(client):
    """Off by default: nothing mounted, so the path does not resolve."""
    resp = client.post("/mcp", json={})
    assert resp.status_code == 404


def test_the_mcp_endpoint_rejects_an_unauthenticated_call(mcp_app):
    with TestClient(mcp_app, base_url="http://127.0.0.1:8000") as c:
        resp = c.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Accept": "application/json, text/event-stream"},
        )
    assert resp.status_code == 401
    # The SDK is expected to name the scheme it wants.
    assert "bearer" in resp.headers.get("www-authenticate", "").lower()


def test_the_mcp_endpoint_accepts_a_valid_token(mcp_app, db_session, default_store):
    # Unlike the bare `client` fixture, entering `mcp_app` via `with
    # TestClient(...)` actually runs the app's lifespan (it has to, to enter
    # the MCP session manager) — and that lifespan calls ensure_default_store,
    # which only recognises an EXISTING store as "already have one" via
    # is_default. The `default_store` fixture doesn't set that flag, so
    # without this the lifespan tries to create a second store at the same
    # bootstrap path and hits a UNIQUE constraint on it.
    default_store.is_default = True
    db_session.commit()

    user = create_user(
        db_session, "mcpmount", "mcppass123456", UserRole.user, store_id=default_store.id
    )
    _, token = svc.create_credential(db_session, user, name="agent", scopes=[svc.SCOPE_MAIL_READ])

    with TestClient(mcp_app, base_url="http://127.0.0.1:8000") as c:
        resp = c.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            },
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json, text/event-stream",
            },
        )

    # The session-manager lifespan must have run: without it the SDK raises a
    # task-group error rather than answering at all.
    assert resp.status_code == 200, resp.text


def test_a_garbage_token_is_rejected(mcp_app):
    with TestClient(mcp_app, base_url="http://127.0.0.1:8000") as c:
        resp = c.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={
                "Authorization": "Bearer mfb_deadbeef_nope",
                "Accept": "application/json, text/event-stream",
            },
        )
    assert resp.status_code == 401
