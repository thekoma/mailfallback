from mailfallback.config import Settings


def test_default_settings():
    s = Settings()
    assert s.database_url == "postgresql://mailfallback:mailfallback@db:5432/mailfallback"
    assert s.mbsync_binary == "mbsync"
    assert s.oidc_enabled is False


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("MAILFALLBACK_DATABASE_URL", "postgresql://localhost/mf")
    monkeypatch.setenv("MAILFALLBACK_OIDC_ENABLED", "true")
    s = Settings()
    assert s.database_url == "postgresql://localhost/mf"
    assert s.oidc_enabled is True


def test_dovecot_settings_defaults():
    from mailfallback.config import Settings

    s = Settings(secret_key="test", session_secret="test", _env_file=None)
    assert s.dovecot_api_url == "http://dovecot:8080"
    assert s.dovecot_api_key == ""


def test_webmail_url_defaults():
    s = Settings(secret_key="test", session_secret="test", _env_file=None)
    assert s.webmail_url == ""


def test_webmail_url_from_env(monkeypatch):
    monkeypatch.setenv("MAILFALLBACK_WEBMAIL_URL", "http://localhost:8001")
    s = Settings()
    assert s.webmail_url == "http://localhost:8001"


def test_sync_max_workers_default():
    s = Settings(secret_key="test", session_secret="test", _env_file=None)
    assert s.sync_max_workers == 4


def test_sync_max_workers_from_env(monkeypatch):
    monkeypatch.setenv("MAILFALLBACK_SYNC_MAX_WORKERS", "8")
    s = Settings()
    assert s.sync_max_workers == 8


def test_tika_settings_defaults():
    s = Settings()
    assert s.tika_enabled is False
    assert s.tika_url == "http://tika:9998"


def test_webmail_settings_defaults():
    s = Settings()
    assert s.webmail_enabled is False
    assert s.confs_path == "/confs"
