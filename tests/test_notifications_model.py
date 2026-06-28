from mailfallback.models import Account, NotificationChannel


def test_notification_channel_columns():
    cols = NotificationChannel.__table__.c
    assert {"id", "user_id", "label", "apprise_url", "enabled", "events", "created_at"} <= set(
        cols.keys()
    )


def test_account_last_notified_state_column():
    assert "last_notified_state" in Account.__table__.c


def test_notification_channel_payload_format_column_exists():
    assert "payload_format" in NotificationChannel.__table__.c


def test_notification_channel_payload_format_default():
    col = NotificationChannel.__table__.c["payload_format"]
    assert col.server_default is not None
    assert str(col.server_default.arg) == "text"


def test_notification_channel_payload_format_not_nullable():
    col = NotificationChannel.__table__.c["payload_format"]
    assert col.nullable is False


def test_notification_channel_payload_format_explicit_json(db_session, default_store):
    """A channel created with payload_format='json' retains that value."""
    from mailfallback.models import User

    user = User(username="test_pf_user", password_hash="x", store_id=default_store.id)
    db_session.add(user)
    db_session.flush()

    channel = NotificationChannel(
        user_id=user.id,
        label="test-json",
        apprise_url="enc-json://example",
        payload_format="json",
    )
    db_session.add(channel)
    db_session.flush()
    db_session.refresh(channel)

    assert channel.payload_format == "json"


def test_notification_channel_payload_format_default_orm(db_session, default_store):
    """A channel created without specifying payload_format defaults to 'text' in the ORM."""
    from mailfallback.models import User

    user = User(username="test_pf_default_user", password_hash="x", store_id=default_store.id)
    db_session.add(user)
    db_session.flush()

    channel = NotificationChannel(
        user_id=user.id,
        label="test-text-default",
        apprise_url="enc-text://example",
    )
    db_session.add(channel)
    db_session.flush()
    db_session.refresh(channel)

    assert channel.payload_format == "text"
