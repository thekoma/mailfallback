# tests/test_auth.py
import urllib.parse
from unittest.mock import AsyncMock, patch

from mailfallback.models import SyncState, UserRole
from mailfallback.services.account_service import create_account
from mailfallback.services.user_service import create_user


def test_login_success(client, db_session, default_store):
    create_user(db_session, "admin", "secret123", UserRole.admin, store_id=default_store.id)
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "secret123"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.json()["role"] == "admin"


def test_login_wrong_password(client, db_session, default_store):
    create_user(db_session, "admin", "secret123", UserRole.admin, store_id=default_store.id)
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert resp.status_code == 401


def test_login_unknown_user(client, db_session):
    resp = client.post("/api/auth/login", json={"username": "nobody", "password": "x"})
    assert resp.status_code == 401


def test_logout(client, db_session, default_store):
    create_user(db_session, "user1", "pass", UserRole.user, store_id=default_store.id)
    client.post("/api/auth/login", json={"username": "user1", "password": "pass"})
    resp = client.post("/api/auth/logout")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_google_oauth_callback_denied(client, db_session, default_store):
    """OAuth denied (error param) should redirect with flash error."""
    create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    account = create_account(
        db_session,
        name="Test",
        imap_host="imap.gmail.com",
        imap_port=993,
        auth_type="oauth2",
        store=default_store,
        provider="google",
    )

    client.post("/api/auth/login", json={"username": "admin", "password": "pass"})

    resp = client.get("/auth/google/callback?error=access_denied", follow_redirects=False)
    assert resp.status_code in (303, 307, 302)

    db_session.refresh(account)
    assert account.credentials is None


def test_google_callback_after_reauth_resets_error_and_resyncs(client, db_session, default_store):
    """After a successful re-authentication the account must leave the
    token-refresh error state and a sync must be enqueued — the panel
    promises 'sync will resume automatically as soon as you reconnect'."""
    from mailfallback.services.sync_worker import TOKEN_REFRESH_FAILED

    create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    account = create_account(
        db_session,
        name="Gmail",
        imap_host="imap.gmail.com",
        imap_port=993,
        auth_type="oauth2",
        store=default_store,
        provider="google",
    )
    account.credentials = "stale"
    account.sync_state = SyncState.error
    account.last_error = TOKEN_REFRESH_FAILED
    db_session.commit()

    client.post("/api/auth/login", json={"username": "admin", "password": "pass"})

    # /start stores oauth_state + oauth_account_id in the session and embeds
    # the state in the Google redirect URL — read it back from Location.
    start = client.get(f"/auth/google/start?account_id={account.id}", follow_redirects=False)
    assert start.status_code in (302, 303, 307)
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(start.headers["location"]).query)
    state = qs["state"][0]

    submitted = []
    with (
        patch(
            "mailfallback.routers.auth.exchange_google_code",
            new=AsyncMock(return_value={"access_token": "new-at", "refresh_token": "new-rt"}),
        ),
        patch("mailfallback.routers.auth.submit_sync_job", side_effect=submitted.append),
    ):
        resp = client.get(f"/auth/google/callback?code=abc&state={state}", follow_redirects=False)
    assert resp.status_code in (302, 303, 307)

    db_session.refresh(account)
    assert account.sync_state == SyncState.idle
    assert account.last_error is None
    assert len(submitted) == 1
