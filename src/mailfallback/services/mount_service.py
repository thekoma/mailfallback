"""Mount service — ephemeral Recovery lifecycle.

Wraps recovery_service.create_recovery for the workspace flow:
- ensure_mounted is idempotent: returns the existing Recovery if there's
  already one for (account_id, snapshot_id) with status=ready, otherwise
  creates an ephemeral one.
- touch_mount bumps last_accessed_at to defer cleanup.
- cleanup_idle_mounts removes ephemerals whose last_accessed_at is older
  than ttl_minutes.
- force_unmount removes a Recovery row immediately.

The persistent path is unchanged — call recovery_service.create_recovery
directly (or ensure_mounted with kind=persistent).
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from mailfallback.config import settings
from mailfallback.models import Recovery, RecoveryKind, RecoveryStatus
from mailfallback.services import recovery_service

logger = logging.getLogger(__name__)


def ensure_mounted(
    db: Session,
    account_id: str,
    snapshot_id: str,
    *,
    kind: RecoveryKind = RecoveryKind.ephemeral,
    ttl_minutes: int | None = None,
) -> Recovery:
    """Idempotent mount. Returns existing Recovery (bumping last_accessed_at)
    or creates a new one via recovery_service.create_recovery.

    Race note: the existence check is non-atomic. Two concurrent callers for
    the same (account_id, snapshot_id) can both fall through to create_recovery
    and trigger duplicate restic restores. In practice MFB is single-tenant
    self-hosted and this is rare; if it bites, add a partial unique index on
    (account_id, snapshot_id) WHERE status='ready' and catch IntegrityError.
    """
    existing = (
        db.query(Recovery)
        .filter(
            Recovery.account_id == account_id,
            Recovery.snapshot_id == snapshot_id,
            Recovery.status == RecoveryStatus.ready,
        )
        .first()
    )
    if existing:
        existing.last_accessed_at = datetime.now(UTC)
        db.commit()
        db.refresh(existing)
        return existing

    if ttl_minutes is None and kind == RecoveryKind.ephemeral:
        ttl_minutes = settings.recovery_ephemeral_ttl_minutes

    return recovery_service.create_recovery(
        db, account_id, snapshot_id, kind=kind, ttl_minutes=ttl_minutes
    )


def touch_mount(db: Session, recovery_id: str) -> None:
    """Bump last_accessed_at — defers cleanup."""
    rec = db.query(Recovery).filter(Recovery.id == recovery_id).first()
    if rec is None:
        return
    rec.last_accessed_at = datetime.now(UTC)
    db.commit()


def force_unmount(db: Session, recovery_id: str) -> None:
    """Remove the Recovery (DB row + on-disk tree). Delegates to recovery_service.

    Idempotent: succeeds silently if the Recovery is already gone.
    """
    recovery_service.delete_recovery(db, recovery_id)


def cleanup_idle_mounts(db: Session) -> int:
    """Remove ephemeral Recoveries whose last_accessed_at is older than ttl.

    Returns the number of recoveries removed.
    """
    now = datetime.now(UTC)
    candidates = (
        db.query(Recovery)
        .filter(
            Recovery.kind == RecoveryKind.ephemeral,
            Recovery.ttl_minutes.is_not(None),
        )
        .all()
    )
    removed = 0
    for rec in candidates:
        if rec.last_accessed_at is None:
            continue
        cutoff = rec.last_accessed_at + timedelta(minutes=rec.ttl_minutes)
        # SQLite drops tzinfo on DateTime(timezone=True) round-trips while
        # PostgreSQL preserves it. Normalise both sides to naive UTC for the
        # comparison so tests on SQLite and prod on PostgreSQL behave the same.
        cutoff_cmp = cutoff.replace(tzinfo=None) if cutoff.tzinfo else cutoff
        now_cmp = now.replace(tzinfo=None)
        if cutoff_cmp < now_cmp:
            recovery_service.delete_recovery(db, rec.id)
            removed += 1
    if removed:
        logger.info("cleanup_idle_mounts: removed %d ephemeral recoveries", removed)
    return removed
