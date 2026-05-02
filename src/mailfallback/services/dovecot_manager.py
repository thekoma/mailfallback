import logging

import httpx

from mailfallback.config import settings

logger = logging.getLogger(__name__)


def _doveadm_auth() -> httpx.BasicAuth:
    return httpx.BasicAuth("doveadm", settings.dovecot_api_key)


def reload_dovecot() -> bool:
    """Send reload command to Dovecot via doveadm HTTP API. Returns True on success."""
    if not settings.dovecot_enabled:
        return False

    url = f"{settings.dovecot_api_url}/doveadm/v1"

    try:
        resp = httpx.post(
            url,
            json=[["reload", {}, "tag1"]],
            auth=_doveadm_auth(),
        )
        resp.raise_for_status()
        return True
    except Exception:
        logger.warning("Failed to reload Dovecot", exc_info=True)
        return False


def get_mailbox_stats(username: str) -> list[dict] | None:
    """Get per-folder messages, unseen, and vsize from Dovecot doveadm HTTP API.

    Returns a list of dicts with keys: mailbox, messages, unseen, vsize.
    Returns None if Dovecot is unavailable.
    """
    if not settings.dovecot_enabled:
        return None

    url = f"{settings.dovecot_api_url}/doveadm/v1"

    try:
        resp = httpx.post(
            url,
            json=[
                [
                    "mailboxStatus",
                    {
                        "user": username,
                        "field": ["messages", "unseen", "vsize"],
                        "mailboxMask": ["*", "*/*"],
                    },
                    "tag1",
                ]
            ],
            auth=_doveadm_auth(),
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        logger.debug("Doveadm mailboxStatus response for %s: %s", username, data)
        if not data or not isinstance(data[0], list) or len(data[0]) < 2:
            logger.debug("Unexpected response structure for %s", username)
            return None
        if data[0][0] == "error":
            logger.warning(
                "Doveadm error for %s: %s",
                username,
                data[0][1] if len(data[0]) > 1 else "unknown",
            )
            return None
        results = data[0][1]
        if not isinstance(results, list):
            logger.debug("Results not a list for %s: %s", username, type(results))
            return None
        logger.debug("Got %d mailbox entries for %s", len(results), username)
        return [
            {
                "mailbox": entry["mailbox"],
                "messages": int(entry.get("messages", 0)),
                "unseen": int(entry.get("unseen", 0)),
                "vsize": int(entry.get("vsize", 0)),
            }
            for entry in results
            if isinstance(entry, dict) and "mailbox" in entry
        ]
    except Exception:
        logger.warning("Failed to get mailbox stats for %s", username, exc_info=True)
        return None
