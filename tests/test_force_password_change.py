# tests/test_force_password_change.py
"""ForcePasswordChangeMiddleware redirects HTML pages when admin still has
the default bootstrap password; lets exempt paths through."""

from mailfallback.models import UserRole
from mailfallback.services.user_service import create_user


def _login(client, username, password):
    client.post("/api/auth/login", json={"username": username, "password": password})


def test_admin_with_default_password_redirected_off_dashboard(client, db_session, default_store):
    create_user(db_session, "admin", "changeme1234!", UserRole.admin, store_id=default_store.id)
    _login(client, "admin", "changeme1234!")
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/profile?force_password_change=1"


def test_admin_with_changed_password_can_open_dashboard(client, db_session, default_store):
    create_user(
        db_session, "admin", "very-strong-pass-123!", UserRole.admin, store_id=default_store.id
    )
    _login(client, "admin", "very-strong-pass-123!")
    resp = client.get("/")
    assert resp.status_code == 200


def test_non_admin_user_unaffected(client, db_session, default_store):
    create_user(db_session, "alice", "changeme1234!", UserRole.user, store_id=default_store.id)
    _login(client, "alice", "changeme1234!")
    # Non-admin: middleware does not redirect.
    resp = client.get("/", follow_redirects=False)
    # User dashboard renders (200) — non-admin doesn't get the password check.
    assert resp.status_code in (200, 303)
    if resp.status_code == 303:
        # If anything redirects, it should NOT be to force_password_change.
        assert "force_password_change" not in resp.headers.get("location", "")


def test_profile_path_exempt(client, db_session, default_store):
    create_user(db_session, "admin", "changeme1234!", UserRole.admin, store_id=default_store.id)
    _login(client, "admin", "changeme1234!")
    # /profile is exempt — should render normally so the user can change pw.
    resp = client.get("/profile")
    assert resp.status_code == 200


def test_static_path_exempt(client, db_session, default_store):
    create_user(db_session, "admin", "changeme1234!", UserRole.admin, store_id=default_store.id)
    _login(client, "admin", "changeme1234!")
    # /static/* is exempt — static files can be served without redirect.
    # (404 because no file at /static/foo, but no 303 redirect.)
    resp = client.get("/static/css/style.css", follow_redirects=False)
    assert resp.status_code in (200, 404)


def test_anonymous_user_not_affected(client, db_session, default_store):
    create_user(db_session, "admin", "changeme1234!", UserRole.admin, store_id=default_store.id)
    # No login. Dashboard's own redirect to /login should fire (not the
    # password middleware).
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 307  # FastAPI's RedirectResponse default
    assert resp.headers["location"] == "/login"
