"""Folders that vanished from the Source: naming, detection, quarantine.

Kept out of sync_worker deliberately. The rules here — what counts as
"removed", what the quarantine is called, when NOT to act — are the part
worth testing on their own, and sync_worker is already long.
"""

import fnmatch
import logging
import os
import re
import shutil
import socket
import time
from datetime import datetime
from email.utils import formatdate

logger = logging.getLogger(__name__)

REMOVED_CONTAINER = "Removed from Source"

_README_TAG = "container-readme"

# "{folder} (YYYY-MM-DD HHMM)", with an optional " (2)" collision suffix.
_QUARANTINED_RE = re.compile(r"^(?P<name>.+) \(\d{4}-\d{2}-\d{2} \d{4}\)(?: \(\d+\))?$")


def quarantine_name(folder: str, when: datetime) -> str:
    """The folder's name inside the container.

    Minute precision, not seconds or a Unix timestamp: this is a name a human
    reads in webmail. "/" and ":" are folded to "-" — "/" so a nested provider
    folder does not grow a tree inside the container, ":" because it separates
    flags from the filename in Maildir.
    """
    leaf = folder.replace("/", "-").replace(":", "-")
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


def write_container_readme(maildir_path: str) -> None:
    """Drop a one-time explanatory message into the container's own inbox.

    Whoever opens "Removed from Source" in webmail otherwise finds a folder
    of dated folders with no clue what put them there. Idempotent: a marker
    in the filename, not the timestamp, is what's checked, since a folder
    landing in the container twice in the same minute must not double this up.
    The guard checks both `new/` and `cur/`: Dovecot moves the message out of
    `new/` the first time a client opens the mailbox, and that must not read
    as "no README exists yet".

    No `mailfallback` imports here on purpose (see module docstring), so this
    replicates the Maildir-write shape of `user_service.create_welcome_email`
    with the standard library instead of reusing it.
    """
    container = os.path.join(maildir_path.rstrip("/"), REMOVED_CONTAINER)
    new_dir = os.path.join(container, "new")
    cur_dir = os.path.join(container, "cur")
    for sub in ("cur", "new", "tmp"):
        os.makedirs(os.path.join(container, sub), exist_ok=True)

    existing = os.listdir(new_dir) + os.listdir(cur_dir)
    if any(f".{_README_TAG}." in name for name in existing):
        return

    timestamp = int(time.time())
    hostname = socket.gethostname()
    filename = f"{timestamp}.{_README_TAG}.{hostname}:2,"

    msg = f"""\
From: MailFallBack <noreply@mailfallback.local>
To: undisclosed-recipients:;
Subject: About this folder
Date: {formatdate(localtime=True)}
Message-ID: <{_README_TAG}-{timestamp}@mailfallback.local>

Folders in here were removed from the Source and are no longer synced.

MailFallBack keeps the local backup copy it already had. The messages are
intact and searchable, and the date in each folder's name is when it
stopped being synced.

Nothing here is sent back to the Source. Delete a folder by hand when you
no longer want the copy; if the folder comes back at the Source, it is
synced again as a new folder, and this copy is left untouched.
"""

    fd = os.open(os.path.join(new_dir, filename), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(msg)
    logger.info("Wrote README for the removed-folder container: %s", container)


def quarantine_folder(maildir_path: str, folder: str, when: datetime) -> str | None:
    """Move a removed folder into the container. Returns the destination.

    Moved, never copied or deleted: on a plain IMAP server a deleted folder
    takes the mail with it, and the local copy is then the only one left.

    The sync state goes with the move. Keeping it would make a folder of the
    same name returning upstream fail with "Unable to recover from UIDVALIDITY
    change" — trading one permanent error for another.
    """
    source = os.path.join(maildir_path.rstrip("/"), folder)
    if not os.path.isdir(source):
        return None

    dest = quarantine_path(maildir_path, folder, when)
    if os.path.exists(dest):
        n = 2
        while os.path.exists(f"{dest} ({n})"):
            n += 1
        dest = f"{dest} ({n})"

    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.move(source, dest)
    for leftover in os.listdir(dest):
        if leftover.startswith(".mbsyncstate"):
            os.remove(os.path.join(dest, leftover))
    logger.info("Quarantined folder removed from the Source: %s -> %s", source, dest)

    # The README is an explanation, not the point of the operation: a folder
    # that failed to move would stay stuck resyncing forever, but a folder
    # that moved fine must never be undone by a disk-full/permissions error
    # writing this one message.
    try:
        write_container_readme(maildir_path)
    except OSError:
        logger.warning("Could not write the container README for %s", maildir_path, exc_info=True)

    return dest
