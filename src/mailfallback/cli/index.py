"""`mfb index` subcommand handlers."""

from mailfallback.db import SessionLocal


def handle_index(args) -> int:
    if args.index_cmd == "status":
        return _status()
    if args.index_cmd == "rebuild-account":
        return _rebuild_account(args.account_id)
    if args.index_cmd == "backfill-snapshots":
        return _backfill_snapshots(args.account_id)
    return 1


def _status() -> int:
    from mailfallback.models import MailIndexRebuildStatus

    db = SessionLocal()
    try:
        rows = db.query(MailIndexRebuildStatus).all()
        if not rows:
            print("No rebuild_status rows yet.")
            return 0
        for r in rows:
            print(
                f"  account={r.account_id[:8]} state={r.state} "
                f"last_indexed={r.last_indexed_at} "
                f"backfill={r.backfill_progress}/{r.backfill_total}"
            )
        return 0
    finally:
        db.close()


def _rebuild_account(account_id: str) -> int:
    from mailfallback.services import index_service

    db = SessionLocal()
    try:
        n = index_service.upsert_message_set(db, account_id)
        print(f"Rebuilt {n} rows for {account_id}.")
        return 0
    finally:
        db.close()


def _backfill_snapshots(account_id: str) -> int:
    from mailfallback.services import index_service

    db = SessionLocal()
    try:
        skipped = 0
        for progress in index_service.backfill_snapshots(db, account_id):
            if progress.get("skipped"):
                skipped += 1
                continue
            print(
                f"  snap {progress['snapshot_id']}: "
                f"{progress['processed']}/{progress['total']}, "
                f"+{progress['bits_inserted']} bits"
            )
        if skipped:
            print(f"Skipped {skipped} already-processed snapshot(s).")
        print("Done.")
        return 0
    finally:
        db.close()
