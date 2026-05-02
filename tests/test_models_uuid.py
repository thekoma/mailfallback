# tests/test_models_uuid.py
"""Tests for UUID-based maildir refactor model changes."""

from mailfallback.models import Account, MailStore, StoreMigration, User, UserRole


class TestAccountStoreRelationship:
    """Account.store_id FK and MailStore.accounts backref."""

    def test_account_has_store_id(self, db_session, default_store):
        """Account can be created with a store_id pointing to a MailStore."""
        account = Account(
            name="Test Account",
            email_address="test@example.com",
            imap_host="imap.example.com",
            maildir_path="/data/mailboxes/test",
            store_id=default_store.id,
        )
        db_session.add(account)
        db_session.commit()
        db_session.refresh(account)

        assert account.store_id == default_store.id
        assert account.store is not None
        assert account.store.id == default_store.id
        assert account.store.name == "default"

    def test_account_store_relationship(self, db_session, default_store):
        """MailStore.accounts backref lists accounts assigned to that store."""
        acct1 = Account(
            name="Account 1",
            email_address="one@example.com",
            imap_host="imap.example.com",
            maildir_path="/data/mailboxes/one",
            store_id=default_store.id,
        )
        acct2 = Account(
            name="Account 2",
            email_address="two@example.com",
            imap_host="imap.example.com",
            maildir_path="/data/mailboxes/two",
            store_id=default_store.id,
        )
        db_session.add_all([acct1, acct2])
        db_session.commit()
        db_session.refresh(default_store)

        assert len(default_store.accounts) == 2
        account_names = {a.name for a in default_store.accounts}
        assert account_names == {"Account 1", "Account 2"}


class TestDeriveMaildirPathUUID:
    """derive_maildir_path uses account UUID for path derivation."""

    def test_derive_maildir_path_uuid(self):
        from mailfallback.services.store_service import derive_maildir_path

        path = derive_maildir_path("/data/mailboxes", "550e8400-e29b-41d4-a716-446655440000")
        assert path == "/data/mailboxes/550e8400-e29b-41d4-a716-446655440000"

    def test_derive_maildir_path_strips_trailing_slash(self):
        from mailfallback.services.store_service import derive_maildir_path

        path = derive_maildir_path("/data/mailboxes/", "some-uuid")
        assert path == "/data/mailboxes/some-uuid"


class TestCreateAccountWithStore:
    """account_service.create_account derives maildir_path from store + UUID."""

    def test_create_account_with_store(self, db_session, default_store):
        """account_service.create_account derives maildir_path from store + UUID."""
        from mailfallback.services.account_service import create_account

        account = create_account(
            db_session,
            name="Gmail",
            imap_host="imap.gmail.com",
            imap_port=993,
            auth_type="app_password",
            store=default_store,
            email_address="test@gmail.com",
        )
        assert account.store_id == default_store.id
        assert account.maildir_path == f"/data/mailboxes/{account.id}"
        assert account.id in account.maildir_path


class TestStoreMigrationPerAccount:
    """StoreMigration supports per-account migrations."""

    def test_store_migration_per_account(self, db_session, default_store):
        """StoreMigration can target an account instead of a user."""
        target_store = MailStore(name="target", path="/data/target")
        db_session.add(target_store)
        db_session.commit()

        account = Account(
            name="Migrating Account",
            email_address="migrate@example.com",
            imap_host="imap.example.com",
            maildir_path="/data/mailboxes/migrate",
            store_id=default_store.id,
        )
        db_session.add(account)
        db_session.commit()

        migration = StoreMigration(
            account_id=account.id,
            source_store_id=default_store.id,
            target_store_id=target_store.id,
        )
        db_session.add(migration)
        db_session.commit()
        db_session.refresh(migration)

        assert migration.account_id == account.id
        assert migration.user_id is None
        assert migration.source_store_id == default_store.id
        assert migration.target_store_id == target_store.id

    def test_store_migration_per_user_still_works(self, db_session, default_store):
        """StoreMigration can still target a user (backward compat)."""
        target_store = MailStore(name="target", path="/data/target")
        db_session.add(target_store)
        db_session.commit()

        user = User(
            username="migrator",
            password_hash="fakehash",
            role=UserRole.user,
            store_id=default_store.id,
        )
        db_session.add(user)
        db_session.commit()

        migration = StoreMigration(
            user_id=user.id,
            source_store_id=default_store.id,
            target_store_id=target_store.id,
        )
        db_session.add(migration)
        db_session.commit()
        db_session.refresh(migration)

        assert migration.user_id == user.id
        assert migration.account_id is None
