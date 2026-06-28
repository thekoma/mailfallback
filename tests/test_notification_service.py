# tests/test_notification_service.py
from unittest.mock import patch

from mailfallback.config import settings
from mailfallback.models import NotificationChannel, UserRole
from mailfallback.security import encrypt_credentials
from mailfallback.services import notification_service as ns


class _SyncThread:
    """Replacement for threading.Thread that runs synchronously — keeps tests deterministic."""

    def __init__(self, target, args=(), daemon=None):
        self._t = target
        self._a = args

    def start(self):
        self._t(*self._a)


def _channel(user_id, events, enabled=True, url="ntfy://host/topic"):
    return NotificationChannel(
        user_id=user_id,
        label="c",
        apprise_url=encrypt_credentials(url, settings.secret_key),
        enabled=enabled,
        events=events,
    )


def test_notify_sends_to_subscribed_enabled_channels_only(db_session, default_store):
    from mailfallback.services.account_service import assign_owner, create_account
    from mailfallback.services.user_service import create_user

    user = create_user(db_session, "u", "p", UserRole.user, store_id=default_store.id)
    acct = create_account(db_session, "G", "imap.gmail.com", 993, "oauth2", store=default_store)
    assign_owner(db_session, acct.id, user.id)
    db_session.add(_channel(user.id, ["needs_reauth"]))  # subscribed
    db_session.add(_channel(user.id, ["sync_error"]))  # not subscribed
    db_session.add(_channel(user.id, ["needs_reauth"], enabled=False))  # disabled
    db_session.commit()

    sent = []
    with (
        patch.object(ns.threading, "Thread", _SyncThread),
        patch.object(ns, "send_to_channel", lambda ch, t, b: sent.append(ch.events) or True),
    ):
        ns.notify_account_problem(db_session, acct, "needs_reauth", "t", "b")
    assert sent == [["needs_reauth"]]  # only the enabled, subscribed channel


def test_notify_is_deduped_until_recovery(db_session, default_store):
    from mailfallback.services.account_service import assign_owner, create_account
    from mailfallback.services.user_service import create_user

    user = create_user(db_session, "u", "p", UserRole.user, store_id=default_store.id)
    acct = create_account(db_session, "G", "imap.gmail.com", 993, "oauth2", store=default_store)
    assign_owner(db_session, acct.id, user.id)
    db_session.add(_channel(user.id, ["needs_reauth"]))
    db_session.commit()

    calls = []
    with (
        patch.object(ns.threading, "Thread", _SyncThread),
        patch.object(ns, "send_to_channel", lambda ch, t, b: calls.append(1) or True),
    ):
        ns.notify_account_problem(db_session, acct, "needs_reauth", "t", "b")
        ns.notify_account_problem(db_session, acct, "needs_reauth", "t", "b")  # deduped
        assert len(calls) == 1
        ns.clear_notified_state(acct)
        db_session.commit()
        ns.notify_account_problem(db_session, acct, "needs_reauth", "t", "b")  # fires again
        assert len(calls) == 2


def test_notify_is_best_effort_on_apprise_failure(db_session, default_store):
    from mailfallback.services.account_service import assign_owner, create_account
    from mailfallback.services.user_service import create_user

    user = create_user(db_session, "u", "p", UserRole.user, store_id=default_store.id)
    acct = create_account(db_session, "G", "imap.gmail.com", 993, "oauth2", store=default_store)
    assign_owner(db_session, acct.id, user.id)
    db_session.add(_channel(user.id, ["needs_reauth"]))
    db_session.commit()

    with (
        patch.object(ns.threading, "Thread", _SyncThread),
        patch.object(ns, "send_to_channel", side_effect=RuntimeError("boom")),
    ):
        ns.notify_account_problem(db_session, acct, "needs_reauth", "t", "b")  # must not raise
    db_session.refresh(acct)
    assert acct.last_notified_state == "needs_reauth"  # marker still set
