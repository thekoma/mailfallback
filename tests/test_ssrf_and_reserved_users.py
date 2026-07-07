"""Security regressions from the deepsec full revalidation (2026-07-06).

Covers the highest-severity remaining true-positives:
- SSRF: account PATCH update must reject internal imap_host (accounts.py)
- SSRF: validate_host_not_internal must reject unresolvable hosts + more ranges
- SSRF: /api/sync/discover must reject non-hostname domains
- Data loss: reserved _restore_ prefix cannot be taken by real users
"""

from unittest.mock import patch

import pytest

from mailfallback.models import UserRole
from mailfallback.services.dovecot_auth import TEMP_USER_PREFIX
from mailfallback.services.user_service import create_user


def _login(client, username, password):
    client.post("/api/auth/login", json={"username": username, "password": password})


class TestValidateHostNotInternal:
    def test_rejects_loopback(self):
        from mailfallback.services.imap_check import validate_host_not_internal

        with pytest.raises(ValueError, match="internal"):
            validate_host_not_internal("127.0.0.1")

    def test_rejects_cloud_metadata_ip(self):
        from mailfallback.services.imap_check import validate_host_not_internal

        with pytest.raises(ValueError, match="internal"):
            validate_host_not_internal("169.254.169.254")

    def test_rejects_cgnat_range(self):
        """100.64.0.0/10 is carrier-grade NAT — not caught by is_private."""
        from mailfallback.services.imap_check import validate_host_not_internal

        with pytest.raises(ValueError, match="internal"):
            validate_host_not_internal("100.64.0.1")

    def test_rejects_ipv4_mapped_internal(self):
        """An AAAA record can return the IPv4-mapped form of an internal IP."""
        from mailfallback.services.imap_check import validate_host_not_internal

        for mapped in ("::ffff:127.0.0.1", "::ffff:100.64.0.1", "::ffff:169.254.169.254"):
            with (
                patch(
                    "mailfallback.services.imap_check.socket.getaddrinfo",
                    return_value=[(10, 1, 6, "", (mapped, 0, 0, 0))],
                ),
                pytest.raises(ValueError, match="internal"),
            ):
                validate_host_not_internal("rebind.example.com")

    def test_rejects_unresolvable_host(self):
        """gaierror must NOT be swallowed — an unresolvable host is rejected."""
        import socket

        from mailfallback.services.imap_check import validate_host_not_internal

        with (
            patch(
                "mailfallback.services.imap_check.socket.getaddrinfo",
                side_effect=socket.gaierror("nope"),
            ),
            pytest.raises(ValueError),
        ):
            validate_host_not_internal("does-not-resolve.invalid")

    def test_allows_public_host(self):
        from mailfallback.services.imap_check import validate_host_not_internal

        with patch(
            "mailfallback.services.imap_check.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
        ):
            validate_host_not_internal("imap.example.com")  # must not raise


class TestResolvePublicIpPinning:
    def test_returns_public_ip(self):
        from mailfallback.services.imap_check import resolve_public_ip

        with patch(
            "mailfallback.services.imap_check.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
        ):
            assert resolve_public_ip("imap.example.com", 993) == "93.184.216.34"

    def test_rejects_internal_at_resolve(self):
        from mailfallback.services.imap_check import resolve_public_ip

        with (
            patch(
                "mailfallback.services.imap_check.socket.getaddrinfo",
                return_value=[(2, 1, 6, "", ("10.0.0.5", 0))],
            ),
            pytest.raises(ValueError, match="internal"),
        ):
            resolve_public_ip("rebind.example.com", 993)

    def test_test_connection_pins_ip_and_rejects_rebind(self, client, db_session, default_store):
        """test-connection resolves+pins; a rebinding flip to internal is caught."""
        create_user(db_session, "u1", "pass", UserRole.user, store_id=default_store.id)
        _login(client, "u1", "pass")

        # First resolution (route-level validate) public, second (pin at connect) internal.
        seq = [
            [(2, 1, 6, "", ("93.184.216.34", 0))],
            [(2, 1, 6, "", ("169.254.169.254", 0))],
        ]
        with patch(
            "mailfallback.services.imap_check.socket.getaddrinfo",
            side_effect=seq,
        ):
            resp = client.post(
                "/api/sync/test-connection",
                json={"imap_host": "rebind.example.com", "imap_port": 993},
            )
        assert resp.status_code == 200
        assert resp.json()["ok"] is False


