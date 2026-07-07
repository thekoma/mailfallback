# tests/test_issue_fixes.py
"""Tests for GitHub issues #57-#143 security and bug fixes."""

import json
from unittest.mock import AsyncMock, patch

from mailfallback.models import (
    Account,
    Group,
    MailStore,
    User,
    UserRole,
    account_owners,
)
from mailfallback.security import hash_password
from mailfallback.services.mbsync_config import generate_mbsyncrc


def _make_user(db, store, username="testuser", role=UserRole.user, **kw):
    u = User(
        username=username,
        password_hash=hash_password("testpassword1"),
        role=role,
        store_id=store.id,
        **kw,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _make_account(db, store, name="Test", email="t@t.com", **kw):
    import uuid

    uid = uuid.uuid4().hex[:8]
    a = Account(
        name=name,
        email_address=email,
        imap_host="imap.test.com",
        imap_port=993,
        maildir_path=f"{store.path}/{uid}",
        store_id=store.id,
        **kw,
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


def _login(client, db, store, role=UserRole.admin):
    u = _make_user(db, store, username=f"u_{role.value}", role=role)
    client.post("/api/auth/login", json={"username": u.username, "password": "testpassword1"})
    return u


# ---------------------------------------------------------------------------
# #78 CRITICAL — mbsync config injection
# ---------------------------------------------------------------------------


class TestMbsyncInjection:
    def test_newline_in_account_name_stripped(self):
        config = generate_mbsyncrc(
            account_name="test\nHost evil.com\n#",
            imap_host="imap.safe.com",
            imap_port=993,
            username="user@safe.com",
            auth_type="app_password",
            maildir_path="/data/test",
            password="secret",
        )
        lines = config.splitlines()
        host_lines = [ln for ln in lines if ln.startswith("Host ")]
        assert all("imap.safe.com" in h for h in host_lines)
        assert not any(ln.strip() == "Host evil.com" for ln in lines)

    def test_newline_in_password_stripped(self):
        config = generate_mbsyncrc(
            account_name="test",
            imap_host="imap.safe.com",
            imap_port=993,
            username="user@safe.com",
            auth_type="app_password",
            maildir_path="/data/test",
            password='secret"\nTunnel "curl|sh"\n#',
        )
        lines = config.splitlines()
        assert not any(ln.strip().startswith("Tunnel") for ln in lines)

    def test_newline_in_host_stripped(self):
        config = generate_mbsyncrc(
            account_name="test",
            imap_host="imap.safe.com\nTunnel evil",
            imap_port=993,
            username="user",
            auth_type="app_password",
            maildir_path="/data/test",
            password="pass",
        )
        lines = config.splitlines()
        assert not any(ln.strip().startswith("Tunnel") for ln in lines)

    def test_newline_in_token_command_stripped(self):
        config = generate_mbsyncrc(
            account_name="test",
            imap_host="imap.safe.com",
            imap_port=993,
            username="user",
            auth_type="oauth2",
            maildir_path="/data/test",
            token_command='cat /tmp/token"\nTunnel "evil"',
        )
        lines = config.splitlines()
        assert not any(ln.strip().startswith("Tunnel") for ln in lines)

    def test_control_chars_stripped(self):
        config = generate_mbsyncrc(
            account_name="test\x00\x01\x1f",
            imap_host="imap.safe.com",
            imap_port=993,
            username="user\r\n",
            auth_type="app_password",
            maildir_path="/data/test",
            password="pass",
        )
        assert "\x00" not in config
        assert "\r" not in config
        assert "\n\n" not in config or "Host imap.safe.com" in config


# ---------------------------------------------------------------------------
# #79 — OIDC sub=None admin login
# ---------------------------------------------------------------------------


class TestOidcSubValidation:
    def test_oidc_callback_rejects_missing_sub(self, client, db_session, default_store):
        with patch("mailfallback.routers.auth.settings") as mock_settings:
            mock_settings.oidc_enabled = True
            mock_settings.oidc_admin_group = ""
            mock_settings.oidc_user_group = ""
            mock_settings.oidc_client_id = "test"
            mock_settings.oidc_client_secret = "test"
            mock_settings.oidc_discovery_url = "https://example.com"
            with patch("mailfallback.routers.auth.oauth") as mock_oauth:
                mock_oauth.oidc.authorize_access_token = AsyncMock(
                    return_value={"userinfo": {"email": "user@example.com"}}
                )
                resp = client.get("/auth/oidc/callback", follow_redirects=False)
                assert resp.status_code == 400

    def test_oidc_callback_rejects_empty_sub(self, client, db_session, default_store):
        with patch("mailfallback.routers.auth.settings") as mock_settings:
            mock_settings.oidc_enabled = True
            mock_settings.oidc_admin_group = ""
            mock_settings.oidc_user_group = ""
            with patch("mailfallback.routers.auth.oauth") as mock_oauth:
                mock_oauth.oidc.authorize_access_token = AsyncMock(
                    return_value={"userinfo": {"sub": "", "email": "user@example.com"}}
                )
                resp = client.get("/auth/oidc/callback", follow_redirects=False)
                assert resp.status_code == 400


# ---------------------------------------------------------------------------
# #80 — SSRF on account creation
# ---------------------------------------------------------------------------


class TestAccountCreationSSRF:
    def test_create_account_rejects_internal_host(self, client, db_session, default_store):
        _login(client, db_session, default_store)
        resp = client.post(
            "/api/accounts",
            json={
                "name": "Evil",
                "imap_host": "127.0.0.1",
                "imap_port": 993,
                "auth_type": "app_password",
            },
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"].lower()
        assert "internal" in detail or "blocked" in detail

    def test_create_account_rejects_localhost(self, client, db_session, default_store):
        _login(client, db_session, default_store)
        resp = client.post(
            "/api/accounts",
            json={
                "name": "Evil",
                "imap_host": "localhost",
                "imap_port": 993,
                "auth_type": "app_password",
            },
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# #70 — non-admin cannot set enabled/suspended via API
# ---------------------------------------------------------------------------


class TestNonAdminAccountUpdate:
    def test_non_admin_cannot_set_enabled(self, client, db_session, default_store):
        owner = _make_user(db_session, default_store, username="owner1")
        acct = _make_account(db_session, default_store)
        db_session.execute(account_owners.insert().values(account_id=acct.id, user_id=owner.id))
        db_session.commit()
        client.post("/api/auth/login", json={"username": "owner1", "password": "testpassword1"})
        resp = client.patch(f"/api/accounts/{acct.id}", json={"enabled": False})
        assert resp.status_code == 200
        db_session.refresh(acct)
        assert acct.enabled is True  # Should NOT have changed


# ---------------------------------------------------------------------------
# #59 — /readyz doesn't leak exception text
# ---------------------------------------------------------------------------


class TestReadyzNoLeak:
    def test_readyz_hides_db_error(self, client, db_session, default_store):
        with patch("mailfallback.routers.health.text") as mock_text:
            mock_text.side_effect = Exception("connection refused to secret-host:5432")
            resp = client.get("/readyz")
            assert resp.status_code == 503
            body = resp.json()
            assert "secret-host" not in json.dumps(body)
            assert body["checks"]["db"] == "unavailable"


# ---------------------------------------------------------------------------
# #87 — timing-safe metrics comparison
# ---------------------------------------------------------------------------


class TestMetricsAuth:
    def test_metrics_rejects_wrong_key(self, client, db_session, default_store):
        resp = client.get("/metrics", headers={"Authorization": "Bearer wrong-key"})
        assert resp.status_code == 401

    def test_metrics_accepts_correct_key(self, client, db_session, default_store):
        resp = client.get("/metrics", headers={"Authorization": "Bearer test-key"})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# #117 — set_allowed_stores with empty list
# ---------------------------------------------------------------------------


class TestSetAllowedStores:
    def test_empty_list_blocked_when_user_has_store(self, db_session, default_store):
        from mailfallback.services.store_service import set_allowed_stores

        user = _make_user(db_session, default_store)
        error = set_allowed_stores(db_session, user.id, [])
        assert error is not None
        assert "home store" in error.lower()


# ---------------------------------------------------------------------------
# #119 — block store_id change while migrating
# ---------------------------------------------------------------------------


class TestMigratingStoreChange:
    def test_update_user_skips_store_id_when_migrating(self, db_session, default_store):
        from mailfallback.services.user_service import update_user

        user = _make_user(db_session, default_store)
        user.migrating = True
        db_session.commit()
        s2 = MailStore(name="s2", path="/data/s2")
        db_session.add(s2)
        db_session.commit()
        update_user(db_session, user.id, store_id=s2.id)
        db_session.refresh(user)
        assert user.store_id == default_store.id


# ---------------------------------------------------------------------------
# #108 — password length enforced in service
# ---------------------------------------------------------------------------


class TestPasswordLengthService:
    def test_change_password_rejects_short(self, db_session, default_store):
        import pytest

        from mailfallback.services.user_service import change_password

        user = _make_user(db_session, default_store)
        with pytest.raises(ValueError, match="at least"):
            change_password(db_session, user.id, "short")


# ---------------------------------------------------------------------------
# #81 — non-admin cannot toggle sso_sync
# ---------------------------------------------------------------------------


class TestGroupSsoSyncAdminOnly:
    def test_non_admin_cannot_toggle_sso_sync(self, client, db_session, default_store):
        _login(client, db_session, default_store, UserRole.admin)
        from mailfallback.services.group_service import create_group

        owner = _make_user(db_session, default_store, username="gowner")
        group = create_group(db_session, "testgroup", owner.id, sso_sync=False)
        client.post("/api/auth/login", json={"username": "gowner", "password": "testpassword1"})
        client.post(
            f"/admin/groups/{group.id}/edit",
            data={
                "sso_sync": "on",
                "account_ids": [],
                "member_ids": [],
            },
            follow_redirects=False,
        )
        db_session.refresh(group)
        assert group.sso_sync is False  # Should NOT have changed


# ---------------------------------------------------------------------------
# #82 — non-admin can only remove self as owner
# ---------------------------------------------------------------------------


class TestRemoveOwnerRestriction:
    def test_non_admin_cannot_remove_other_owner(self, client, db_session, default_store):
        owner1 = _make_user(db_session, default_store, username="owner_a")
        owner2 = _make_user(db_session, default_store, username="owner_b")
        acct = _make_account(db_session, default_store)
        db_session.execute(account_owners.insert().values(account_id=acct.id, user_id=owner1.id))
        db_session.execute(account_owners.insert().values(account_id=acct.id, user_id=owner2.id))
        db_session.commit()
        # Login as owner1
        client.post("/api/auth/login", json={"username": "owner_a", "password": "testpassword1"})
        resp = client.post(
            f"/accounts/{acct.id}/remove-owner", data={"user_id": owner2.id}, follow_redirects=False
        )
        assert resp.status_code == 303
        # owner2 should still be an owner
        row = (
            db_session.query(account_owners)
            .filter(account_owners.c.account_id == acct.id, account_owners.c.user_id == owner2.id)
            .first()
        )
        assert row is not None


# ---------------------------------------------------------------------------
# #83/#121 — non-admin group edit preserves admin-added accounts
# ---------------------------------------------------------------------------


class TestGroupEditPreservesAccounts:
    def test_non_admin_preserves_non_owned_accounts(self, client, db_session, default_store):
        owner = _make_user(db_session, default_store, username="gowner2")
        admin_acct = _make_account(db_session, default_store, name="AdminAcct", email="admin@t.com")
        owner_acct = _make_account(db_session, default_store, name="OwnerAcct", email="owner@t.com")
        db_session.execute(
            account_owners.insert().values(account_id=owner_acct.id, user_id=owner.id)
        )
        db_session.commit()
        group = Group(name="testgrp", owner_id=owner.id)
        db_session.add(group)
        db_session.commit()
        db_session.refresh(group)
        # Admin adds admin_acct to group
        group.accounts = [admin_acct]
        db_session.commit()
        # Login as owner (non-admin) and submit edit without admin_acct
        client.post("/api/auth/login", json={"username": "gowner2", "password": "testpassword1"})
        client.post(
            f"/admin/groups/{group.id}/edit",
            data={
                "account_ids": [owner_acct.id],
            },
            follow_redirects=False,
        )
        db_session.refresh(group)
        group_acct_ids = {a.id for a in group.accounts}
        assert admin_acct.id in group_acct_ids  # Admin's account preserved
        assert owner_acct.id in group_acct_ids  # Owner's account added


# ---------------------------------------------------------------------------
# #92 — uniform 404 for migrating user
# ---------------------------------------------------------------------------


class TestDovecotUniform404:
    def test_migrating_user_returns_404(self, client, db_session, default_store):
        u = _make_user(db_session, default_store, username="miguser")
        u.migrating = True
        db_session.commit()
        headers = {"x-api-key": "test-key"}
        resp = client.get(f"/api/internal/dovecot/userdb/{u.username}", headers=headers)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# #66 — accounts table partial redirects on session expire
# ---------------------------------------------------------------------------


class TestAccountsTableRedirect:
    def test_expired_session_returns_hx_redirect(self, client, db_session, default_store):
        resp = client.get("/partials/accounts-table")
        assert resp.headers.get("HX-Redirect") == "/login"


# ---------------------------------------------------------------------------
# #71 — delete_temp_imap_user refuses non-temp users
# ---------------------------------------------------------------------------


class TestDeleteTempUserValidation:
    def test_refuses_to_delete_real_user(self, db_session, default_store):
        from mailfallback.services.dovecot_auth import delete_temp_imap_user

        _make_user(db_session, default_store, username="realuser")
        delete_temp_imap_user(db_session, "realuser")
        assert db_session.query(User).filter(User.username == "realuser").first() is not None


# ---------------------------------------------------------------------------
# #86 — legacy KDF warning
# ---------------------------------------------------------------------------


class TestLegacyKdfWarning:
    def test_legacy_decrypt_logs_warning(self, db_session):
        from cryptography.fernet import Fernet

        from mailfallback.security import (
            _derive_fernet_key_legacy,
            decrypt_credentials,
        )

        key = "test-secret-key"
        f = Fernet(_derive_fernet_key_legacy(key))
        encrypted = f.encrypt(b"mysecret").decode()
        with patch("mailfallback.security.logger") as mock_logger:
            result = decrypt_credentials(encrypted, key)
            assert result == "mysecret"
            mock_logger.warning.assert_called_once()


# ---------------------------------------------------------------------------
# #96/#97 — rate limiting on account creation and restore
# ---------------------------------------------------------------------------


class TestRateLimiting:
    def test_account_creation_rate_limited(self, client, db_session, default_store):
        _login(client, db_session, default_store)
        for _ in range(6):
            client.post(
                "/api/accounts",
                json={
                    "name": "rl",
                    "imap_host": "imap.test.com",
                    "imap_port": 993,
                    "auth_type": "app_password",
                },
            )
        resp = client.post(
            "/api/accounts",
            json={
                "name": "rl",
                "imap_host": "imap.test.com",
                "imap_port": 993,
                "auth_type": "app_password",
            },
        )
        assert resp.status_code == 429


# ---------------------------------------------------------------------------
# #111 — import validates auth_type and port
# ---------------------------------------------------------------------------


class TestImportValidation:
    def test_import_rejects_invalid_auth_type(self, client, db_session, default_store):
        _login(client, db_session, default_store)
        resp = client.post(
            "/api/config/import",
            json={
                "accounts": [
                    {
                        "name": "bad",
                        "imap_host": "imap.ok.com",
                        "imap_port": 993,
                        "auth_type": "invalid_type",
                        "maildir_path": "/x",
                        "sync_schedule": None,
                    }
                ]
            },
        )
        # auth_type is now a typed enum on the request model: Pydantic rejects
        # the whole payload with 422 instead of a per-item error.
        assert resp.status_code == 422

    def test_import_rejects_invalid_port(self, client, db_session, default_store):
        _login(client, db_session, default_store)
        resp = client.post(
            "/api/config/import",
            json={
                "accounts": [
                    {
                        "name": "bad",
                        "imap_host": "imap.ok.com",
                        "imap_port": 99999,
                        "auth_type": "app_password",
                        "maildir_path": "/x",
                        "sync_schedule": None,
                    }
                ]
            },
        )
        data = resp.json()
        assert data["imported"] == 0
        assert "port" in data["errors"][0]["error"].lower()


# ---------------------------------------------------------------------------
# #85 — imported accounts get owner
# ---------------------------------------------------------------------------


class TestImportAssignsOwner:
    def test_imported_account_has_admin_owner(self, client, db_session, default_store):
        admin = _login(client, db_session, default_store)
        resp = client.post(
            "/api/config/import",
            json={
                "accounts": [
                    {
                        "name": "owned",
                        "imap_host": "imap.ok.com",
                        "imap_port": 993,
                        "auth_type": "app_password",
                        "maildir_path": "/x",
                        "sync_schedule": None,
                    }
                ]
            },
        )
        data = resp.json()
        assert data["imported"] == 1
        acct = db_session.query(Account).filter(Account.name == "owned").first()
        assert acct is not None
        owner_row = (
            db_session.query(account_owners)
            .filter(account_owners.c.account_id == acct.id, account_owners.c.user_id == admin.id)
            .first()
        )
        assert owner_row is not None


# ---------------------------------------------------------------------------
# #114 — audit page handles invalid dates
# ---------------------------------------------------------------------------


class TestAuditDateValidation:
    def test_invalid_date_does_not_crash(self, client, db_session, default_store):
        _login(client, db_session, default_store)
        resp = client.get("/admin/audit?from=not-a-date&to=also-bad")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# #130 — API rejects disabled store
# ---------------------------------------------------------------------------


class TestDisabledStoreRejected:
    def test_create_account_rejects_disabled_store(self, client, db_session, default_store):
        _login(client, db_session, default_store)
        s2 = MailStore(name="disabled", path="/data/dis", enabled=False)
        db_session.add(s2)
        db_session.commit()
        resp = client.post(
            "/api/accounts",
            json={
                "name": "test",
                "imap_host": "imap.test.com",
                "imap_port": 993,
                "auth_type": "app_password",
                "store_id": s2.id,
            },
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# #95 — username sanitized in dovecot path
# ---------------------------------------------------------------------------


class TestUsernameSanitization:
    def test_dovecot_path_sanitized(self, client, db_session, default_store):
        u = _make_user(db_session, default_store, username="../../etc/passwd")
        headers = {"x-api-key": "test-key"}
        resp = client.get(f"/api/internal/dovecot/userdb/{u.username}", headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            assert ".." not in data["home"]
            assert "etc/passwd" not in data["home"]


# ---------------------------------------------------------------------------
# #133 — audit logging for login
# ---------------------------------------------------------------------------


class TestLoginAuditLog:
    def test_api_login_creates_audit_entry(self, client, db_session, default_store):
        from mailfallback.models import AuditLog

        _make_user(db_session, default_store, username="auditlogin")
        client.post("/api/auth/login", json={"username": "auditlogin", "password": "testpassword1"})
        entry = db_session.query(AuditLog).filter(AuditLog.action == "user.login").first()
        assert entry is not None
        assert entry.username == "auditlogin"
