"""Folders that vanished from the Source: naming, detection, quarantine.

Kept out of sync_worker deliberately. The rules here — what counts as
"removed", what the quarantine is called, when NOT to act — are the part
worth testing on their own, and sync_worker is already long.
"""

import re
from datetime import datetime

REMOVED_CONTAINER = "Removed from Source"

# "{folder} (YYYY-MM-DD HHMM)", with an optional " (2)" collision suffix.
_QUARANTINED_RE = re.compile(r"^(?P<name>.+) \(\d{4}-\d{2}-\d{2} \d{4}\)(?: \(\d+\))?$")


def quarantine_name(folder: str, when: datetime) -> str:
    """The folder's name inside the container.

    Minute precision, not seconds or a Unix timestamp: this is a name a human
    reads in webmail. "/" is folded to "-" so a nested provider folder does not
    grow a tree inside the container.
    """
    leaf = folder.replace("/", "-")
    return f"{leaf} ({when.strftime('%Y-%m-%d %H%M')})"


def quarantine_path(maildir_path: str, folder: str, when: datetime) -> str:
    return f"{maildir_path.rstrip('/')}/{REMOVED_CONTAINER}/{quarantine_name(folder, when)}"


def original_folder_name(folder_path: str) -> str:
    """The name a quarantined folder had at the Source.

    Used when staging records where a message came from: the quarantine is
    MFB's bookkeeping, not a place the message ever occupied upstream.
    """
    prefix = f"{REMOVED_CONTAINER}/"
    if not folder_path.startswith(prefix):
        return folder_path
    match = _QUARANTINED_RE.match(folder_path[len(prefix) :])
    return match.group("name") if match else folder_path
