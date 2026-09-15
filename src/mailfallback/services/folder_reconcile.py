"""Folders that vanished from the Source: naming, detection, quarantine.

Kept out of sync_worker deliberately. The rules here — what counts as
"removed", what the quarantine is called, when NOT to act — are the part
worth testing on their own, and sync_worker is already long.
"""

import fnmatch
import os
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


class ProviderAnomaly(Exception):
    """Too many folders vanished at once to be a user tidying labels."""


# Both halves are needed. The ratio alone trips on an account with three
# folders that loses one, which is ordinary and is the very failure this
# module removes. The floor alone says nothing about a mailbox with sixty.
_ANOMALY_RATIO = 0.25
_ANOMALY_FLOOR = 3


def _excluded(name: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if name == pattern:
            return True
        if ("*" in pattern or "?" in pattern) and fnmatch.fnmatchcase(
            name, pattern.replace("[", "[[]")
        ):
            return True
    return False


def folders_to_quarantine(
    local_folders: set[str], remote_folders: set[str], excluded: list[str]
) -> list[str]:
    """Local folders that no longer exist at the Source.

    An empty remote set returns nothing: a LIST that failed or came back empty
    must never be read as "everything was deleted".
    """
    if not remote_folders:
        return []
    missing = sorted(
        f for f in local_folders if f not in remote_folders and not _excluded(f, excluded)
    )
    if (
        len(missing) > _ANOMALY_FLOOR
        and local_folders
        and len(missing) / len(local_folders) > _ANOMALY_RATIO
    ):
        raise ProviderAnomaly(
            f"{len(missing)} of {len(local_folders)} folders missing from the Source"
        )
    return missing


def local_synced_folders(maildir_path: str) -> set[str]:
    """Folders under the account maildir that mbsync has actually synced.

    `.mbsyncstate` is the discriminator: it separates a folder MFB really
    pulled from any other directory that happens to sit there — the container
    itself, a Dovecot artefact, something an admin dropped in.
    """
    found: set[str] = set()
    root = maildir_path.rstrip("/")
    for dirpath, dirnames, filenames in os.walk(root):
        if os.path.basename(dirpath) == REMOVED_CONTAINER:
            dirnames[:] = []
            continue
        if ".mbsyncstate" not in filenames:
            continue
        rel = os.path.relpath(dirpath, root)
        if rel != ".":
            found.add(rel)
    return found
