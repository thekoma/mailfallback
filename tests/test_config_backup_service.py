"""Config backup: export, scrypt+Fernet envelope, import round-trip."""

import pytest

from mailfallback.config import settings
from mailfallback.models import (
    Account,
    BackendType,
    BackupPolicy,
    MailStore,
    Repository,
    RepositoryAttachment,
    User,
    UserRole,
)
from mailfallback.security import decrypt_credentials, encrypt_credentials
from mailfallback.services import config_backup_service as cbs


def _enc(value: str) -> str:
    return encrypt_credentials(value, settings.secret_key)


@pytest.fixture
def populated(db_session):
    store = MailStore(name="default", path="/data/mailboxes")
    db_session.add(store)
    db_session.flush()
    user = User(
        username="alice",
        password_hash="bcrypt$x",  # pragma: allowlist secret
        role=UserRole.admin,
        store_id=store.id,
    )
    db_session.add(user)
    db_session.flush()
    acc = Account(
        name="work",
        email_address="a@b.c",
        imap_host="imap.b.c",
        maildir_path="/data/mailboxes/fixed-uuid",
        credentials=_enc("imap-secret"),
        store_id=store.id,
    )
    db_session.add(acc)
    db_session.flush()
    acc.owners.append(user)
    repo = Repository(
        name="offsite",
        backend_type=BackendType.s3,
        s3_endpoint=_enc("https://s3.example.com"),
        s3_bucket=_enc("bucket"),
        s3_access_key=_enc("ak"),
        s3_secret_key=_enc("sk"),  # pragma: allowlist secret
        restic_password=_enc("rp"),  # pragma: allowlist secret
    )
    db_session.add(repo)
    db_session.flush()
    db_session.add(BackupPolicy(account_id=acc.id, destination_id=repo.id, schedule="0 2 * * *"))
    db_session.add(
        RepositoryAttachment(repository_id=repo.id, account_id=acc.id, prefix="old-prefix")
    )
    db_session.commit()
    return {"store": store, "user": user, "account": acc, "repo": repo}


class TestExport:
    def test_export_contains_all_tables_and_plaintext_secrets(self, db_session, populated):
        data = cbs.build_export(db_session)

        assert data["schema_version"] == 1
        tables = data["tables"]
        assert {r["username"] for r in tables["users"]} == {"alice"}
        acc_row = tables["accounts"][0]
        assert acc_row["id"] == populated["account"].id  # IDs preserved
        assert acc_row["credentials"] == "imap-secret"  # decrypted for re-keying
        repo_row = tables["backup_destinations"][0]
        assert repo_row["restic_password"] == "rp"  # pragma: allowlist secret
        assert tables["account_owners"] == [
            {"account_id": populated["account"].id, "user_id": populated["user"].id}
        ]
        assert tables["repository_attachments"][0]["prefix"] == "old-prefix"

    def test_export_table_names_exist_in_metadata(self):
        from mailfallback.models import Base

        for name in cbs._EXPORT_TABLES:
            assert name in Base.metadata.tables, f"unknown table in export list: {name}"

    def test_export_is_json_serializable(self, db_session, populated):
        import json

        json.dumps(cbs.build_export(db_session))  # enums/datetimes must be converted


class TestEnvelope:
    def test_round_trip(self):
        blob = cbs.encrypt_export({"schema_version": 1, "tables": {}}, "correct horse battery")
        data = cbs.decrypt_export(blob, "correct horse battery")
        assert data["schema_version"] == 1

    def test_wrong_passphrase_raises_clean_error(self):
        blob = cbs.encrypt_export({"schema_version": 1, "tables": {}}, "right")
        with pytest.raises(cbs.ConfigDecryptError):
            cbs.decrypt_export(blob, "wrong")

    def test_garbage_raises_clean_error(self):
        with pytest.raises(cbs.ConfigDecryptError):
            cbs.decrypt_export(b"not an envelope", "whatever")

    def test_unknown_kdf_raises_clean_error(self):
        import json

        blob = json.loads(cbs.encrypt_export({"schema_version": 1, "tables": {}}, "pw"))
        blob["kdf"] = "pbkdf2"
        with pytest.raises(cbs.ConfigDecryptError, match="Unsupported KDF"):
            cbs.decrypt_export(json.dumps(blob).encode(), "pw")

    def test_empty_passphrase_rejected(self):
        with pytest.raises(ValueError):
            cbs.encrypt_export({"schema_version": 1, "tables": {}}, "")


