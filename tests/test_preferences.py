from mailfallback.models import UserRole
from mailfallback.services.user_service import create_user


def _login(client, db_session, default_store, username="admin", role=UserRole.admin):
    user = create_user(db_session, username, "pass", role, store_id=default_store.id)
    client.post("/api/auth/login", json={"username": username, "password": "pass"})
    return user


def test_patch_preferences_sets_theme(client, db_session, default_store):
    user = _login(client, db_session, default_store)
    resp = client.patch("/api/preferences", json={"theme": "dark"})
    assert resp.status_code == 204
    db_session.refresh(user)
    assert user.preferences["theme"] == "dark"


def test_patch_preferences_merges(client, db_session, default_store):
    user = _login(client, db_session, default_store)
    client.patch("/api/preferences", json={"theme": "dark"})
    client.patch("/api/preferences", json={"theme": "light"})
    db_session.refresh(user)
    assert user.preferences["theme"] == "light"


def test_patch_preferences_rejects_invalid_theme(client, db_session, default_store):
    _login(client, db_session, default_store)
    resp = client.patch("/api/preferences", json={"theme": "neon"})
    assert resp.status_code == 422


def test_patch_preferences_unauthenticated(client):
    resp = client.patch("/api/preferences", json={"theme": "dark"})
    assert resp.status_code == 401
