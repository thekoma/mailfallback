"""The dashboard must surface accounts that need re-authorization, so a
revoked OAuth token (e.g. after a Gmail password change) is visible without
opening the account (2026-06-28)."""

from mailfallback.models import SyncState, UserRole
from mailfallback.services.account_service import assign_owner, create_account
from mailfallback.services.user_service import create_user


def test_dashboard_flags_needs_reauth_account(client, db_session, default_store):
    user = create_user(db_session, "u", "secretpass123", UserRole.user, store_id=default_store.id)
    account = create_account(
        db_session, "Gmail", "imap.gmail.com", 993, "oauth2", store=default_store
    )
    account.provider = "google"
    account.sync_state = SyncState.needs_reauth
    db_session.commit()
    assign_owner(db_session, account.id, user.id)

    client.post(
        "/login",
        data={"username": "u", "password": "secretpass123"},
        follow_redirects=False,
    )
    resp = client.get("/")

    assert resp.status_code == 200
    # surfaced in the Needs Attention panel with a one-click reconnect link
    assert "Sign-in expired" in resp.text
    assert f"/auth/google/start?account_id={account.id}" in resp.text
