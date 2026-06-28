# src/mailfallback/services/notification_service.py
import json
import logging
import threading
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.models import Account, NotificationChannel
from mailfallback.security import decrypt_credentials

logger = logging.getLogger(__name__)

PROBLEM_EVENT_KEYS = ("needs_reauth", "sync_error", "sync_paused", "stale")
ACTIVITY_EVENT_KEYS = (
    "sync_completed",
    "initial_sync_completed",
    "restore_completed",
    "backup_completed",
    "account_added",
)
EVENT_KEYS = PROBLEM_EVENT_KEYS + ACTIVITY_EVENT_KEYS


def send_to_channel(
    channel: NotificationChannel,
    title: str,
    body: str,
    *,
    event_key: str | None = None,
    account_email: str | None = None,
    details: dict | None = None,
) -> bool:
    """Decrypt the channel's Apprise URL and send one notification.
    Never raises; returns delivery success."""
    try:
        import apprise

        url = decrypt_credentials(channel.apprise_url, settings.secret_key)
        ap = apprise.Apprise()
        if not ap.add(url):
            logger.warning("Notification channel %s: invalid Apprise URL", channel.id)
            return False

        if channel.payload_format == "json":
            body = json.dumps(
                {
                    "event": event_key,
                    "account": account_email,
                    "title": title,
                    "message": body,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "details": details or {},
                }
            )

        return bool(ap.notify(title=title, body=body))
    except Exception:
        logger.warning("Notification channel %s send failed", channel.id, exc_info=True)
        return False


def clear_notified_state(account: Account) -> None:
    account.last_notified_state = None


def _send_to_users(
    db: Session,
    user_ids: list,
    event_key: str,
    title: str,
    body: str,
    details: dict | None = None,
    account_email: str | None = None,
) -> None:
    """Query enabled channels for the given user ids subscribed to event_key
    and fire a daemon thread per channel. Never raises."""
    try:
        if not user_ids:
            return
        channels = (
            db.query(NotificationChannel)
            .filter(
                NotificationChannel.user_id.in_(user_ids),
                NotificationChannel.enabled.is_(True),
            )
            .all()
        )
        targets = [c for c in channels if event_key in (c.events or [])]
        for ch in targets:
            threading.Thread(
                target=send_to_channel,
                args=(ch, title, body),
                kwargs={
                    "event_key": event_key,
                    "account_email": account_email,
                    "details": details,
                },
                daemon=True,
            ).start()
    except Exception:
        logger.warning("_send_to_users failed for event %s", event_key, exc_info=True)


def notify_account_problem(
    db: Session, account: Account, event_key: str, title: str, body: str
) -> None:
    """Notify the account's owners on their enabled channels subscribed to
    event_key — once per entering this state (deduped via
    account.last_notified_state). Never raises into the caller."""
    try:
        if account.last_notified_state == event_key:
            return
        account.last_notified_state = event_key
        owner_ids = [o.id for o in account.owners]
        _send_to_users(
            db,
            owner_ids,
            event_key,
            title,
            body,
            details=None,
            account_email=account.email_address,
        )
    except Exception:
        logger.warning("notify_account_problem failed for %s", account.id, exc_info=True)


def notify_account_event(
    db: Session,
    account: Account,
    event_key: str,
    title: str,
    body: str,
    details: dict | None = None,
) -> None:
    """Notify the account's owners on a lifecycle event — NOT deduped.
    Every call fires if a matching channel exists. Never raises."""
    try:
        owner_ids = [o.id for o in account.owners]
        _send_to_users(
            db,
            owner_ids,
            event_key,
            title,
            body,
            details=details,
            account_email=account.email_address,
        )
    except Exception:
        logger.warning("notify_account_event failed for %s", account.id, exc_info=True)


def notify_users(
    db: Session,
    user_ids: list,
    event_key: str,
    title: str,
    body: str,
    details: dict | None = None,
) -> None:
    """Notify a specific list of users' enabled channels for the given event.
    Never raises."""
    try:
        _send_to_users(db, user_ids, event_key, title, body, details=details, account_email=None)
    except Exception:
        logger.warning("notify_users failed for event %s", event_key, exc_info=True)
