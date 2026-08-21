"""Access tokens on the profile page: create, list, reveal once, revoke."""

from mailfallback.config import settings
from mailfallback.models import AppCredential, NotificationChannel, UserRole
from mailfallback.security import encrypt_credentials
from mailfallback.services import app_credential_service as svc
from mailfallback.services.user_service import create_user


def _login(client, db_session, default_store, username="tokui"):
    user = create_user(
        db_session, username, "startpass12345", UserRole.user, store_id=default_store.id
    )
    client.post(
        "/login",
        data={"username": username, "password": "startpass12345"},
        follow_redirects=False,
    )
    return user


def test_profile_page_lists_existing_tokens(client, db_session, default_store):
    user = _login(client, db_session, default_store)
    svc.create_credential(db_session, user, name="Hermes Agent", scopes=[svc.SCOPE_IMAP])

    resp = client.get("/profile")

    assert resp.status_code == 200
    assert "Access tokens" in resp.text
    assert "Hermes Agent" in resp.text


def test_profile_page_shows_the_empty_state_without_tokens(client, db_session, default_store):
    _login(client, db_session, default_store)

    resp = client.get("/profile")

    assert resp.status_code == 200
    assert "No access tokens yet" in resp.text


def test_creating_a_token_reveals_it_once(client, db_session, default_store):
    user = _login(client, db_session, default_store)

    resp = client.post(
        "/profile/tokens",
        data={"name": "Hermes", "scopes": ["imap"], "ttl_days": ""},
        follow_redirects=False,
    )

    assert resp.status_code == 200  # renders, does not redirect — the token is shown once
    cred = db_session.query(AppCredential).one()
    assert cred.user_id == user.id
    assert cred.scopes == "imap"
    assert cred.expires_at is None
    # The full token appears in this response...
    assert f"mfb_{cred.token_prefix}_" in resp.text
    # ...and never again.
    assert f"mfb_{cred.token_prefix}_" not in client.get("/profile").text


def test_creating_a_token_with_a_ttl_sets_expiry(client, db_session, default_store):
    _login(client, db_session, default_store)

    client.post(
        "/profile/tokens",
        data={"name": "Temp", "scopes": ["imap"], "ttl_days": "30"},
        follow_redirects=False,
    )

    assert db_session.query(AppCredential).one().expires_at is not None


def test_creating_a_token_with_multiple_scopes(client, db_session, default_store):
    _login(client, db_session, default_store)

    client.post(
        "/profile/tokens",
        data={"name": "Both", "scopes": ["imap", "mail:read"], "ttl_days": ""},
        follow_redirects=False,
    )

    assert db_session.query(AppCredential).one().scopes == "imap,mail:read"


def test_an_unknown_scope_is_dropped_not_stored(client, db_session, default_store):
    """Scope names come from the request; only known ones may reach the row."""
    _login(client, db_session, default_store)

    resp = client.post(
        "/profile/tokens",
        data={"name": "Sneaky", "scopes": ["imap", "mail:write"], "ttl_days": ""},
        follow_redirects=False,
    )

    assert resp.status_code == 200
    assert db_session.query(AppCredential).one().scopes == "imap"


def test_creating_a_token_with_no_valid_scope_shows_an_error(client, db_session, default_store):
    _login(client, db_session, default_store)

    resp = client.post(
        "/profile/tokens",
        data={"name": "Empty", "scopes": [], "ttl_days": ""},
        follow_redirects=False,
    )

    assert resp.status_code == 200
    assert db_session.query(AppCredential).count() == 0
    assert "at least one scope" in resp.text.lower()


def test_revoking_a_token_keeps_the_row_and_marks_it(client, db_session, default_store):
    user = _login(client, db_session, default_store)
    cred, _ = svc.create_credential(db_session, user, name="Doomed", scopes=[svc.SCOPE_IMAP])

    resp = client.post(f"/profile/tokens/{cred.id}/revoke", follow_redirects=False)

    assert resp.status_code == 303
    db_session.refresh(cred)
    assert cred.revoked_at is not None
    assert db_session.query(AppCredential).count() == 1


def test_cannot_revoke_another_users_token(client, db_session, default_store):
    _login(client, db_session, default_store)
    victim = create_user(
        db_session, "victim", "victimpass12345", UserRole.user, store_id=default_store.id
    )
    cred, _ = svc.create_credential(db_session, victim, name="Theirs", scopes=[svc.SCOPE_IMAP])

    client.post(f"/profile/tokens/{cred.id}/revoke", follow_redirects=False)

    db_session.refresh(cred)
    assert cred.revoked_at is None


def test_token_routes_require_a_session(client, db_session, default_store):
    resp = client.post(
        "/profile/tokens", data={"name": "x", "scopes": ["imap"]}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_a_password_error_does_not_blank_the_other_sections(client, db_session, default_store):
    """Regression: profile_change_password hand-built a context without
    `channels`, so a wrong password silently emptied the notifications section.
    The shared context helper must keep every section populated."""
    user = _login(client, db_session, default_store)
    svc.create_credential(db_session, user, name="Survivor", scopes=[svc.SCOPE_IMAP])
    db_session.add(
        NotificationChannel(
            user_id=user.id,
            label="My phone",
            apprise_url=encrypt_credentials("ntfy://ntfy.sh/topic", settings.secret_key),
            events=[],
        )
    )
    db_session.commit()

    resp = client.post(
        "/profile/password",
        data={
            "current_password": "wrongpassword",
            "new_password": "brandnewpass123",
            "confirm_password": "brandnewpass123",
        },
        follow_redirects=False,
    )

    assert resp.status_code == 200
    assert "Survivor" in resp.text
    assert "My phone" in resp.text
