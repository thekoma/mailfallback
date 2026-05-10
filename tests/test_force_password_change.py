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


def test_oidc_admin_unaffected(client, db_session, default_store):
    """OIDC-linked admins are exempt: even with a default password_hash
    leftover from bootstrap, the middleware skips them.

    Reproduces the koma bug: admin role + OIDC link + bootstrap default
    password_hash = should NOT redirect.
    """
    from mailfallback.models import User, UserRole
    from mailfallback.security import hash_password

    user = User(
        username="koma",
        password_hash=hash_password("changeme1234!"),  # leftover from bootstrap
        role=UserRole.admin,
        store_id=default_store.id,
        oidc_subject="external-id-1234",
    )
    db_session.add(user)
    db_session.commit()

    # Authenticate via the local-password API (the leftover hash still works).
    _login(client, "koma", "changeme1234!")
    resp = client.get("/", follow_redirects=False)
    # Middleware exempts OIDC users → no force_password_change redirect.
    if resp.status_code == 303:
        assert "force_password_change" not in resp.headers.get("location", ""), (
            "OIDC-linked admin should be exempt from force-password-change redirect"
        )


def test_admin_with_no_password_hash_unaffected(db_session, default_store):
    """Admin created without a local password (rare) — middleware skips.
    Unit-level: just assert the model accepts None password_hash."""
    from mailfallback.models import User, UserRole

    user = User(
        username="api-admin",
        password_hash=None,
        role=UserRole.admin,
        store_id=default_store.id,
        oidc_subject="ext-no-pw",
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    assert user.password_hash is None
    assert user.oidc_subject == "ext-no-pw"
