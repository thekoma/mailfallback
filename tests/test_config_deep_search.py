from mailfallback.config import Settings


def test_deep_search_timeout_default():
    s = Settings()
    assert s.deep_search_timeout_seconds == 10


def test_deep_search_timeout_env_override(monkeypatch):
    monkeypatch.setenv("MAILFALLBACK_DEEP_SEARCH_TIMEOUT_SECONDS", "3")
    s = Settings()
    assert s.deep_search_timeout_seconds == 3
