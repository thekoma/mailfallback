# tests/test_ui.py
from mailfallback.models import UserRole
from mailfallback.services.user_service import create_user


def test_login_page(client):
    resp = client.get("/login")
    assert resp.status_code == 200
    assert "Login" in resp.text


def test_dashboard_redirects_when_not_logged_in(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code in (302, 307)


def test_dashboard_shows_when_logged_in(client, db_session, default_store):
    create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    client.post("/api/auth/login", json={"username": "admin", "password": "pass"})
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Dashboard" in resp.text


def test_webmail_link_hidden_by_default(client, db_session, default_store):
    create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    client.post("/api/auth/login", json={"username": "admin", "password": "pass"})
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Webmail" not in resp.text


def test_webmail_link_shown_when_configured(db_session, default_store, monkeypatch):
    import sys
    import tempfile

    # Remove modules from cache to force reload with new settings
    modules_to_remove = ["mailfallback.routers.ui", "mailfallback.app"]
    for module in modules_to_remove:
        if module in sys.modules:
            del sys.modules[module]

    # Monkeypatch BEFORE importing to ensure the global is set correctly
    monkeypatch.setattr("mailfallback.config.settings.webmail_url", "http://localhost:8001")
    monkeypatch.setattr("mailfallback.config.settings.webmail_enabled", True)
    monkeypatch.setattr("mailfallback.config.settings.confs_path", tempfile.mkdtemp())

    # Now import and create app - the ui module will be imported with the new setting
    from mailfallback.app import create_app
    from mailfallback.dependencies import get_db

    # Also need to manually set the global since templates is already instantiated
    from mailfallback.routers import ui

    ui.templates.env.globals["webmail_url"] = "http://localhost:8001"
    ui.templates.env.globals["webmail_enabled"] = True

    application = create_app()
    application.dependency_overrides[get_db] = lambda: db_session
    from fastapi.testclient import TestClient

    test_client = TestClient(application)
    create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    test_client.post("/api/auth/login", json={"username": "admin", "password": "pass"})
    resp = test_client.get("/")
    assert resp.status_code == 200
    assert "Webmail" in resp.text
    assert "http://localhost:8001" in resp.text
