"""Robustness true-positives from the deepsec revalidation (2026-07-06).

- login form missing fields must not 500
- templates dir must be resolvable regardless of CWD
- account form missing fields must not 500
- _to_imap_date must not raise on malformed input
- a cancelled restore job must not be promoted to completed
- config export/import must round-trip the full connection profile
"""

from unittest.mock import MagicMock, patch

import pytest

from mailfallback.models import (
    Account,
    JobStatus,
    UserRole,
)
from mailfallback.services.account_service import create_account
from mailfallback.services.user_service import create_user


@pytest.fixture(autouse=True)
def _public_dns():
    with patch(
        "mailfallback.services.imap_check.socket.getaddrinfo",
        return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
    ):
        yield


def _login(client, username, password):
    client.post("/api/auth/login", json={"username": username, "password": password})


class TestFormRobustness:
    def test_login_missing_fields_no_500(self, client, db_session, default_store):
        resp = client.post("/login", data={}, follow_redirects=False)
        assert resp.status_code == 200
        assert "Invalid credentials" in resp.text

    def test_account_form_missing_fields_no_500(self, client, db_session, default_store):
        create_user(db_session, "u1", "pass", UserRole.user, store_id=default_store.id)
        _login(client, "u1", "pass")
        resp = client.post("/accounts/new", data={"name": "x"}, follow_redirects=False)
        # Missing auth_type → friendly redirect, not a 500 trace.
        assert resp.status_code == 303


class TestTemplatesPath:
    def test_templates_dir_is_absolute_and_exists(self):
        import os

        from mailfallback.routers.ui import templates

        directory = templates.env.loader.searchpath[0]
        assert os.path.isabs(directory)
        assert os.path.isdir(directory)


class TestToImapDate:
    def test_malformed_date_returns_none(self):
        from mailfallback.routers.ui_restore import _to_imap_date

        assert _to_imap_date("not-a-date") is None
        assert _to_imap_date("2026-13-40") is None

    def test_valid_date_converts(self):
        from mailfallback.routers.ui_restore import _to_imap_date

        assert _to_imap_date("2026-07-06") == "6-Jul-2026"


class TestRestoreCancelNotOverwritten:
    def test_cancel_wins_over_completion(self):
        from mailfallback.services.restore_worker import _finalize_running_job

        job = MagicMock(restored_messages=100, failed_messages=0)
        _finalize_running_job(job, was_cancelled=True)
        assert job.status == JobStatus.cancelled

    def test_clean_run_completes(self):
        from mailfallback.services.restore_worker import _finalize_running_job

        job = MagicMock(restored_messages=100, failed_messages=0)
        _finalize_running_job(job, was_cancelled=False)
        assert job.status == JobStatus.completed

    def test_all_failed_marks_failed(self):
        from mailfallback.services.restore_worker import _finalize_running_job

        job = MagicMock(restored_messages=0, failed_messages=5)
        _finalize_running_job(job, was_cancelled=False)
        assert job.status == JobStatus.failed


class TestConfigRoundTrip:
    def test_export_includes_full_profile(self, client, db_session, default_store):
        create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
        _login(client, "admin", "pass")
        acct = create_account(
            db_session, "Gmail", "imap.gmail.com", 993, "app_password", store=default_store
        )
        acct.tls_type = "STARTTLS"
        acct.imap_user = "custom@gmail.com"
        acct.provider = "google"
        db_session.commit()

        data = client.get("/api/config/export").json()["accounts"][0]
        assert data["tls_type"] == "STARTTLS"
        assert data["imap_user"] == "custom@gmail.com"
        assert data["provider"] == "google"
        assert data["enabled"] is True

    def test_import_restores_full_profile(self, client, db_session, default_store):
        create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
        _login(client, "admin", "pass")
        payload = {
            "accounts": [
                {
                    "name": "Imported",
                    "imap_host": "imap.imported.com",
                    "imap_port": 143,
                    "auth_type": "app_password",
                    "tls_type": "STARTTLS",
                    "imap_user": "iu@imported.com",
                    "provider": "other",
                    "enabled": True,
                    "sync_schedule": "*/10 * * * *",
                    "store_id": default_store.id,
                }
            ]
        }
        resp = client.post("/api/config/import", json=payload)
        assert resp.status_code == 200
        assert resp.json()["imported"] == 1

        acct = db_session.query(Account).filter(Account.name == "Imported").first()
        assert acct.tls_type == "STARTTLS"
        assert acct.imap_user == "iu@imported.com"

    def test_import_rejects_invalid_auth_type(self, client, db_session, default_store):
        create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
        _login(client, "admin", "pass")
        payload = {
            "accounts": [
                {
                    "name": "Bad",
                    "imap_host": "imap.imported.com",
                    "imap_port": 993,
                    "auth_type": "totally-bogus",
                    "sync_schedule": None,
                    "store_id": default_store.id,
                }
            ]
        }
        resp = client.post("/api/config/import", json=payload)
        # Pydantic enum validation rejects the whole request body.
        assert resp.status_code == 422
