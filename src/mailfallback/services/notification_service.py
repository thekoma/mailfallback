# src/mailfallback/services/notification_service.py
import logging
import threading

from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.models import Account, NotificationChannel
from mailfallback.security import decrypt_credentials

logger = logging.getLogger(__name__)

EVENT_KEYS = ("needs_reauth", "sync_error", "sync_paused", "stale")


def send_to_channel(channel: NotificationChannel, title: str, body: str) -> bool:
    """Decrypt the channel's Apprise URL and send one notification.
    Never raises; returns delivery success."""
    try:
        import apprise

        url = decrypt_credentials(channel.apprise_url, settings.secret_key)
        ap = apprise.Apprise()
        if not ap.add(url):
            logger.warning("Notification channel %s: invalid Apprise URL", channel.id)
            return False
        return bool(ap.notify(title=title, body=body))
    except Exception:
        logger.warning("Notification channel %s send failed", channel.id, exc_info=True)
        return False


def clear_notified_state(account: Account) -> None:
    account.last_notified_state = None


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
        if not owner_ids:
            return
        channels = (
            db.query(NotificationChannel)
            .filter(
                NotificationChannel.user_id.in_(owner_ids),
                NotificationChannel.enabled.is_(True),
            )
            .all()
        )
        targets = [c for c in channels if event_key in (c.events or [])]
        for ch in targets:
            # Fire-and-forget: a slow/blocking channel must not stall the
            # caller (sync worker / scheduler). Best-effort, logged.
            threading.Thread(target=send_to_channel, args=(ch, title, body), daemon=True).start()
    except Exception:
        logger.warning("notify_account_problem failed for %s", account.id, exc_info=True)
