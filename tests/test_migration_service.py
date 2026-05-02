import os
import tempfile

import pytest

from mailfallback.models import Account, MailStore, MigrationStatus, User, UserRole
from mailfallback.services.migration_service import (
    execute_account_migration,
    execute_home_migration,
    get_drain_status,
    initiate_account_migration,
    initiate_home_migration,
    initiate_store_drain,
)


def _make_store(db, name, path):
    store = MailStore(name=name, path=path)
    db.add(store)
    db.commit()
    db.refresh(store)
    return store


def _make_user(db, username, store):
    user = User(
        username=username,
        password_hash="x",
        role=UserRole.user,
        enabled=True,
        store_id=store.id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _make_account(db, name, email, imap_host, maildir_path, store):
    account = Account(
        name=name,
        email_address=email,
        imap_host=imap_host,
        maildir_path=maildir_path,
        store_id=store.id,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


# --- Account migration tests ---


def test_initiate_account_migration(db_session):
    src = _make_store(db_session, "src", "/tmp/src")
    dst = _make_store(db_session, "dst", "/tmp/dst")
    account = _make_account(
        db_session,
        "Test Gmail",
        "test@gmail.com",
        "imap.gmail.com",
        "/tmp/src/test-uuid",
        src,
    )

    migration = initiate_account_migration(db_session, account.id, dst.id)

    assert migration.account_id == account.id
    assert migration.user_id is None
    assert migration.source_store_id == src.id
    assert migration.target_store_id == dst.id
    assert migration.status == MigrationStatus.pending


def test_initiate_account_migration_rejects_same_store(db_session):
    store = _make_store(db_session, "only", "/tmp/only")
    account = _make_account(
        db_session,
        "Test Gmail",
        "test@gmail.com",
        "imap.gmail.com",
        "/tmp/only/test-uuid",
        store,
    )

    with pytest.raises(ValueError, match=r"already on.*store"):
        initiate_account_migration(db_session, account.id, store.id)


def test_initiate_account_migration_rejects_missing_account(db_session):
    dst = _make_store(db_session, "dst", "/tmp/dst")

    with pytest.raises(ValueError, match="Account not found"):
        initiate_account_migration(db_session, "nonexistent-id", dst.id)


def test_initiate_account_migration_rejects_missing_store(db_session):
    src = _make_store(db_session, "src", "/tmp/src")
    account = _make_account(
        db_session,
        "Test Gmail",
        "test@gmail.com",
        "imap.gmail.com",
        "/tmp/src/test-uuid",
        src,
    )

    with pytest.raises(ValueError, match="Target store not found"):
        initiate_account_migration(db_session, account.id, "nonexistent-store-id")


def test_execute_account_migration_full_flow(db_session):
    with tempfile.TemporaryDirectory() as src_dir, tempfile.TemporaryDirectory() as dst_dir:
        src_store = _make_store(db_session, "source", src_dir)
        dst_store = _make_store(db_session, "target", dst_dir)
        account = _make_account(
            db_session,
            "Test Gmail",
            "test@gmail.com",
            "imap.gmail.com",
            f"{src_dir}/test-uuid",
            src_store,
        )

        # Create real Maildir files in source
        cur_dir = os.path.join(src_dir, "test-uuid", "cur")
        os.makedirs(cur_dir)
        for i in range(3):
            with open(os.path.join(cur_dir, f"msg{i}"), "w") as f:
                f.write(f"email content {i}")

        # Initiate and execute
        migration = initiate_account_migration(db_session, account.id, dst_store.id)
        execute_account_migration(db_session, migration.id)

        # Reload from DB
        db_session.refresh(migration)
        db_session.refresh(account)

        # Migration completed
        assert migration.status == MigrationStatus.completed
        assert migration.completed_at is not None
        assert migration.total_files == 3
        assert migration.copied_files == 3

        # Account updated
        assert account.store_id == dst_store.id
        expected_path = f"{dst_dir}/{account.id}"
        assert account.maildir_path == expected_path

        # Files exist in destination
        dst_cur = os.path.join(dst_dir, account.id, "cur")
        assert os.path.exists(os.path.join(dst_cur, "msg0"))
        assert os.path.exists(os.path.join(dst_cur, "msg1"))
        assert os.path.exists(os.path.join(dst_cur, "msg2"))

        # Source directory deleted
        assert not os.path.exists(os.path.join(src_dir, "test-uuid"))


def test_execute_account_migration_no_source_dir(db_session):
    """Migration completes even when the source directory doesn't exist yet."""
    with tempfile.TemporaryDirectory() as src_dir, tempfile.TemporaryDirectory() as dst_dir:
        src_store = _make_store(db_session, "source", src_dir)
        dst_store = _make_store(db_session, "target", dst_dir)
        account = _make_account(
            db_session,
            "Test Gmail",
            "test@gmail.com",
            "imap.gmail.com",
            f"{src_dir}/test-uuid",
            src_store,
        )

        migration = initiate_account_migration(db_session, account.id, dst_store.id)
        execute_account_migration(db_session, migration.id)

        db_session.refresh(migration)
        db_session.refresh(account)

        assert migration.status == MigrationStatus.completed
        assert migration.total_files == 0
        assert migration.total_bytes == 0
        assert account.store_id == dst_store.id


def test_execute_account_migration_missing_record(db_session):
    """execute_account_migration handles a nonexistent migration ID gracefully."""
    execute_account_migration(db_session, "does-not-exist")


# --- Home migration tests ---


def test_initiate_home_migration(db_session):
    src = _make_store(db_session, "src", "/tmp/src")
    dst = _make_store(db_session, "dst", "/tmp/dst")
    user = _make_user(db_session, "alice", src)

    migration = initiate_home_migration(db_session, user.id, dst.id)

    assert migration.user_id == user.id
    assert migration.account_id is None
    assert migration.source_store_id == src.id
    assert migration.target_store_id == dst.id
    assert migration.status == MigrationStatus.pending

    db_session.refresh(user)
    assert user.migrating is True


def test_initiate_home_migration_rejects_same_store(db_session):
    store = _make_store(db_session, "only", "/tmp/only")
    user = _make_user(db_session, "bob", store)

    with pytest.raises(ValueError, match=r"already on.*store"):
        initiate_home_migration(db_session, user.id, store.id)


def test_initiate_home_migration_rejects_already_migrating(db_session):
    src = _make_store(db_session, "src", "/tmp/src")
    dst = _make_store(db_session, "dst", "/tmp/dst")
    user = _make_user(db_session, "carol", src)
    user.migrating = True
    db_session.commit()

    with pytest.raises(ValueError, match="already migrating"):
        initiate_home_migration(db_session, user.id, dst.id)


def test_initiate_home_migration_rejects_missing_user(db_session):
    dst = _make_store(db_session, "dst", "/tmp/dst")

    with pytest.raises(ValueError, match="User not found"):
        initiate_home_migration(db_session, "nonexistent-id", dst.id)


def test_initiate_home_migration_rejects_missing_store(db_session):
    src = _make_store(db_session, "src", "/tmp/src")
    user = _make_user(db_session, "dave", src)

    with pytest.raises(ValueError, match="Target store not found"):
        initiate_home_migration(db_session, user.id, "nonexistent-store-id")


def test_execute_home_migration_full_flow(db_session):
    with tempfile.TemporaryDirectory() as src_dir, tempfile.TemporaryDirectory() as dst_dir:
        src_store = _make_store(db_session, "source", src_dir)
        dst_store = _make_store(db_session, "target", dst_dir)
        user = _make_user(db_session, "eve", src_store)

        # Create dovecot-home files in source
        home_dir = os.path.join(src_dir, ".dovecot-home", "eve")
        os.makedirs(home_dir)
        for name in ["dovecot.sieve", "dovecot-uidvalidity", "subscriptions"]:
            with open(os.path.join(home_dir, name), "w") as f:
                f.write(f"content of {name}")

        # Initiate and execute
        migration = initiate_home_migration(db_session, user.id, dst_store.id)
        execute_home_migration(db_session, migration.id)

        # Reload from DB
        db_session.refresh(migration)
        db_session.refresh(user)

        # Migration completed
        assert migration.status == MigrationStatus.completed
        assert migration.completed_at is not None
        assert migration.total_files == 3
        assert migration.copied_files == 3

        # User updated
        assert user.migrating is False
        assert user.store_id == dst_store.id

        # Files exist in destination
        dst_home = os.path.join(dst_dir, ".dovecot-home", "eve")
        assert os.path.exists(os.path.join(dst_home, "dovecot.sieve"))
        assert os.path.exists(os.path.join(dst_home, "dovecot-uidvalidity"))
        assert os.path.exists(os.path.join(dst_home, "subscriptions"))

        # Source directory deleted
        assert not os.path.exists(home_dir)


def test_execute_home_migration_no_source_dir(db_session):
    """Migration completes even when the dovecot-home doesn't exist yet."""
    with tempfile.TemporaryDirectory() as src_dir, tempfile.TemporaryDirectory() as dst_dir:
        src_store = _make_store(db_session, "source", src_dir)
        dst_store = _make_store(db_session, "target", dst_dir)
        user = _make_user(db_session, "frank", src_store)

        migration = initiate_home_migration(db_session, user.id, dst_store.id)
        execute_home_migration(db_session, migration.id)

        db_session.refresh(migration)
        db_session.refresh(user)

        assert migration.status == MigrationStatus.completed
        assert migration.total_files == 0
        assert migration.total_bytes == 0
        assert user.migrating is False
        assert user.store_id == dst_store.id


def test_execute_home_migration_missing_record(db_session):
    """execute_home_migration handles a nonexistent migration ID gracefully."""
    execute_home_migration(db_session, "does-not-exist")


# --- Store drain tests ---


def test_initiate_store_drain(db_session):
    src = _make_store(db_session, "src", "/tmp/drain-src")
    dst = _make_store(db_session, "dst", "/tmp/drain-dst")
    user = _make_user(db_session, "drainuser", src)
    acct = _make_account(
        db_session,
        "Drain Acct",
        "d@example.com",
        "imap.ex.com",
        "/tmp/drain-src/acct-uuid",
        src,
    )

    migrations = initiate_store_drain(db_session, src.id, dst.id)

    assert len(migrations) == 2
    account_migs = [m for m in migrations if m.account_id]
    home_migs = [m for m in migrations if m.user_id]
    assert len(account_migs) == 1
    assert len(home_migs) == 1
    assert account_migs[0].account_id == acct.id
    assert home_migs[0].user_id == user.id

    db_session.refresh(acct)
    db_session.refresh(user)
    assert acct.migrating is True
    assert user.migrating is True


def test_initiate_store_drain_rejects_same_store(db_session):
    store = _make_store(db_session, "same", "/tmp/drain-same")
    with pytest.raises(ValueError, match="same"):
        initiate_store_drain(db_session, store.id, store.id)


def test_initiate_store_drain_empty_store(db_session):
    src = _make_store(db_session, "empty", "/tmp/drain-empty")
    dst = _make_store(db_session, "dst2", "/tmp/drain-dst2")
    migrations = initiate_store_drain(db_session, src.id, dst.id)
    assert migrations == []


def test_get_drain_status_no_migrations(db_session):
    store = _make_store(db_session, "clean", "/tmp/drain-clean")
    status = get_drain_status(db_session, store.id)
    assert status["draining"] is False
    assert status["active"] == 0
    assert status["completed"] == 0


def test_get_drain_status_active(db_session):
    src = _make_store(db_session, "src3", "/tmp/drain-src3")
    dst = _make_store(db_session, "dst3", "/tmp/drain-dst3")
    _make_account(
        db_session,
        "A1",
        "a1@ex.com",
        "imap.ex.com",
        "/tmp/drain-src3/a1-uuid",
        src,
    )
    _make_account(
        db_session,
        "A2",
        "a2@ex.com",
        "imap.ex.com",
        "/tmp/drain-src3/a2-uuid",
        src,
    )
    initiate_store_drain(db_session, src.id, dst.id)

    status = get_drain_status(db_session, src.id)
    assert status["draining"] is True
    assert status["active"] == 2
    assert len(status["items"]) == 2