class TestImport:
    def test_import_into_empty_db_preserves_ids_and_rekeys(self, db_session, populated):
        data = cbs.build_export(db_session)
        for model in (RepositoryAttachment, BackupPolicy, Account, Repository, User, MailStore):
            for row in db_session.query(model).all():
                db_session.delete(row)
        db_session.commit()

        report = cbs.import_export(db_session, data)

        assert report["errors"] == []
        acc = db_session.query(Account).one()
        assert acc.id == populated["account"].id
        assert decrypt_credentials(acc.credentials, settings.secret_key) == "imap-secret"
        repo = db_session.query(Repository).one()
        assert decrypt_credentials(repo.restic_password, settings.secret_key) == "rp"
        assert db_session.query(RepositoryAttachment).count() == 1
        user = db_session.query(User).one()
        assert user.password_hash == "bcrypt$x"  # pragma: allowlist secret
        # association row restored
        assert len(acc.owners) == 1

    def test_import_skips_collisions(self, db_session, populated):
        data = cbs.build_export(db_session)

        report = cbs.import_export(db_session, data)  # everything already exists

        assert report["imported"]["accounts"] == 0
        assert report["skipped"]["accounts"] == 1
        assert report["skipped"]["users"] == 1
        assert db_session.query(Account).count() == 1

    def test_import_partial_failure_keeps_other_tables(self, db_session, populated):
        """A broken record (FK to a nonexistent store) must not poison the
        rest of the import: stores/users/repos still land, error is reported,
        and a valid row AFTER the broken one in the same table still inserts."""
        data = cbs.build_export(db_session)
        data["tables"]["accounts"][0]["store_id"] = "no-such-store"
        good = dict(data["tables"]["accounts"][0])
        good["id"] = "second-account-id"
        good["maildir_path"] = "/data/mailboxes/second-account-id"
        good["store_id"] = populated["store"].id
        data["tables"]["accounts"].append(good)
        for model in (RepositoryAttachment, BackupPolicy, Account, Repository, User, MailStore):
            for row in db_session.query(model).all():
                db_session.delete(row)
        db_session.commit()

        report = cbs.import_export(db_session, data)

        assert any(e.startswith("accounts:") for e in report["errors"])
        # no SQLAlchemy parameter dump (row values / secrets) in errors
        assert all("parameters" not in e for e in report["errors"])
        assert all("bcrypt$x" not in e for e in report["errors"])
        assert report["imported"]["accounts"] == 1  # the good row after the bad one
        assert report["imported"]["mail_stores"] == 1
        assert report["imported"]["users"] == 1
        assert report["imported"]["backup_destinations"] == 1
        assert db_session.query(User).count() == 1
        assert db_session.query(Repository).count() == 1
        assert db_session.query(Account).one().id == "second-account-id"

    def test_import_into_seeded_fresh_install_remaps(self, db_session, populated):
        """The real DR scenario: a fresh install has already seeded a default
        MailStore (same path, new UUID) and an admin user (same username, new
        UUID) before import runs. Those rows are skipped via natural keys and
        every FK pointing at the old UUIDs is remapped to the seeded rows."""
        data = cbs.build_export(db_session)
        old_account_id = populated["account"].id
        for model in (RepositoryAttachment, BackupPolicy, Account, Repository, User, MailStore):
            for row in db_session.query(model).all():
                db_session.delete(row)
        db_session.commit()

        seeded_store = MailStore(name="default", path="/data/mailboxes")
        db_session.add(seeded_store)
        db_session.flush()
        seeded_user = User(
            username="alice",
            password_hash="seeded-hash",  # pragma: allowlist secret
            role=UserRole.admin,
            store_id=seeded_store.id,
        )
        db_session.add(seeded_user)
        db_session.commit()
        assert seeded_store.id != populated["store"].id
        assert seeded_user.id != populated["user"].id

        report = cbs.import_export(db_session, data)

        assert report["errors"] == []
        assert report["skipped"]["mail_stores"] == 1
        assert report["skipped"]["users"] == 1
        acc = db_session.query(Account).one()
        assert acc.id == old_account_id  # original UUID preserved
        assert acc.store_id == seeded_store.id  # FK remapped to seeded store
        assert [u.id for u in acc.owners] == [seeded_user.id]  # association remapped
        assert db_session.query(Repository).count() == 1
        assert db_session.query(BackupPolicy).count() == 1
        assert db_session.query(RepositoryAttachment).count() == 1

    def test_import_round_trips_preferences_json(self, db_session, populated):
        populated["user"].preferences = {"theme": "dark", "n": 3}
        db_session.commit()
        data = cbs.build_export(db_session)
        for model in (RepositoryAttachment, BackupPolicy, Account, Repository, User, MailStore):
            for row in db_session.query(model).all():
                db_session.delete(row)
        db_session.commit()

        report = cbs.import_export(db_session, data)

        assert report["errors"] == []
        user = db_session.query(User).one()
        assert user.preferences == {"theme": "dark", "n": 3}
