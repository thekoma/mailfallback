"""DR config-restore flow on the System page — restic mocked end to end."""

import os
from unittest.mock import patch

from mailfallback.models import Account, UserRole
from mailfallback.services import config_backup_service as cbs
from mailfallback.services.user_service import create_user


def _login_admin(client, db_session, default_store):
    user = create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    client.post("/api/auth/login", json={"username": "admin", "password": "pass"})
    return user


RESTORE_FORM = {
    "backend_type": "s3",
    "s3_endpoint": "https://s3.example.com",
    "s3_bucket": "bucket",
    "s3_access_key": "ak",
    "s3_secret_key": "sk",  # pragma: allowlist secret
    "restic_password": "rp",  # pragma: allowlist secret
    "passphrase": "averylongpassphrase",
}


def _fake_fetch(blob):
    """fetch_latest_config replacement writing `blob` and returning its path."""

    def fetch(repository, target_dir):
        path = os.path.join(target_dir, "mfb-config.json.enc")
        with open(path, "wb") as f:
            f.write(blob)
        return path

    return fetch


def _export_blob(db_session, passphrase="averylongpassphrase"):  # noqa: S107
    return cbs.encrypt_export(cbs.build_export(db_session), passphrase)


class TestPreview:
    @patch("mailfallback.routers.ui_admin.config_backup_service")
    def test_preview_shows_counts(self, mock_cbs, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        blob = _export_blob(db_session)
        mock_cbs.fetch_latest_config.side_effect = _fake_fetch(blob)
        mock_cbs.decrypt_export = cbs.decrypt_export
        mock_cbs.ConfigDecryptError = cbs.ConfigDecryptError

        resp = client.post("/admin/system/config-restore/preview", data=RESTORE_FORM)

        assert resp.status_code == 200
        assert "users" in resp.text.lower()
        assert "Confirm restore" in resp.text

    @patch("mailfallback.routers.ui_admin.config_backup_service")
    def test_wrong_passphrase_shows_error(self, mock_cbs, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        blob = _export_blob(db_session, passphrase="different")
        mock_cbs.fetch_latest_config.side_effect = _fake_fetch(blob)
        mock_cbs.decrypt_export = cbs.decrypt_export
        mock_cbs.ConfigDecryptError = cbs.ConfigDecryptError

        resp = client.post("/admin/system/config-restore/preview", data=RESTORE_FORM)

        assert resp.status_code == 200
        assert "passphrase" in resp.text.lower()

    def test_preview_requires_admin(self, client, db_session, default_store):
        create_user(db_session, "user1", "pass", UserRole.user, store_id=default_store.id)
        client.post("/api/auth/login", json={"username": "user1", "password": "pass"})

        resp = client.post(
            "/admin/system/config-restore/preview", data=RESTORE_FORM, follow_redirects=False
        )

        assert resp.status_code == 303


class TestConfirm:
    @patch("mailfallback.routers.ui_admin.config_backup_service")
    def test_confirm_imports(self, mock_cbs, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        acc = Account(
            name="ghost",
            imap_host="h",
            maildir_path="/data/m/ghost",
            store_id=default_store.id,
        )
        db_session.add(acc)
        db_session.commit()
        original_id = acc.id
        blob = _export_blob(db_session)
        db_session.delete(acc)
        db_session.commit()

        mock_cbs.fetch_latest_config.side_effect = _fake_fetch(blob)
        mock_cbs.decrypt_export = cbs.decrypt_export
        mock_cbs.import_export = cbs.import_export
        mock_cbs.ConfigDecryptError = cbs.ConfigDecryptError

        resp = client.post(
            "/admin/system/config-restore/confirm", data=RESTORE_FORM, follow_redirects=False
        )

        assert resp.status_code == 303
        restored = db_session.query(Account).filter(Account.name == "ghost").one()
        assert restored.id == original_id  # ID preserved

    @patch("mailfallback.routers.ui_admin.config_backup_service")
    def test_confirm_failure_flashes_error(self, mock_cbs, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        mock_cbs.fetch_latest_config.side_effect = RuntimeError("bucket unreachable")
        mock_cbs.ConfigDecryptError = cbs.ConfigDecryptError

        resp = client.post(
            "/admin/system/config-restore/confirm", data=RESTORE_FORM, follow_redirects=False
        )

        assert resp.status_code == 303
