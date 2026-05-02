"""Migration service — orchestrates store-to-store migration.

Supports two migration types:
- **Account migration**: moves one account's maildir to a different store.
- **Home migration**: moves a user's dovecot-home directory to a different store.
- **Store drain**: batch-migrates all accounts and user homes off a store.
"""

import logging
import os
import shutil
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from mailfallback.models import Account, MailStore, MigrationStatus, StoreMigration, User
from mailfallback.services.migration_worker import copy_tree, prescan, verify_copy
from mailfallback.services.store_service import derive_maildir_path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Account migration
# ---------------------------------------------------------------------------


def initiate_account_migration(
    db: Session, account_id: str, target_store_id: str
) -> StoreMigration:
    """Validate and create a migration record for moving an account's maildir.

    Returns the new ``StoreMigration`` with ``account_id`` set and
    ``user_id=None``.  Raises ``ValueError`` on invalid input.
    """
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise ValueError("Account not found")

    target_store = db.query(MailStore).filter(MailStore.id == target_store_id).first()
    if not target_store:
        raise ValueError("Target store not found")

    if account.store_id == target_store_id:
        raise ValueError("Account is already on the target store")

    if account.migrating:
        raise ValueError("Account is already migrating")

    account.migrating = True
    migration = StoreMigration(
        account_id=account.id,
        user_id=None,
        source_store_id=account.store_id,
        target_store_id=target_store_id,
    )
    db.add(migration)
    db.commit()
    db.refresh(migration)
    return migration


def execute_account_migration(db: Session, migration_id: str) -> None:
    """Run the full migration flow for an account maildir.

    Copies the account's maildir from the source store to the target store,
    verifies the copy, updates ``account.maildir_path`` and
    ``account.store_id``, and removes the old directory.

    On any exception the migration is marked as failed.
    """
    migration = db.query(StoreMigration).filter(StoreMigration.id == migration_id).first()
    if not migration:
        logger.error("Migration %s not found", migration_id)
        return

    account = db.query(Account).filter(Account.id == migration.account_id).first()
    target_store = db.query(MailStore).filter(MailStore.id == migration.target_store_id).first()

    if not account or not target_store:
        migration.status = MigrationStatus.failed
        migration.error = "Missing account or store record"
        db.commit()
        return

    try:
        # Start copying
        now = datetime.now(UTC)
        migration.status = MigrationStatus.copying
        migration.started_at = now
        if migration.last_resumed_at is None:
            migration.last_resumed_at = now
        db.commit()

        source_path = account.maildir_path
        target_path = derive_maildir_path(target_store.path, account.id)

        if os.path.exists(source_path):
            # Prescan
            total_files, total_bytes = prescan(source_path)
            migration.total_files = total_files
            migration.total_bytes = total_bytes
            db.commit()

            # Copy with progress tracking
            _file_counter = [0]

            def on_progress(copied_files: int, copied_bytes: int) -> None:
                migration.copied_files = copied_files
                migration.copied_bytes = copied_bytes
                _file_counter[0] += 1
                if _file_counter[0] % 100 == 0:
                    db.commit()

            copy_tree(source_path, target_path, on_progress=on_progress)

            # Verify
            migration.status = MigrationStatus.verifying
            db.commit()

            ok, detail = verify_copy(source_path, target_path)
            if not ok:
                migration.status = MigrationStatus.failed
                migration.error = detail
                db.commit()
                return
        else:
            migration.total_files = 0
            migration.total_bytes = 0

        # Clean phase — update paths
        migration.status = MigrationStatus.cleaning
        db.commit()

        account.maildir_path = target_path
        account.store_id = target_store.id
        db.commit()

        # Remove old directory
        if os.path.exists(source_path):
            shutil.rmtree(source_path, ignore_errors=True)

        # Done
        migration.status = MigrationStatus.completed
        migration.completed_at = datetime.now(UTC)
        account.migrating = False
        db.commit()

    except Exception as exc:
        logger.exception("Account migration %s failed", migration_id)
        migration.status = MigrationStatus.failed
        migration.error = str(exc)
        db.commit()


# ---------------------------------------------------------------------------
# Home migration (dovecot-home)
# ---------------------------------------------------------------------------


