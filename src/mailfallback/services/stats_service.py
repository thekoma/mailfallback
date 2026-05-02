import json
import logging

from sqlalchemy.orm import Session

from mailfallback.models import Account

logger = logging.getLogger(__name__)


def collect_account_stats(db: Session, account: Account) -> None:
    """Collect mailbox statistics from Dovecot after a successful sync.

    Uses doveadm HTTP API to get messages, unseen, and vsize per folder.
    Each account has a namespace prefix "Name (email)/" — filters by that.
    Never raises — failures are logged and silently ignored.
    """
    try:
        if not account.owners:
            return

        from mailfallback.services.dovecot_manager import get_mailbox_stats

        owner = account.owners[0]
        stats = get_mailbox_stats(owner.username)
        if not stats:
            return

        short_id = account.id[-4:]
        prefix = f"{account.name} ({account.email_address}) [{short_id}]/"
        logger.debug("Filtering stats for account %s with prefix '%s'", account.name, prefix)

        folder_stats = []
        for entry in stats:
            mailbox = entry["mailbox"]
            if not mailbox.startswith(prefix):
                continue
            folder_name = mailbox[len(prefix) :]
            folder_stats.append(
                {
                    "name": folder_name,
                    "messages": entry["messages"],
                    "unread": entry["unseen"],
                    "size_bytes": entry["vsize"],
                }
            )

        account.total_messages = sum(f["messages"] for f in folder_stats)
        account.unread_messages = sum(f["unread"] for f in folder_stats)
        account.maildir_size_bytes = sum(f["size_bytes"] for f in folder_stats)
        account.folder_stats = json.dumps(folder_stats) if folder_stats else None

        db.commit()
        logger.info(
            "Stats for %s: %d messages (%d unread), %d folders",
            account.name,
            account.total_messages,
            account.unread_messages,
            len(folder_stats),
        )

    except Exception:
        logger.warning("Failed to collect stats for %s", account.name, exc_info=True)
        db.rollback()
