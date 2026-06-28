# tests/test_notification_service.py
import json
from unittest.mock import MagicMock, patch

from mailfallback.config import settings
from mailfallback.models import NotificationChannel, UserRole
from mailfallback.security import encrypt_credentials
from mailfallback.services import notification_service as ns


class _SyncThread:
    """Replacement for threading.Thread that runs synchronously — keeps tests deterministic."""

    def __init__(self, target, args=(), kwargs=None, daemon=None):
        self._t = target
        self._a = args
        self._kw = kwargs or {}

    def start(self):
        self._t(*self._a, **self._kw)


def _channel(user_id, events, enabled=True, url="ntfy://host/topic", payload_format="text"):
    return NotificationChannel(
        user_id=user_id,
        label="c",
        apprise_url=encrypt_credentials(url, settings.secret_key),
        enabled=enabled,
        events=events,
        payload_format=payload_format,
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
        patch.object(ns, "send_to_channel", lambda ch, t, b, **kw: sent.append(ch.events) or True),
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
        patch.object(ns, "send_to_channel", lambda ch, t, b, **kw: calls.append(1) or True),
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


# ---------------------------------------------------------------------------
# New tests — notify_account_event, notify_users, JSON payload, best-effort
# ---------------------------------------------------------------------------


def test_notify_account_event_fires_every_call(db_session, default_store):
    """notify_account_event must NOT dedup — each call fires the channel."""
    from mailfallback.services.account_service import assign_owner, create_account
    from mailfallback.services.user_service import create_user

    user = create_user(db_session, "u2", "p", UserRole.user, store_id=default_store.id)
    acct = create_account(db_session, "G2", "imap.gmail.com", 993, "oauth2", store=default_store)
    assign_owner(db_session, acct.id, user.id)
    db_session.add(_channel(user.id, ["sync_completed"]))
    db_session.commit()

    calls = []
    with (
        patch.object(ns.threading, "Thread", _SyncThread),
        patch.object(ns, "send_to_channel", lambda ch, t, b, **kw: calls.append(1) or True),
    ):
        ns.notify_account_event(db_session, acct, "sync_completed", "Done", "body")
        ns.notify_account_event(db_session, acct, "sync_completed", "Done", "body")
    assert len(calls) == 2  # no dedup


def test_notify_account_problem_dedupes_vs_event_does_not(db_session, default_store):
    """Contrast: problem dedupes, event does not."""
    from mailfallback.services.account_service import assign_owner, create_account
    from mailfallback.services.user_service import create_user

    user = create_user(db_session, "u3", "p", UserRole.user, store_id=default_store.id)
    acct = create_account(db_session, "G3", "imap.gmail.com", 993, "oauth2", store=default_store)
    assign_owner(db_session, acct.id, user.id)
    db_session.add(_channel(user.id, ["sync_error", "sync_completed"]))
    db_session.commit()

    problem_calls = []
    event_calls = []
    with (
        patch.object(ns.threading, "Thread", _SyncThread),
        patch.object(
            ns,
            "send_to_channel",
            lambda ch, t, b, **kw: (
                (problem_calls if t == "prob" else event_calls).append(1) or True
            ),
        ),
    ):
        ns.notify_account_problem(db_session, acct, "sync_error", "prob", "b")
        ns.notify_account_problem(db_session, acct, "sync_error", "prob", "b")  # deduped
        ns.notify_account_event(db_session, acct, "sync_completed", "ev", "b")
        ns.notify_account_event(db_session, acct, "sync_completed", "ev", "b")

    assert len(problem_calls) == 1  # deduped
    assert len(event_calls) == 2  # not deduped


def test_only_enabled_subscribed_channels_fire_for_event(db_session, default_store):
    """Only enabled channels subscribed to the event key fire."""
    from mailfallback.services.account_service import assign_owner, create_account
    from mailfallback.services.user_service import create_user

    user = create_user(db_session, "u4", "p", UserRole.user, store_id=default_store.id)
    acct = create_account(db_session, "G4", "imap.gmail.com", 993, "oauth2", store=default_store)
    assign_owner(db_session, acct.id, user.id)
    db_session.add(_channel(user.id, ["sync_completed"]))  # matches
    db_session.add(_channel(user.id, ["backup_completed"]))  # wrong event
    db_session.add(_channel(user.id, ["sync_completed"], enabled=False))  # disabled
    db_session.commit()

    sent = []
    with (
        patch.object(ns.threading, "Thread", _SyncThread),
        patch.object(ns, "send_to_channel", lambda ch, t, b, **kw: sent.append(ch.events) or True),
    ):
        ns.notify_account_event(db_session, acct, "sync_completed", "t", "b")
    assert sent == [["sync_completed"]]


def test_json_payload_format(db_session, default_store):
    """JSON-format channels get a JSON body; text-format channels get the raw body."""
    from mailfallback.services.account_service import assign_owner, create_account
    from mailfallback.services.user_service import create_user

    user = create_user(db_session, "u5", "p", UserRole.user, store_id=default_store.id)
    acct = create_account(
        db_session,
        "label5",
        "imap.gmail.com",
        993,
        "oauth2",
        store=default_store,
        email_address="test@example.com",
    )
    assign_owner(db_session, acct.id, user.id)
    db_session.add(_channel(user.id, ["sync_completed"], payload_format="json"))
    db_session.add(_channel(user.id, ["sync_completed"], payload_format="text"))
    db_session.commit()

    captured_bodies = []

    def _fake_apprise_cls():
        ap = MagicMock()
        ap.add.return_value = True
        ap.notify.side_effect = lambda title, body: captured_bodies.append(body) or True
        return ap

    with (
        patch.object(ns.threading, "Thread", _SyncThread),
        patch("apprise.Apprise", _fake_apprise_cls),
    ):
        ns.notify_account_event(db_session, acct, "sync_completed", "Sync done", "All mail synced")

    assert len(captured_bodies) == 2
    # One body should be JSON, one should be the raw string
    json_bodies = []
    text_bodies = []
    for b in captured_bodies:
        try:
            parsed = json.loads(b)
            json_bodies.append(parsed)
        except (json.JSONDecodeError, TypeError):
            text_bodies.append(b)

    assert len(json_bodies) == 1
    assert len(text_bodies) == 1
    assert json_bodies[0]["event"] == "sync_completed"
    # account is now a rich object, not a bare email string
    assert json_bodies[0]["account"]["email"] == "test@example.com"
    assert json_bodies[0]["account"]["name"] == "label5"
    assert json_bodies[0]["account"]["provider"]
    assert "timestamp" in json_bodies[0]
    assert text_bodies[0] == "All mail synced"


def test_notify_users_resolves_given_user_ids(db_session, default_store):
    """notify_users sends to channels owned by the specified user ids."""
    from mailfallback.services.user_service import create_user

    user_a = create_user(db_session, "ua", "p", UserRole.user, store_id=default_store.id)
    user_b = create_user(db_session, "ub", "p", UserRole.user, store_id=default_store.id)
    db_session.add(_channel(user_a.id, ["backup_completed"]))
    db_session.add(_channel(user_b.id, ["backup_completed"]))
    db_session.commit()

    sent_users = []
    with (
        patch.object(ns.threading, "Thread", _SyncThread),
        patch.object(
            ns, "send_to_channel", lambda ch, t, b, **kw: sent_users.append(ch.user_id) or True
        ),
    ):
        ns.notify_users(db_session, [user_a.id], "backup_completed", "t", "b")

    assert sent_users == [user_a.id]  # only user_a's channel fires


def test_notify_users_best_effort_on_failure(db_session, default_store):
    """A raising send_to_channel inside notify_users must not propagate."""
    from mailfallback.services.user_service import create_user

    user = create_user(db_session, "uc", "p", UserRole.user, store_id=default_store.id)
    db_session.add(_channel(user.id, ["backup_completed"]))
    db_session.commit()

    with (
        patch.object(ns.threading, "Thread", _SyncThread),
        patch.object(ns, "send_to_channel", side_effect=RuntimeError("boom")),
    ):
        ns.notify_users(db_session, [user.id], "backup_completed", "t", "b")  # must not raise
