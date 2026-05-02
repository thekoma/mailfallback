from unittest.mock import MagicMock, patch

from mailfallback.services.dovecot_manager import (
    get_mailbox_stats,
    reload_dovecot,
)


def test_reload_dovecot_success(monkeypatch):
    monkeypatch.setattr("mailfallback.services.dovecot_manager.settings.dovecot_enabled", True)
    monkeypatch.setattr(
        "mailfallback.services.dovecot_manager.settings.dovecot_api_url", "http://dovecot:8080"
    )
    monkeypatch.setattr(
        "mailfallback.services.dovecot_manager.settings.dovecot_api_key", "secretkey"
    )

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    with patch(
        "mailfallback.services.dovecot_manager.httpx.post", return_value=mock_response
    ) as mock_post:
        result = reload_dovecot()

    assert result is True
    mock_post.assert_called_once()

    call_args = mock_post.call_args
    assert call_args[0][0] == "http://dovecot:8080/doveadm/v1"
    assert call_args[1]["json"] == [["reload", {}, "tag1"]]

    assert "auth" in call_args[1]


def test_reload_dovecot_failure(monkeypatch):
    monkeypatch.setattr("mailfallback.services.dovecot_manager.settings.dovecot_enabled", True)
    monkeypatch.setattr(
        "mailfallback.services.dovecot_manager.settings.dovecot_api_url", "http://dovecot:8080"
    )
    monkeypatch.setattr("mailfallback.services.dovecot_manager.settings.dovecot_api_key", "key")

    with patch(
        "mailfallback.services.dovecot_manager.httpx.post",
        side_effect=Exception("connection refused"),
    ):
        result = reload_dovecot()

    assert result is False


def test_reload_dovecot_disabled(monkeypatch):
    monkeypatch.setattr("mailfallback.services.dovecot_manager.settings.dovecot_enabled", False)

    with patch("mailfallback.services.dovecot_manager.httpx.post") as mock_post:
        result = reload_dovecot()

    assert result is False
    mock_post.assert_not_called()


def test_get_mailbox_stats_success(monkeypatch):
    monkeypatch.setattr("mailfallback.services.dovecot_manager.settings.dovecot_enabled", True)
    monkeypatch.setattr(
        "mailfallback.services.dovecot_manager.settings.dovecot_api_url", "http://dovecot:8080"
    )
    monkeypatch.setattr("mailfallback.services.dovecot_manager.settings.dovecot_api_key", "key")

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = [
        [
            "doveadmResponse",
            [
                {"mailbox": "INBOX", "messages": "5", "unseen": "2", "vsize": "1024"},
                {"mailbox": "Sent", "messages": "10", "unseen": "0", "vsize": "512"},
            ],
            "tag1",
        ]
    ]

    with patch("mailfallback.services.dovecot_manager.httpx.post", return_value=mock_response):
        result = get_mailbox_stats("testuser")

    assert result == [
        {"mailbox": "INBOX", "messages": 5, "unseen": 2, "vsize": 1024},
        {"mailbox": "Sent", "messages": 10, "unseen": 0, "vsize": 512},
    ]


def test_get_mailbox_stats_disabled(monkeypatch):
    monkeypatch.setattr("mailfallback.services.dovecot_manager.settings.dovecot_enabled", False)
    assert get_mailbox_stats("testuser") is None


def test_get_mailbox_stats_failure(monkeypatch):
    monkeypatch.setattr("mailfallback.services.dovecot_manager.settings.dovecot_enabled", True)
    monkeypatch.setattr(
        "mailfallback.services.dovecot_manager.settings.dovecot_api_url", "http://dovecot:8080"
    )
    monkeypatch.setattr("mailfallback.services.dovecot_manager.settings.dovecot_api_key", "key")

    with patch(
        "mailfallback.services.dovecot_manager.httpx.post",
        side_effect=Exception("connection refused"),
    ):
        result = get_mailbox_stats("testuser")

    assert result is None
