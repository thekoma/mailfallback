"""Version visible in System page and sidebar footer."""

from mailfallback.models import UserRole
from mailfallback.services.user_service import create_user


def _login(client, username, password):
    client.post("/api/auth/login", json={"username": username, "password": password})


def test_settings_page_shows_version(client, db_session, default_store):
    create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    _login(client, "admin", "pass")
    resp = client.get("/settings")
    assert resp.status_code == 200
    from mailfallback.version import __version__

    assert f"MFB {__version__}" in resp.text


def test_sidebar_footer_shows_version(client, db_session, default_store):
    create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    _login(client, "admin", "pass")
    resp = client.get("/")
    assert resp.status_code == 200
    assert 'class="sidebar-version"' in resp.text
