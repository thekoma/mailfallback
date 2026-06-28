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


def test_update_channel_changes_events_and_audits(client, db_session, default_store):
    _login(client, db_session, default_store)
    client.post(
        "/profile/notifications",
        data={"label": "Phone", "apprise_url": "ntfy://host/topic", "events": ["needs_reauth"]},
        follow_redirects=False,
    )
    ch = db_session.query(NotificationChannel).filter_by(label="Phone").first()
    cid = ch.id
    # Change label + events; leave URL blank (keep existing)
    client.post(
        f"/profile/notifications/{cid}/update",
        data={"label": "My Phone", "apprise_url": "", "events": ["sync_error", "stale"]},
        follow_redirects=False,
    )
    db_session.expire_all()
    ch = db_session.query(NotificationChannel).filter_by(id=cid).first()
    assert ch.label == "My Phone"
    assert set(ch.events) == {"sync_error", "stale"}
    # Blank URL means keep the original (still decryptable to the original)
    assert decrypt_credentials(ch.apprise_url, settings.secret_key) == "ntfy://host/topic"
    assert (
        db_session.query(AuditLog).filter_by(action="user.notification_channel_update").count() == 1
    )


def test_update_channel_replaces_url_when_provided(client, db_session, default_store):
    _login(client, db_session, default_store)
    client.post(
        "/profile/notifications",
        data={"label": "Phone", "apprise_url": "ntfy://host/topic", "events": ["needs_reauth"]},
        follow_redirects=False,
    )
    ch = db_session.query(NotificationChannel).filter_by(label="Phone").first()
    cid = ch.id
    client.post(
        f"/profile/notifications/{cid}/update",
        data={"label": "Phone", "apprise_url": "tgram://newtoken/123", "events": ["needs_reauth"]},
        follow_redirects=False,
    )
    db_session.expire_all()
    ch = db_session.query(NotificationChannel).filter_by(id=cid).first()
    assert decrypt_credentials(ch.apprise_url, settings.secret_key) == "tgram://newtoken/123"


def test_update_channel_scoped_to_owner(client, db_session, default_store):
    from mailfallback.security import encrypt_credentials

    other = create_user(
        db_session, "other", "secretpass123", UserRole.user, store_id=default_store.id
    )
    foreign = NotificationChannel(
        user_id=other.id,
        label="Foreign",
        apprise_url=encrypt_credentials("ntfy://x/y", settings.secret_key),
        events=["stale"],
    )
    db_session.add(foreign)
    db_session.commit()
    fid = foreign.id
    _login(client, db_session, default_store)  # logs in as "u", not "other"
    client.post(
        f"/profile/notifications/{fid}/update",
        data={"label": "Hacked", "events": []},
        follow_redirects=False,
    )
    db_session.expire_all()
    # The other user's channel is untouched
    assert db_session.query(NotificationChannel).filter_by(id=fid).first().label == "Foreign"


def test_profile_get_masks_notification_urls(client, db_session, default_store):
    """Assert that GET /profile masks secret tokens in notification URLs."""
    _login(client, db_session, default_store)
    # POST a channel with a secret-bearing URL
    client.post(
        "/profile/notifications",
        data={
            "label": "Ntfy",
            "apprise_url": "ntfy://secrettoken@ntfy.example.com/mytopic",
            "events": ["sync_error"],
        },
        follow_redirects=False,
    )
    # GET profile page
    resp = client.get("/profile")
    assert resp.status_code == 200
    # Verify secrets are NOT present
    assert "secrettoken" not in resp.text
    assert "mytopic" not in resp.text
    # Verify masked form IS present
    assert "ntfy://…" in resp.text


def test_profile_shows_apprise_docs_and_examples(client, db_session, default_store):
    """The notifications section links to the Apprise docs and shows copyable URL examples."""
    _login(client, db_session, default_store)
    resp = client.get("/profile")
    assert resp.status_code == 200
    # Link to the Apprise documentation
    assert "https://github.com/caronc/apprise" in resp.text
    # At least a couple of concrete, copyable URL examples
    assert 'data-copy="ntfy://ntfy.sh/your-topic"' in resp.text
    assert 'data-copy="tgram://bottoken/ChatID"' in resp.text
