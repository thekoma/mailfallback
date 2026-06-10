"""Admin repository create/edit flows — probe mocked."""

from unittest.mock import patch

from mailfallback.config import settings
from mailfallback.models import Account, BackendType, Repository, RepositoryAttachment, UserRole
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


class TestInlineTest:
    @patch("mailfallback.services.s3_probe.probe")
    def test_htmx_test_returns_partial_ok(self, mock_probe, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        mock_probe.return_value = {"ok": True, "error": None}
        client.post("/admin/backup/new", data=S3_FORM, follow_redirects=False)
        repo = db_session.query(Repository).one()

        resp = client.post(f"/admin/backup/{repo.id}/test", headers={"HX-Request": "true"})

        assert resp.status_code == 200
        assert "Connection OK" in resp.text

    @patch("mailfallback.services.s3_probe.probe")
    def test_htmx_test_returns_partial_error(self, mock_probe, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        mock_probe.return_value = {"ok": True, "error": None}
        client.post("/admin/backup/new", data=S3_FORM, follow_redirects=False)
        repo = db_session.query(Repository).one()
        mock_probe.return_value = {"ok": False, "error": "AccessDenied"}

        resp = client.post(f"/admin/backup/{repo.id}/test", headers={"HX-Request": "true"})

        assert resp.status_code == 200
        assert "AccessDenied" in resp.text

    @patch("mailfallback.services.s3_probe.probe")
    def test_non_htmx_test_still_redirects(self, mock_probe, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        mock_probe.return_value = {"ok": True, "error": None}
        client.post("/admin/backup/new", data=S3_FORM, follow_redirects=False)
        repo = db_session.query(Repository).one()

        resp = client.post(f"/admin/backup/{repo.id}/test", follow_redirects=False)

        assert resp.status_code == 303

    def test_htmx_test_missing_repo_returns_partial(self, client, db_session, default_store):
        _login_admin(client, db_session, default_store)

        resp = client.post("/admin/backup/bogus-id/test", headers={"HX-Request": "true"})

        assert resp.status_code == 200
        assert "Repository not found" in resp.text


def _mk_account(db_session, default_store, name="acc1", path="/data/m/acc1"):
    acc = Account(name=name, imap_host="h", maildir_path=path, store_id=default_store.id)
    db_session.add(acc)
    db_session.commit()
    return acc


def _mk_repo(client, db_session, default_store):
    with patch("mailfallback.services.s3_probe.probe") as mock_probe:
        mock_probe.return_value = {"ok": True, "error": None}
        client.post("/admin/backup/new", data=S3_FORM, follow_redirects=False)
    return db_session.query(Repository).one()


class TestContents:
    @patch("mailfallback.services.repo_inventory.list_prefixes")
    def test_contents_panel_classifies(self, mock_list, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        acc = _mk_account(db_session, default_store)
        repo = _mk_repo(client, db_session, default_store)
        mock_list.return_value = [acc.id, "__mfb_config__", "ghost-uuid"]

        resp = client.get(f"/admin/backup/{repo.id}/contents")

        assert resp.status_code == 200
        assert "ghost-uuid" in resp.text
        assert "Orphan" in resp.text
        assert "Attach" in resp.text

    @patch("mailfallback.services.repo_inventory.list_prefixes")
    def test_contents_error_is_rendered(self, mock_list, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        repo = _mk_repo(client, db_session, default_store)
        mock_list.side_effect = RuntimeError("boom")

        resp = client.get(f"/admin/backup/{repo.id}/contents")

        assert resp.status_code == 200
        assert "boom" in resp.text

    @patch("mailfallback.services.repo_inventory.restic_service")
    def test_prefix_detail_partial(self, mock_restic, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        repo = _mk_repo(client, db_session, default_store)
        mock_restic.list_snapshots.return_value = [
            {"short_id": "ab12", "time": "2026-06-09T03:00:00Z"}
        ]

        resp = client.get(f"/admin/backup/{repo.id}/contents/ghost-uuid/detail")

        assert resp.status_code == 200
        assert "1" in resp.text


class TestAttach:
    def test_attach_creates_row(self, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        acc = _mk_account(db_session, default_store)
        repo = _mk_repo(client, db_session, default_store)

        resp = client.post(
            f"/admin/backup/{repo.id}/attach",
            data={"prefix": "ghost-uuid", "account_id": acc.id},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        att = db_session.query(RepositoryAttachment).one()
        assert att.prefix == "ghost-uuid"
        assert att.account_id == acc.id

    def test_attach_duplicate_prefix_rejected(self, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        acc = _mk_account(db_session, default_store)
        repo = _mk_repo(client, db_session, default_store)
        client.post(
            f"/admin/backup/{repo.id}/attach",
            data={"prefix": "ghost-uuid", "account_id": acc.id},
            follow_redirects=False,
        )

        resp = client.post(
            f"/admin/backup/{repo.id}/attach",
            data={"prefix": "ghost-uuid", "account_id": acc.id},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert db_session.query(RepositoryAttachment).count() == 1

    def test_attach_prefix_matching_account_id_rejected(self, client, db_session, default_store):
        """A prefix equal to a live Account.id would be shadowed in classify()."""
        _login_admin(client, db_session, default_store)
        acc = _mk_account(db_session, default_store)
        other = _mk_account(db_session, default_store, name="acc2", path="/data/m/acc2")
        repo = _mk_repo(client, db_session, default_store)

        resp = client.post(
            f"/admin/backup/{repo.id}/attach",
            data={"prefix": acc.id, "account_id": other.id},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert db_session.query(RepositoryAttachment).count() == 0

    def test_detach_deletes_row(self, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        acc = _mk_account(db_session, default_store)
        repo = _mk_repo(client, db_session, default_store)
        client.post(
            f"/admin/backup/{repo.id}/attach",
            data={"prefix": "ghost-uuid", "account_id": acc.id},
            follow_redirects=False,
        )
        att = db_session.query(RepositoryAttachment).one()

        resp = client.post(f"/admin/backup/attachments/{att.id}/delete", follow_redirects=False)

        assert resp.status_code == 303
        assert db_session.query(RepositoryAttachment).count() == 0


class TestPrefixValidation:
    def test_detail_rejects_dotdot_prefix(self, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        repo = _mk_repo(client, db_session, default_store)

        # ".." sent percent-encoded so the HTTP client does not normalize the
        # path before the route's {prefix} param receives the raw value.
        resp = client.get(f"/admin/backup/{repo.id}/contents/%2E%2E/detail")

        assert resp.status_code == 400

    def test_attach_rejects_dotdot_prefix(self, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        acc = _mk_account(db_session, default_store)
        repo = _mk_repo(client, db_session, default_store)

        resp = client.post(
            f"/admin/backup/{repo.id}/attach",
            data={"prefix": "..", "account_id": acc.id},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert db_session.query(RepositoryAttachment).count() == 0


class TestAttachmentRestore:
    def _attach(self, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        acc = _mk_account(db_session, default_store)
        repo = _mk_repo(client, db_session, default_store)
        client.post(
            f"/admin/backup/{repo.id}/attach",
            data={"prefix": "ghost-uuid", "account_id": acc.id},
            follow_redirects=False,
        )
        att = db_session.query(RepositoryAttachment).one()
        return acc, repo, att

    @patch("mailfallback.services.recovery_service.create_recovery")
    def test_restore_uses_attachment_prefix(self, mock_create, client, db_session, default_store):
        from types import SimpleNamespace

        acc, repo, att = self._attach(client, db_session, default_store)
        mock_create.return_value = SimpleNamespace(
            id="rec-1", status=SimpleNamespace(value="ready"), error=None
        )

        resp = client.post(
            f"/accounts/{acc.id}/backup/attachments/{att.id}/restore/ab12",
            follow_redirects=False,
        )

        assert resp.status_code == 303
        mock_create.assert_called_once()
        kwargs = mock_create.call_args.kwargs
        assert kwargs["source_prefix"] == "ghost-uuid"
        assert kwargs["source_repository"].id == repo.id

    @patch("mailfallback.services.recovery_service.create_recovery")
    def test_restore_unknown_attachment_404s_softly(
        self, mock_create, client, db_session, default_store
    ):
        acc, _, _ = self._attach(client, db_session, default_store)

        resp = client.post(
            f"/accounts/{acc.id}/backup/attachments/bogus-id/restore/ab12",
            follow_redirects=False,
        )

        assert resp.status_code == 303
        mock_create.assert_not_called()

    @patch("mailfallback.services.recovery_service.create_recovery")
    def test_restore_attachment_scoped_to_account(
        self, mock_create, client, db_session, default_store
    ):
        """An attachment belonging to another account must not be usable."""
        _, _, att = self._attach(client, db_session, default_store)
        other = _mk_account(db_session, default_store, name="acc2", path="/data/m/acc2")

        resp = client.post(
            f"/accounts/{other.id}/backup/attachments/{att.id}/restore/ab12",
            follow_redirects=False,
        )

        assert resp.status_code == 303
        mock_create.assert_not_called()

    @patch("mailfallback.services.restic_service.list_snapshots")
    def test_snapshots_panel_lists_attached_sources(
        self, mock_list, client, db_session, default_store
    ):
        """A policy-less account with an attachment still gets a snapshots panel."""
        acc, _, att = self._attach(client, db_session, default_store)
        mock_list.return_value = [
            {"short_id": "ab12", "time": "2026-06-01T00:00:00Z", "hostname": "mfb"}
        ]

        resp = client.get(f"/accounts/{acc.id}/backup/snapshots")

        assert resp.status_code == 200
        assert "Attached:" in resp.text
        assert "ghost-uuid" in resp.text
        assert f"/accounts/{acc.id}/backup/attachments/{att.id}/restore/ab12" in resp.text
