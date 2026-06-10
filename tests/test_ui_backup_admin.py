"""Admin repository create/edit flows — probe mocked."""

from unittest.mock import patch

from mailfallback.config import settings
from mailfallback.models import BackendType, Repository, UserRole
from mailfallback.security import decrypt_credentials
from mailfallback.services.user_service import create_user


def _login_admin(client, db_session, default_store):
    user = create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    client.post("/api/auth/login", json={"username": "admin", "password": "pass"})
    return user


S3_FORM = {
    "name": "repo1",
    "backend_type": "s3",
    "restic_password": "resticpass",  # pragma: allowlist secret
    "s3_endpoint": "https://s3.example.com",
    "s3_bucket": "bucket",
    "s3_access_key": "ak",
    "s3_secret_key": "sk",  # pragma: allowlist secret
}


class TestCreate:
    @patch("mailfallback.services.s3_probe.probe")
    def test_failed_probe_saves_nothing(self, mock_probe, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        mock_probe.return_value = {"ok": False, "error": "AccessDenied"}

        resp = client.post("/admin/backup/new", data=S3_FORM, follow_redirects=False)

        assert resp.status_code == 303
        assert db_session.query(Repository).count() == 0

    @patch("mailfallback.services.s3_probe.probe")
    def test_successful_probe_saves(self, mock_probe, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        mock_probe.return_value = {"ok": True, "error": None}

        resp = client.post("/admin/backup/new", data=S3_FORM, follow_redirects=False)

        assert resp.status_code == 303
        repo = db_session.query(Repository).one()
        assert repo.name == "repo1"
        assert repo.backend_type == BackendType.s3
        mock_probe.assert_called_once()


class TestEdit:
    @patch("mailfallback.services.s3_probe.probe")
    def test_failed_probe_rolls_back_changes(self, mock_probe, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        mock_probe.return_value = {"ok": True, "error": None}
        client.post("/admin/backup/new", data=S3_FORM, follow_redirects=False)
        repo = db_session.query(Repository).one()
        old_bucket = repo.s3_bucket

        mock_probe.return_value = {"ok": False, "error": "NoSuchBucket"}
        resp = client.post(
            f"/admin/backup/{repo.id}/edit",
            data={"name": "renamed", "s3_bucket": "other-bucket"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        db_session.expire_all()
        repo = db_session.query(Repository).one()
        assert repo.name == "repo1"
        assert repo.s3_bucket == old_bucket

    @patch("mailfallback.services.s3_probe.probe")
    def test_successful_probe_commits_changes(self, mock_probe, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        mock_probe.return_value = {"ok": True, "error": None}
        client.post("/admin/backup/new", data=S3_FORM, follow_redirects=False)
        repo = db_session.query(Repository).one()

        resp = client.post(
            f"/admin/backup/{repo.id}/edit",
            data={"name": "renamed", "s3_bucket": "other-bucket"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        db_session.expire_all()
        repo = db_session.query(Repository).one()
        assert repo.name == "renamed"
        assert decrypt_credentials(repo.s3_bucket, settings.secret_key) == "other-bucket"
