"""One-shot: remove duplicate Recovery rows.

Keeps the newest ready Recovery per (account_id, snapshot_id), deletes the rest
(both DB rows AND on-disk restore_path via recovery_service.delete_recovery).

Run inside the container:

    docker compose exec mailfallback \
        uv run python scripts/cleanup_duplicate_recoveries.py
"""
# ruff: noqa: T201

from mailfallback.db import SessionLocal
from mailfallback.models import Recovery, RecoveryStatus
from mailfallback.services import recovery_service


def main() -> int:
    db = SessionLocal()
    try:
        all_ready = (
            db.query(Recovery)
            .filter(Recovery.status == RecoveryStatus.ready)
            .order_by(Recovery.account_id, Recovery.snapshot_id, Recovery.restored_at.desc())
            .all()
        )
        seen = set()
        to_delete = []
        for rec in all_ready:
            key = (rec.account_id, rec.snapshot_id)
            if key in seen:
                to_delete.append(rec)
            else:
                seen.add(key)
        print(f"Found {len(all_ready)} ready Recoveries, {len(to_delete)} duplicates to remove.")
        for rec in to_delete:
            print(
                f"  removing rec={rec.id[:8]} acct={rec.account_id[:8]} "
                f"snap={rec.snapshot_id} restored={rec.restored_at}"
            )
            recovery_service.delete_recovery(db, rec.id)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
