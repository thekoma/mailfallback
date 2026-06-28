# tests/test_notifications_ui.py
from unittest.mock import patch

from mailfallback.config import settings
from mailfallback.models import AuditLog, NotificationChannel, UserRole
from mailfallback.security import decrypt_credentials
from mailfallback.services.user_service import create_user


def _login(client, db, store):
    create_user(db, "u", "secretpass123", UserRole.user, store_id=store.id)
    client.post(
        "/login",
        data={"username": "u", "password": "secretpass123"},
        follow_redirects=False,
    )


def test_add_channel_encrypts_and_audits(client, db_session, default_store):
    _login(client, db_session, default_store)
    resp = client.post(
        "/profile/notifications",
        data={"label": "Phone", "apprise_url": "ntfy://host/topic", "events": ["needs_reauth"]},
        follow_redirects=False,
    )
    assert resp.status_code in (200, 303)
    ch = db_session.query(NotificationChannel).filter_by(label="Phone").first()
    assert ch is not None
    assert ch.apprise_url != "ntfy://host/topic"  # encrypted at rest
    assert decrypt_credentials(ch.apprise_url, settings.secret_key) == "ntfy://host/topic"
    assert ch.events == ["needs_reauth"]
    assert db_session.query(AuditLog).filter_by(action="user.notification_channel_add").count() == 1


def test_test_send_invokes_apprise(client, db_session, default_store):
    _login(client, db_session, default_store)
    client.post(
        "/profile/notifications",
        data={"label": "Phone", "apprise_url": "ntfy://host/topic", "events": ["needs_reauth"]},
        follow_redirects=False,
    )
    ch = db_session.query(NotificationChannel).filter_by(label="Phone").first()
    with patch(
        "mailfallback.services.notification_service.send_to_channel", return_value=True
    ) as m:
        resp = client.post(f"/profile/notifications/{ch.id}/test")
    assert resp.status_code == 200
    m.assert_called_once()
