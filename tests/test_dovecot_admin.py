from unittest.mock import MagicMock, patch

from mailfallback.models import UserRole
from mailfallback.services.dovecot_manager import (
    check_dovecot_health,
    force_resync,
    fts_rescan,
)
from mailfallback.services.user_service import create_user


def _login_admin(client, db_session, default_store):
    user = create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    client.post("/api/auth/login", json={"username": "admin", "password": "pass"})
    return user


def _login_regular(client, db_session, default_store):
    create_user(db_session, "regular", "pass", UserRole.user, store_id=default_store.id)
    client.post("/api/auth/login", json={"username": "regular", "password": "pass"})


# --- Route auth checks ---


def test_dovecot_health_requires_admin(client, db_session, default_store):
    _login_regular(client, db_session, default_store)
    resp = client.post("/admin/dovecot/health-check", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


def test_fts_reindex_requires_admin(client, db_session, default_store):
    _login_regular(client, db_session, default_store)
    resp = client.post("/admin/dovecot/fts-reindex", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


def test_force_resync_requires_admin(client, db_session, default_store):
    _login_regular(client, db_session, default_store)
    resp = client.post("/admin/dovecot/force-resync", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


def test_dovecot_health_unauthenticated(client):
    resp = client.post("/admin/dovecot/health-check", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


def test_fts_reindex_unauthenticated(client):
    resp = client.post("/admin/dovecot/fts-reindex", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


def test_force_resync_unauthenticated(client):
    resp = client.post("/admin/dovecot/force-resync", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


def test_dovecot_health_admin_redirects_to_settings(client, db_session, default_store):
    _login_admin(client, db_session, default_store)
    with patch("mailfallback.services.dovecot_manager.check_dovecot_health") as mock_check:
        mock_check.return_value = {"ok": True}
        resp = client.post("/admin/dovecot/health-check", follow_redirects=False)
    assert resp.status_code == 303
    assert "/settings?" in resp.headers["location"]
    assert "dovecot_status=ok" in resp.headers["location"]


def test_dovecot_health_admin_error(client, db_session, default_store):
    _login_admin(client, db_session, default_store)
    with patch("mailfallback.services.dovecot_manager.check_dovecot_health") as mock_check:
        mock_check.return_value = {"ok": False, "error": "connection refused"}
        resp = client.post("/admin/dovecot/health-check", follow_redirects=False)
    assert resp.status_code == 303
    assert "dovecot_status=error" in resp.headers["location"]


def test_settings_page_shows_dovecot_section(client, db_session, default_store):
    _login_admin(client, db_session, default_store)
    resp = client.get("/settings")
    assert resp.status_code == 200
    assert "Dovecot management" in resp.text
    assert "Health Check" in resp.text
    assert "Force Resync" in resp.text
    assert "FTS Reindex" in resp.text


# --- Service function tests ---


def test_check_dovecot_health_disabled(monkeypatch):
    monkeypatch.setattr("mailfallback.services.dovecot_manager.settings.dovecot_api_url", "")
    result = check_dovecot_health()
    assert result["ok"] is False
    assert "not configured" in result["error"]


def test_check_dovecot_health_success(monkeypatch):
    monkeypatch.setattr(
        "mailfallback.services.dovecot_manager.settings.dovecot_api_url", "http://dovecot:8080"
    )
    monkeypatch.setattr("mailfallback.services.dovecot_manager.settings.dovecot_api_key", "key")

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    with patch("mailfallback.services.dovecot_manager.httpx.post", return_value=mock_response):
        result = check_dovecot_health()

    assert result["ok"] is True


def test_check_dovecot_health_failure(monkeypatch):
    monkeypatch.setattr(
        "mailfallback.services.dovecot_manager.settings.dovecot_api_url", "http://dovecot:8080"
    )
    monkeypatch.setattr("mailfallback.services.dovecot_manager.settings.dovecot_api_key", "key")

    with patch(
        "mailfallback.services.dovecot_manager.httpx.post",
        side_effect=Exception("connection refused"),
    ):
        result = check_dovecot_health()

    assert result["ok"] is False
    assert "connection refused" in result["error"]


def test_fts_rescan_disabled(monkeypatch):
    monkeypatch.setattr("mailfallback.services.dovecot_manager.settings.dovecot_api_url", "")
    result = fts_rescan("testuser")
    assert result["ok"] is False


def test_fts_rescan_success(monkeypatch):
    monkeypatch.setattr(
        "mailfallback.services.dovecot_manager.settings.dovecot_api_url", "http://dovecot:8080"
    )
    monkeypatch.setattr("mailfallback.services.dovecot_manager.settings.dovecot_api_key", "key")

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = [["doveadmResponse", {}, "fts1"]]

    with patch("mailfallback.services.dovecot_manager.httpx.post", return_value=mock_response):
        result = fts_rescan("testuser")

    assert result["ok"] is True


def test_fts_rescan_error_response(monkeypatch):
    monkeypatch.setattr(
        "mailfallback.services.dovecot_manager.settings.dovecot_api_url", "http://dovecot:8080"
    )
    monkeypatch.setattr("mailfallback.services.dovecot_manager.settings.dovecot_api_key", "key")

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = [["error", {"exitCode": "75"}]]

    with patch("mailfallback.services.dovecot_manager.httpx.post", return_value=mock_response):
        result = fts_rescan("testuser")

    assert result["ok"] is False
    assert "75" in result["error"]


def test_force_resync_disabled(monkeypatch):
    monkeypatch.setattr("mailfallback.services.dovecot_manager.settings.dovecot_api_url", "")
    result = force_resync("testuser")
    assert result["ok"] is False


def test_force_resync_success(monkeypatch):
    monkeypatch.setattr(
        "mailfallback.services.dovecot_manager.settings.dovecot_api_url", "http://dovecot:8080"
    )
    monkeypatch.setattr("mailfallback.services.dovecot_manager.settings.dovecot_api_key", "key")

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = [["doveadmResponse", {}, "resync1"]]

    with patch("mailfallback.services.dovecot_manager.httpx.post", return_value=mock_response):
        result = force_resync("testuser")

    assert result["ok"] is True


def test_force_resync_failure(monkeypatch):
    monkeypatch.setattr(
        "mailfallback.services.dovecot_manager.settings.dovecot_api_url", "http://dovecot:8080"
    )
    monkeypatch.setattr("mailfallback.services.dovecot_manager.settings.dovecot_api_key", "key")

    with patch(
        "mailfallback.services.dovecot_manager.httpx.post",
        side_effect=Exception("timeout"),
    ):
        result = force_resync("testuser")

    assert result["ok"] is False
    assert "timeout" in result["error"]
