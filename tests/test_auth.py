# tests/test_auth.py
from mailfallback.models import UserRole
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