def initiate_home_migration(db: Session, user_id: str, target_store_id: str) -> StoreMigration:
    """Validate and create a migration record for a user's dovecot-home.

    Sets ``user.migrating = True`` and returns the new ``StoreMigration``
    with ``user_id`` set and ``account_id=None``.
    Raises ``ValueError`` on invalid input or conflicting state.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise ValueError("User not found")

    if user.migrating:
        raise ValueError("User is already migrating")

    target_store = db.query(MailStore).filter(MailStore.id == target_store_id).first()
    if not target_store:
        raise ValueError("Target store not found")

    if user.store_id == target_store_id:
        raise ValueError("User is already on the target store")

    user.migrating = True

    migration = StoreMigration(
        user_id=user.id,
        account_id=None,
        source_store_id=user.store_id,
        target_store_id=target_store_id,
    )
    db.add(migration)
    db.commit()
    db.refresh(migration)
    return migration


def execute_home_migration(db: Session, migration_id: str) -> None:
    """Run the full migration flow for a user's dovecot-home directory.

    Copies from ``source_store.path/.dovecot-home/username`` to
    ``target_store.path/.dovecot-home/username``, updates
    ``user.store_id``, sets ``user.migrating = False``, and removes the
    old directory.

    On any exception the migration is marked as failed and
    ``user.migrating`` stays ``True`` so an admin can investigate.
    """
    migration = db.query(StoreMigration).filter(StoreMigration.id == migration_id).first()
    if not migration:
        logger.error("Migration %s not found", migration_id)
        return

    user = db.query(User).filter(User.id == migration.user_id).first()
    source_store = db.query(MailStore).filter(MailStore.id == migration.source_store_id).first()
    target_store = db.query(MailStore).filter(MailStore.id == migration.target_store_id).first()

    if not user or not source_store or not target_store:
        migration.status = MigrationStatus.failed
        migration.error = "Missing user or store record"
        db.commit()
        return

    try:
        # Start copying
        now = datetime.now(UTC)
        migration.status = MigrationStatus.copying
        migration.started_at = now
        if migration.last_resumed_at is None:
            migration.last_resumed_at = now
        db.commit()

        source_home = f"{source_store.path}/.dovecot-home/{user.username}"
        target_home = f"{target_store.path}/.dovecot-home/{user.username}"

        if os.path.exists(source_home):
            # Prescan
            total_files, total_bytes = prescan(source_home)
            migration.total_files = total_files
            migration.total_bytes = total_bytes
            db.commit()

            # Copy with progress tracking
            _file_counter = [0]

            def on_progress(copied_files: int, copied_bytes: int) -> None:
                migration.copied_files = copied_files
                migration.copied_bytes = copied_bytes
                _file_counter[0] += 1
                if _file_counter[0] % 100 == 0:
                    db.commit()

            copy_tree(source_home, target_home, on_progress=on_progress)

            # Verify
            migration.status = MigrationStatus.verifying
            db.commit()

            ok, detail = verify_copy(source_home, target_home)
            if not ok:
                migration.status = MigrationStatus.failed
                migration.error = detail
                db.commit()
                return
        else:
            migration.total_files = 0
            migration.total_bytes = 0

        # Clean phase — update user store
        migration.status = MigrationStatus.cleaning
        db.commit()

        user.store_id = target_store.id
        db.commit()

        # Remove old directory
        if os.path.exists(source_home):
            shutil.rmtree(source_home, ignore_errors=True)

        # Done
        migration.status = MigrationStatus.completed
        migration.completed_at = datetime.now(UTC)
        user.migrating = False
        db.commit()

    except Exception as exc:
        logger.exception("Home migration %s failed", migration_id)
        migration.status = MigrationStatus.failed
        migration.error = str(exc)
        db.commit()


# ---------------------------------------------------------------------------
# Store drain (batch migration)
# ---------------------------------------------------------------------------


def initiate_store_drain(
    db: Session, source_store_id: str, target_store_id: str
) -> list[StoreMigration]:
    """Create migration records for every account and user home on a store."""
    source = db.query(MailStore).filter(MailStore.id == source_store_id).first()
    target = db.query(MailStore).filter(MailStore.id == target_store_id).first()
    if not source or not target:
        raise ValueError("Store not found")
    if source_store_id == target_store_id:
        raise ValueError("Source and target store are the same")

    migrations: list[StoreMigration] = []

    accounts = (
        db.query(Account)
        .filter(
            Account.store_id == source_store_id,
            Account.migrating.is_(False),
        )
        .all()
    )
    for account in accounts:
        m = initiate_account_migration(db, account.id, target_store_id)
        migrations.append(m)

    users = (
        db.query(User)
        .filter(
            User.store_id == source_store_id,
            User.migrating.is_(False),
        )
        .all()
    )
    for user in users:
        m = initiate_home_migration(db, user.id, target_store_id)
        migrations.append(m)

    return migrations


def execute_store_drain(db: Session, migration_ids: list[str]) -> None:
    """Execute all migrations in sequence."""
    for mid in migration_ids:
        migration = db.query(StoreMigration).filter(StoreMigration.id == mid).first()
        if not migration:
            continue
        if migration.account_id:
            execute_account_migration(db, mid)
        elif migration.user_id:
            execute_home_migration(db, mid)


def get_drain_status(db: Session, store_id: str) -> dict:
    """Get aggregated drain progress for a store.

    Returns active migrations (non-completed, non-failed) where source is this store.
    ``just_finished`` is True only when all migrations completed within the last 60s
    (used to show the success banner briefly after a drain, not on every page load).
    """
    active = (
        db.query(StoreMigration)
        .filter(
            StoreMigration.source_store_id == store_id,
            StoreMigration.status.notin_([MigrationStatus.completed, MigrationStatus.failed]),
        )
        .all()
    )
    completed = (
        db.query(StoreMigration)
        .filter(
            StoreMigration.source_store_id == store_id,
            StoreMigration.status == MigrationStatus.completed,
        )
        .count()
    )
    failed = (
        db.query(StoreMigration)
        .filter(
            StoreMigration.source_store_id == store_id,
            StoreMigration.status == MigrationStatus.failed,
        )
        .count()
    )

    total_bytes = sum(m.total_bytes for m in active)
    copied_bytes = sum(m.copied_bytes for m in active)

    items = []
    for m in active:
        label = ""
        if m.account_id:
            acct = db.query(Account).filter(Account.id == m.account_id).first()
            label = f"{acct.name} ({acct.email_address})" if acct else m.account_id
        elif m.user_id:
            usr = db.query(User).filter(User.id == m.user_id).first()
            label = f"Home: {usr.username}" if usr else m.user_id
        items.append({"label": label, "status": m.status.value})

    just_finished = False
    if not active and (completed or failed):
        cutoff = datetime.now(UTC) - timedelta(seconds=60)
        recent = (
            db.query(StoreMigration)
            .filter(
                StoreMigration.source_store_id == store_id,
                StoreMigration.completed_at >= cutoff,
            )
            .count()
        )
        just_finished = recent > 0

    return {
        "active": len(active),
        "completed": completed,
        "failed": failed,
        "total_bytes": total_bytes,
        "copied_bytes": copied_bytes,
        "items": items,
        "draining": len(active) > 0,
        "just_finished": just_finished,
    }
