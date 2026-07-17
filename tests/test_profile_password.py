"""Profile password change for SSO-provisioned users (no password_hash)."""

from mailfallback.models import UserRole
from mailfallback.security import verify_password
from mailfallback.services.user_service import create_user


def test_sso_user_sets_first_password_without_current_field(client, db_session, default_store):
    """OIDC users have no password_hash; the profile form omits the
    current_password input entirely, so the handler must not require it."""
    user = create_user(
        db_session, "ssouser", "temppass12345", UserRole.user, store_id=default_store.id
    )
    client.post(
        "/login",
        data={"username": "ssouser", "password": "temppass12345"},
        follow_redirects=False,
    )
    # simulate OIDC provisioning: session exists, but no local password
    user.password_hash = None
    db_session.commit()

    resp = client.post(
        "/profile/password",
        data={"new_password": "brandnewpassword456", "confirm_password": "brandnewpassword456"},
        follow_redirects=False,
    )

    assert resp.status_code == 200  # renders profile with success, not a 500
    db_session.refresh(user)
    assert user.password_hash
    assert verify_password("brandnewpassword456", user.password_hash)
