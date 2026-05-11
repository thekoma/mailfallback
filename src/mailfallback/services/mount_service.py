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
from datetime import UTC, datetime

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
