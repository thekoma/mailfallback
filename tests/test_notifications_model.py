from mailfallback.models import Account, NotificationChannel


def test_notification_channel_columns():
    cols = NotificationChannel.__table__.c
    assert {"id", "user_id", "label", "apprise_url", "enabled", "events", "created_at"} <= set(
        cols.keys()
    )


def test_account_last_notified_state_column():
    assert "last_notified_state" in Account.__table__.c
