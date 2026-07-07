"""Security regressions from the deepsec full revalidation (2026-07-06), round 3.

- IDOR: restore progress poll must be scoped to the job's source account
- CSRF: OAuth callback must not delete a pending account on state mismatch
"""

from mailfallback.models import (
    Account,
    JobStatus,
    RestoreJob,
    RestoreMode,
    UserRole,
)
from mailfallback.services.account_service import (
    assign_owner,
    create_account,
    get_account_credentials,
)
from mailfallback.services.user_service import create_user


def _login(client, username, password):
    client.post("/api/auth/login", json={"username": username, "password": password})


def _mk_restore_job(db_session, source_account_id, requested_by):
    job = RestoreJob(
        source_account_id=source_account_id,
        target_account_id=source_account_id,
        status=JobStatus.running,
        restore_mode=RestoreMode.full,
        error="IMAP error: AUTHENTICATE failed for victim@gmail.com",
        total_messages=100,
        restored_messages=42,
        requested_by=requested_by,
    )
    db_session.add(job)
    db_session.commit()
    return job.id


class TestRestoreProgressIDOR:
    def test_cannot_poll_other_accounts_restore_job(self, client, db_session, default_store):
        owner = create_user(db_session, "owner", "pass", UserRole.user, store_id=default_store.id)
        create_user(db_session, "atk", "pass", UserRole.user, store_id=default_store.id)
        victim_acct = create_account(
            db_session, "Victim", "imap.gmail.com", 993, "app_password", store=default_store
        )
        assign_owner(db_session, victim_acct.id, owner.id)
        job_id = _mk_restore_job(db_session, victim_acct.id, owner.id)

        _login(client, "atk", "pass")
        resp = client.get(f"/restore/partials/progress?job_id={job_id}")

        # Must not leak the other tenant's counts or error string.
        assert resp.status_code == 200
        assert "victim@gmail.com" not in resp.text
        assert "42" not in resp.text

    def test_owner_can_poll_own_restore_job(self, client, db_session, default_store):
        owner = create_user(db_session, "owner", "pass", UserRole.user, store_id=default_store.id)
        acct = create_account(
            db_session, "Mine", "imap.gmail.com", 993, "app_password", store=default_store
        )
        assign_owner(db_session, acct.id, owner.id)
        job_id = _mk_restore_job(db_session, acct.id, owner.id)

        _login(client, "owner", "pass")
        resp = client.get(f"/restore/partials/progress?job_id={job_id}")

        assert resp.status_code == 200
        assert "42" in resp.text


class TestLegacyKdfUpgrade:
    def test_is_legacy_encrypted_detects_legacy(self):
        from cryptography.fernet import Fernet

        from mailfallback.security import (
            _derive_fernet_key_legacy,
            encrypt_credentials,
            is_legacy_encrypted,
        )

        key = "some-secret-key"
        legacy = Fernet(_derive_fernet_key_legacy(key)).encrypt(b"secret").decode()
        modern = encrypt_credentials("secret", key)

        assert is_legacy_encrypted(legacy, key) is True
        assert is_legacy_encrypted(modern, key) is False

    def test_get_account_credentials_upgrades_legacy_ciphertext(self, db_session, default_store):
        from cryptography.fernet import Fernet

        from mailfallback.config import settings
        from mailfallback.security import (
            _derive_fernet_key_legacy,
            is_legacy_encrypted,
        )

        legacy_ct = (
            Fernet(_derive_fernet_key_legacy(settings.secret_key))
            .encrypt(b"imap-app-password")
            .decode()
        )
        account = create_account(
            db_session, "Legacy", "imap.gmail.com", 993, "app_password", store=default_store
        )
        account.credentials = legacy_ct
        db_session.commit()

        plain = get_account_credentials(db_session, account.id)

        assert plain == "imap-app-password"
        db_session.refresh(account)
        # The stored ciphertext must have been rewritten with the modern KDF.
        assert account.credentials != legacy_ct
        assert is_legacy_encrypted(account.credentials, settings.secret_key) is False


class TestOAuthCallbackCSRF:
    def test_state_mismatch_does_not_delete_pending_account(
        self, client, db_session, default_store
    ):
        """A forged cross-site callback with a bad state must not delete the stub."""
        user = create_user(db_session, "victim", "pass", UserRole.user, store_id=default_store.id)
        account = Account(
            name="Pending",
            imap_host="imap.gmail.com",
            imap_port=993,
            maildir_path=f"{default_store.path}/pending",
            store_id=default_store.id,
            credentials=None,
        )
        db_session.add(account)
        db_session.commit()
        assign_owner(db_session, account.id, user.id)
        acct_id = account.id

        _login(client, "victim", "pass")
        # Prime the session as if an OAuth flow had started.
        client.get(f"/auth/google/start?account_id={acct_id}", follow_redirects=False)

        # Attacker-forged callback: wrong state.
        resp = client.get("/auth/google/callback?state=WRONG&code=abc", follow_redirects=False)

        assert resp.status_code == 303
        assert db_session.query(Account).filter(Account.id == acct_id).first() is not None
