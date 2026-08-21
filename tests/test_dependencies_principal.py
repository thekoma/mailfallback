"""Principal resolution: bearer token or session, and the scope gate."""

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from mailfallback.dependencies import Principal, get_current_principal, require_scope
from mailfallback.models import UserRole
from mailfallback.services import app_credential_service as svc
from mailfallback.services.user_service import create_user


@pytest.fixture
def probe_app(app, db_session):
    """Mount throwaway routes that expose what the dependency resolved."""

    @app.get("/_probe/principal")
    def _principal(p: Principal = Depends(get_current_principal)):
        return {
            "username": p.user.username,
            "is_token": p.is_token,
            "scopes": sorted(p.scopes),
        }

    @app.get("/_probe/needs-mail-read")
    def _needs(p: Principal = Depends(require_scope(svc.SCOPE_MAIL_READ))):
        return {"username": p.user.username}

    return app


@pytest.fixture
def probe_client(probe_app):
    return TestClient(probe_app)


def _user_with_token(db_session, store, scopes, username="agent"):
    user = create_user(db_session, username, "agentpass12345", UserRole.user, store_id=store.id)
    _, token = svc.create_credential(db_session, user, name="t", scopes=scopes)
    return user, token


def test_bearer_token_resolves_to_a_token_principal(probe_client, db_session, default_store):
    _, token = _user_with_token(db_session, default_store, [svc.SCOPE_MAIL_READ])

    resp = probe_client.get("/_probe/principal", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert resp.json() == {"username": "agent", "is_token": True, "scopes": ["mail:read"]}


def test_session_resolves_to_a_principal_with_every_scope(probe_client, db_session, default_store):
    """An interactive user can already do all of this through the UI, so a
    session must never be blocked by a scope check."""
    create_user(db_session, "human", "humanpass12345", UserRole.user, store_id=default_store.id)
    probe_client.post(
        "/login", data={"username": "human", "password": "humanpass12345"}, follow_redirects=False
    )

    resp = probe_client.get("/_probe/principal")

    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == "human"
    assert body["is_token"] is False
    assert set(body["scopes"]) == set(svc.VALID_SCOPES)


def test_no_credentials_at_all_is_401(probe_client, db_session):
    resp = probe_client.get("/_probe/principal")
    assert resp.status_code == 401


def test_a_bad_bearer_token_is_401_and_does_not_fall_back_to_the_session(
    probe_client, db_session, default_store
):
    """A caller who presented a token meant to authenticate as that token.
    Silently downgrading to the session identity would be a confused-deputy."""
    create_user(db_session, "human", "humanpass12345", UserRole.user, store_id=default_store.id)
    probe_client.post(
        "/login", data={"username": "human", "password": "humanpass12345"}, follow_redirects=False
    )

    resp = probe_client.get(
        "/_probe/principal", headers={"Authorization": "Bearer mfb_deadbeef_nope"}
    )

    assert resp.status_code == 401


def test_a_non_token_bearer_value_is_401(probe_client, db_session):
    resp = probe_client.get("/_probe/principal", headers={"Authorization": "Bearer not-even-close"})
    assert resp.status_code == 401


def test_require_scope_allows_a_token_that_has_it(probe_client, db_session, default_store):
    _, token = _user_with_token(db_session, default_store, [svc.SCOPE_MAIL_READ])

    resp = probe_client.get("/_probe/needs-mail-read", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200


def test_require_scope_refuses_a_token_without_it(probe_client, db_session, default_store):
    """An imap-only token is a real case: it is what the IMAP skills use."""
    _, token = _user_with_token(db_session, default_store, [svc.SCOPE_IMAP])

    resp = probe_client.get("/_probe/needs-mail-read", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 403


def test_require_scope_allows_a_session(probe_client, db_session, default_store):
    create_user(db_session, "human", "humanpass12345", UserRole.user, store_id=default_store.id)
    probe_client.post(
        "/login", data={"username": "human", "password": "humanpass12345"}, follow_redirects=False
    )

    resp = probe_client.get("/_probe/needs-mail-read")

    assert resp.status_code == 200


def test_a_disabled_users_token_is_refused(probe_client, db_session, default_store):
    user, token = _user_with_token(db_session, default_store, [svc.SCOPE_MAIL_READ])
    user.enabled = False
    db_session.commit()

    resp = probe_client.get("/_probe/principal", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401


def test_using_a_token_records_the_api_kind(probe_client, db_session, default_store):
    from mailfallback.models import AppCredential

    _, token = _user_with_token(db_session, default_store, [svc.SCOPE_MAIL_READ])

    probe_client.get("/_probe/principal", headers={"Authorization": f"Bearer {token}"})

    cred = db_session.query(AppCredential).one()
    assert cred.last_used_kind == "api"
    assert cred.last_used_at is not None


def test_a_lowercase_bearer_scheme_with_a_valid_token_authenticates_as_the_token(
    probe_client, db_session, default_store
):
    """RFC 7235 auth-scheme names are case-insensitive; a client that sends
    ``bearer`` instead of ``Bearer`` must still take the token path."""
    _, token = _user_with_token(db_session, default_store, [svc.SCOPE_MAIL_READ])

    resp = probe_client.get("/_probe/principal", headers={"Authorization": f"bearer {token}"})

    assert resp.status_code == 200
    assert resp.json() == {"username": "agent", "is_token": True, "scopes": ["mail:read"]}


def test_a_lowercase_bearer_scheme_with_a_bad_token_is_401_and_does_not_fall_back_to_the_session(
    probe_client, db_session, default_store
):
    """The confused-deputy check must hold regardless of scheme casing: a
    lowercase ``bearer`` with a bad token must not silently authenticate as
    whatever session cookie happens to be attached."""
    create_user(db_session, "human", "humanpass12345", UserRole.user, store_id=default_store.id)
    probe_client.post(
        "/login", data={"username": "human", "password": "humanpass12345"}, follow_redirects=False
    )

    resp = probe_client.get(
        "/_probe/principal", headers={"Authorization": "bearer mfb_deadbeef_nope"}
    )

    assert resp.status_code == 401


def test_a_mixed_case_bearer_scheme_resolves_to_a_token_principal(
    probe_client, db_session, default_store
):
    _, token = _user_with_token(db_session, default_store, [svc.SCOPE_MAIL_READ])

    resp = probe_client.get("/_probe/principal", headers={"Authorization": f"BeArEr {token}"})

    assert resp.status_code == 200
    assert resp.json()["is_token"] is True


def test_a_bare_bearer_scheme_with_no_token_is_401_not_a_session_fallback(
    probe_client, db_session, default_store
):
    """Declaring the bearer scheme is a commitment to the token path even when
    nothing follows it — it must not fall through to the session."""
    create_user(db_session, "human", "humanpass12345", UserRole.user, store_id=default_store.id)
    probe_client.post(
        "/login", data={"username": "human", "password": "humanpass12345"}, follow_redirects=False
    )

    resp = probe_client.get("/_probe/principal", headers={"Authorization": "Bearer"})

    assert resp.status_code == 401


def test_a_non_bearer_scheme_with_a_session_attached_resolves_to_the_session(
    probe_client, db_session, default_store
):
    create_user(db_session, "human", "humanpass12345", UserRole.user, store_id=default_store.id)
    probe_client.post(
        "/login", data={"username": "human", "password": "humanpass12345"}, follow_redirects=False
    )

    resp = probe_client.get("/_probe/principal", headers={"Authorization": "Basic dXNlcjpwYXNz"})

    assert resp.status_code == 200
    assert resp.json()["username"] == "human"
