"""Audit-log coverage for state-changing actions that were silently
unaudited (2026-06-27 sweep)."""

from mailfallback.models import AuditLog, UserRole
from mailfallback.services.user_service import create_user


def test_form_login_is_audited(client, db_session, default_store):
    """The browser form login (/login) must be audited like the API login."""
    create_user(db_session, "webuser", "secretpass123", UserRole.user, store_id=default_store.id)

    resp = client.post(
        "/login",
        data={"username": "webuser", "password": "secretpass123"},
        follow_redirects=False,
    )
    assert resp.status_code == 303  # redirect on success

    rows = db_session.query(AuditLog).filter(AuditLog.action == "user.login").all()
    assert len(rows) == 1
    assert rows[0].username == "webuser"


def test_password_change_is_audited(client, db_session, default_store):
    """Changing one's own password must be audited."""
    create_user(db_session, "webuser2", "secretpass123", UserRole.user, store_id=default_store.id)
    client.post(
        "/login",
        data={"username": "webuser2", "password": "secretpass123"},
        follow_redirects=False,
    )

    client.post(
        "/profile/password",
        data={
            "current_password": "secretpass123",
            "new_password": "brandnewpassword456",
            "confirm_password": "brandnewpassword456",
        },
        follow_redirects=False,
    )

    rows = db_session.query(AuditLog).filter(AuditLog.action == "user.password_change").all()
    assert len(rows) == 1
    assert rows[0].username == "webuser2"
