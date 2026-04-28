from mailfallback.config import Settings


def test_default_settings():
    s = Settings()
    assert s.database_url == "sqlite:////data/config/mailfallback.db"
    assert s.mbsync_binary == "mbsync"
    assert s.oidc_enabled is False


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("MAILFALLBACK_DATABASE_URL", "postgresql://localhost/mf")
    monkeypatch.setenv("MAILFALLBACK_OIDC_ENABLED", "true")
    s = Settings()
    assert s.database_url == "postgresql://localhost/mf"
    assert s.oidc_enabled is True


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