class TestSyncTimeRebindGuard:
    def test_sync_blocked_when_host_resolves_internal(self, db_session, default_store):
        """Defense-in-depth: a sync whose account host resolves internal is failed."""
        from mailfallback.models import Account, JobStatus, SyncJob
        from mailfallback.services.sync_worker import execute_sync_job

        account = Account(
            name="Rebind",
            imap_host="rebind.example.com",
            imap_port=993,
            auth_type="app_password",
            maildir_path=f"{default_store.path}/rebind",
            store_id=default_store.id,
        )
        db_session.add(account)
        db_session.commit()
        job = SyncJob(account_id=account.id, source="manual", status=JobStatus.pending)
        db_session.add(job)
        db_session.commit()

        with patch(
            "mailfallback.services.imap_check.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("169.254.169.254", 0))],
        ):
            execute_sync_job(db_session, job.id)

        db_session.refresh(job)
        assert job.status == JobStatus.failed
        assert "internal" in (job.log or "")


class TestAccountUpdateSSRF:
    def test_patch_rejects_internal_imap_host(self, client, db_session, default_store):
        from mailfallback.services.account_service import assign_owner, create_account

        user = create_user(db_session, "user1", "pass", UserRole.user, store_id=default_store.id)
        account = create_account(
            db_session, "Gmail", "imap.gmail.com", 993, "app_password", store=default_store
        )
        assign_owner(db_session, account.id, user.id)
        _login(client, "user1", "pass")

        resp = client.patch(f"/api/accounts/{account.id}", json={"imap_host": "127.0.0.1"})

        assert resp.status_code == 422
        db_session.refresh(account)
        assert account.imap_host == "imap.gmail.com"

    def test_patch_still_allows_public_imap_host(self, client, db_session, default_store):
        from mailfallback.services.account_service import assign_owner, create_account

        user = create_user(db_session, "user1", "pass", UserRole.user, store_id=default_store.id)
        account = create_account(
            db_session, "Gmail", "imap.gmail.com", 993, "app_password", store=default_store
        )
        assign_owner(db_session, account.id, user.id)
        _login(client, "user1", "pass")

        with patch(
            "mailfallback.services.imap_check.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
        ):
            resp = client.patch(
                f"/api/accounts/{account.id}", json={"imap_host": "imap.fastmail.com"}
            )

        assert resp.status_code == 200
        db_session.refresh(account)
        assert account.imap_host == "imap.fastmail.com"


class TestDiscoverDomainValidation:
    def test_discover_rejects_ip_literal_domain(self, client, db_session, default_store):
        create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
        _login(client, "admin", "pass")

        # An IP literal must never reach discover_provider's URL builder.
        with patch("mailfallback.routers.sync.discover_provider") as mock_disc:
            resp = client.get("/api/sync/discover/169.254.169.254")

        assert resp.status_code == 422
        mock_disc.assert_not_called()

    def test_discover_rejects_non_hostname_domain(self, client, db_session, default_store):
        create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
        _login(client, "admin", "pass")

        with patch("mailfallback.routers.sync.discover_provider") as mock_disc:
            resp = client.get("/api/sync/discover/not_a_valid_host")

        assert resp.status_code == 422
        mock_disc.assert_not_called()

    def test_discover_allows_plain_domain(self, client, db_session, default_store):
        create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
        _login(client, "admin", "pass")

        with patch("mailfallback.routers.sync.discover_provider", return_value=None) as mock_disc:
            resp = client.get("/api/sync/discover/gmail.com")

        assert resp.status_code == 200
        mock_disc.assert_called_once_with("gmail.com")


class TestReservedRestoreUsername:
    def test_create_user_rejects_reserved_prefix(self, db_session, default_store):
        with pytest.raises(ValueError, match="reserved"):
            create_user(
                db_session,
                f"{TEMP_USER_PREFIX}team",
                "pass",
                UserRole.user,
                store_id=default_store.id,
            )

    def test_admin_create_user_reserved_prefix_flashes_not_500(
        self, client, db_session, default_store
    ):
        create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
        _login(client, "admin", "pass")

        resp = client.post(
            "/admin/users/new",
            data={
                "username": f"{TEMP_USER_PREFIX}evil",
                "password": "longenoughpassword",
                "role": "user",
                "store_id": default_store.id,
            },
            follow_redirects=False,
        )

        assert resp.status_code == 303
        from mailfallback.models import User

        assert (
            db_session.query(User).filter(User.username == f"{TEMP_USER_PREFIX}evil").first()
            is None
        )

    def test_create_temp_imap_user_still_uses_prefix(self, db_session, default_store):
        """The internal helper must keep working — it isn't a user-chosen name."""
        from mailfallback.models import Account
        from mailfallback.services.dovecot_auth import create_temp_imap_user

        acct = Account(
            name="t",
            imap_host="imap.test.com",
            imap_port=993,
            maildir_path="/data/mailboxes/t",
            store_id=default_store.id,
        )
        db_session.add(acct)
        db_session.commit()

        username, _ = create_temp_imap_user(db_session, [acct.id])
        assert username.startswith(TEMP_USER_PREFIX)
