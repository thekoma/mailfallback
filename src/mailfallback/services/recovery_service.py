# src/mailfallback/services/recovery_service.py
"""Recovery service — restore a restic snapshot to a Recovery row.

Replaces the legacy Account-as-recovery flow. A Recovery is NOT a mailbox;
it's a read-only on-disk artefact that Dovecot exposes as an additional
namespace under the source Account's owner(s).

Lifecycle:
    create_recovery  → Recovery(status=restoring) + restic restore in temp dir
                     → on success: status=ready, restore_path = path-to-Maildir
                     → on failure: status=failed, error=...
    delete_recovery  → status=deleting → rm -rf restore_path → drop row
"""

import contextlib
import logging
import os
import shutil
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from mailfallback.models import Account, BackupPolicy, Recovery, RecoveryKind, RecoveryStatus
from mailfallback.services import restic_service

logger = logging.getLogger(__name__)


def namespace_prefix(rec, account_label: str) -> str:
    """Build the Dovecot namespace prefix for a Recovery.

    MUST be the single source of truth: dovecot.py (publisher) and
    restore.py (consumer) both call this. Using rec.id[:8] guarantees
    uniqueness even if multiple Recoveries exist for the same snapshot
    (race condition documented in mount_service).
    """
    short = rec.id[:8]
    ts = rec.restored_at.strftime("%Y-%m-%d") if rec.restored_at else "snapshot"
    return f"Recovery - {account_label} ({ts}) [{short}]/"


def _build_restore_root(account: Account) -> str:
    """Where the on-disk restore tree lives for this recovery.

    A timestamped per-recovery directory under <store>/.offsite-restore so
    multiple recoveries of the same account don't collide.
    """
    ts = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    return f"{account.store.path}/.offsite-restore/{account.id}-{ts}"


def _resolve_maildir_inside_restore(restore_root: str, account: Account) -> str:
    """Compute the actual Maildir path inside the restored tree.

    restic restore preserves absolute paths from the original. The original
    Maildir was at e.g. /data/mailboxes/<account-uuid>, so restic restores
    it to <restore_root>/data/mailboxes/<account-uuid>.

    If the layout doesn't match, fall back to the restore root and let the
    caller deal with it.
    """
    rel = account.maildir_path.lstrip("/")
    candidate = os.path.join(restore_root, rel)
    if os.path.isdir(candidate):
        return candidate
    # Walk one level down and pick a directory that looks like a Maildir
    # (has cur/, new/, tmp/ at some depth).
    for root, dirs, _ in os.walk(restore_root):
        if {"cur", "new", "tmp"}.issubset(set(dirs)):
            # Return the parent of the cur/new/tmp triplet.
            return root
    return restore_root


def _compute_size(path: str) -> int | None:
    try:
        total = 0
        for dirpath, _, filenames in os.walk(path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                with contextlib.suppress(OSError):
                    total += os.path.getsize(fp)
        return total
    except OSError:
        return None


def create_recovery(
    db: Session,
    account_id: str,
    snapshot_id: str,
    *,
    kind: RecoveryKind = RecoveryKind.persistent,
    ttl_minutes: int | None = None,
) -> Recovery:
    """Restore a snapshot to disk and create a Recovery row.

    Returns the Recovery (status=ready on success, status=failed on error).
    Never raises — the caller can inspect status/error on the returned row.
    """
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise ValueError(f"Account {account_id} not found")

    backup = db.query(BackupPolicy).filter(BackupPolicy.account_id == account_id).first()
    if not backup:
        raise ValueError("Account has no backup policy; nothing to restore from")

    restore_root = _build_restore_root(account)
    os.makedirs(restore_root, exist_ok=True)

    recovery = Recovery(
        account_id=account.id,
        repository_id=backup.destination_id,
        snapshot_id=snapshot_id,
        restore_path=restore_root,  # placeholder; updated after extract
        status=RecoveryStatus.restoring,
        kind=kind,
        ttl_minutes=ttl_minutes,
    )
    db.add(recovery)
    db.commit()
    db.refresh(recovery)

    try:
        restic_service.restore_snapshot(backup.destination, account.id, snapshot_id, restore_root)
        maildir_root = _resolve_maildir_inside_restore(restore_root, account)
        recovery.restore_path = maildir_root
        recovery.status = RecoveryStatus.ready
        recovery.size_bytes = _compute_size(maildir_root)
        # Best-effort: extract the snapshot's time from restic's snapshots list.
        try:
            snapshots = restic_service.list_snapshots(backup.destination, account.id)
            for s in snapshots:
                if s.get("short_id") == snapshot_id or s.get("id", "").startswith(snapshot_id):
                    ts = s.get("time", "").replace("Z", "+00:00")
                    if ts:
                        recovery.snapshot_time = datetime.fromisoformat(ts)
                    break
        except Exception as e:
            logger.warning("Couldn't fetch snapshot metadata: %s", e)
    except Exception as e:
        recovery.status = RecoveryStatus.failed
        recovery.error = str(e)
        logger.error("Recovery %s failed: %s", recovery.id, e)
    finally:
        db.commit()
        db.refresh(recovery)

    return recovery


def delete_recovery(db: Session, recovery_id: str) -> None:
    """Mark deleting → rm -rf the on-disk tree → drop the row.

    Idempotent: if the row is already gone, succeed silently. The on-disk
    parent directory (.offsite-restore/<account-id>-<timestamp>/) is removed
    too — restic restore's outer wrapper directory.
    """
    recovery = db.query(Recovery).filter(Recovery.id == recovery_id).first()
    if not recovery:
        return
    recovery.status = RecoveryStatus.deleting
    db.commit()

    # Compute the outer wrapper dir to delete (restore_path may have been
    # walked down into the actual Maildir; we want to nuke the whole tree).
    parent = recovery.restore_path
    while parent and parent != "/" and ".offsite-restore" in parent:
        head, tail = os.path.split(parent)
        if tail and tail.startswith(f"{recovery.account_id}-") and ".offsite-restore" in head:
            break
        parent = head

    target = parent if parent and ".offsite-restore" in parent else recovery.restore_path
    try:
        if target and os.path.isdir(target):
            shutil.rmtree(target)
    except OSError as e:
        logger.warning("Failed to remove %s: %s", target, e)

    db.delete(recovery)
    db.commit()


def list_recoveries_for_account(db: Session, account_id: str) -> list[Recovery]:
    return (
        db.query(Recovery)
        .filter(Recovery.account_id == account_id)
        .order_by(Recovery.restored_at.desc())
        .all()
    )


def list_recoveries_for_user_accounts(db: Session, account_ids: list[str]) -> list[Recovery]:
    if not account_ids:
        return []
    return (
        db.query(Recovery)
        .filter(Recovery.account_id.in_(account_ids))
        .filter(Recovery.status == RecoveryStatus.ready)
        .order_by(Recovery.restored_at.desc())
        .all()
    )
