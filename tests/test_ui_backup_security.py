"""Security regressions for restore/recovery routes (deepsec scan 2026-07-06).

Covers:
- IDOR: recovery delete must be scoped to the account authorized in the URL
- Argument injection: snapshot_id must be validated before reaching restic
"""

from unittest.mock import MagicMock, patch

from mailfallback.models import (
    Account,
    BackendType,
    Recovery,
    RecoveryStatus,
    Repository,
    RepositoryAttachment,
    User,
    UserRole,
)
from mailfallback.services.user_service import create_user


def _login_admin(client, db_session, default_store):
    user = create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    client.post("/api/auth/login", json={"username": "admin", "password": "pass"})
    return user


def _mk_account(db_session, default_store, name):
    acct = Account(
        name=name,
        store=default_store,
        maildir_path=f"{default_store.path}/{name}",
        imap_host="imap.example.com",
    )
    db_session.add(acct)
    db_session.commit()
    db_session.refresh(acct)
    return acct


def _mk_recovery(db_session, account_id):
    rec = Recovery(
        account_id=account_id,
        snapshot_id="ab12cd34",
        restore_path="/nonexistent-recovery-path",
        status=RecoveryStatus.ready,
    )
    db_session.add(rec)
    db_session.commit()
    return rec.id


class TestRecoveryDeleteScoping:
    def test_cannot_delete_recovery_of_other_account(self, client, db_session, default_store):
        """POST /accounts/A/recoveries/R/delete with R belonging to B must keep R."""
        _login_admin(client, db_session, default_store)
        mine = _mk_account(db_session, default_store, "mine")
        other = _mk_account(db_session, default_store, "other")
        rec_id = _mk_recovery(db_session, other.id)

        resp = client.post(
            f"/accounts/{mine.id}/recoveries/{rec_id}/delete", follow_redirects=False
        )

        assert resp.status_code == 303
        assert db_session.query(Recovery).filter(Recovery.id == rec_id).first() is not None

    def test_cross_account_delete_attempt_not_audited_as_success(
        self, client, db_session, default_store
    ):
        """A delete that did nothing must not produce a recovery_delete audit row."""
        from mailfallback.models import AuditLog

        _login_admin(client, db_session, default_store)
        mine = _mk_account(db_session, default_store, "mine")
        other = _mk_account(db_session, default_store, "other")
        rec_id = _mk_recovery(db_session, other.id)

        client.post(f"/accounts/{mine.id}/recoveries/{rec_id}/delete", follow_redirects=False)

        logs = db_session.query(AuditLog).filter(AuditLog.action == "account.recovery_delete").all()
        assert logs == []

    def test_non_admin_owner_cannot_delete_other_accounts_recovery(
        self, client, db_session, default_store
    ):
        """The realistic IDOR: a plain user owning account A targets account B's recovery."""
        create_user(db_session, "bob", "pass", UserRole.user, store_id=default_store.id)
        mine = _mk_account(db_session, default_store, "mine")
        other = _mk_account(db_session, default_store, "other")
        user = db_session.query(User).filter(User.username == "bob").first()
        mine.owners.append(user)
        db_session.commit()
        client.post("/api/auth/login", json={"username": "bob", "password": "pass"})
        rec_id = _mk_recovery(db_session, other.id)

        resp = client.post(
            f"/accounts/{mine.id}/recoveries/{rec_id}/delete", follow_redirects=False
        )

        assert resp.status_code == 303
        assert db_session.query(Recovery).filter(Recovery.id == rec_id).first() is not None

    def test_still_deletes_own_recovery(self, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        mine = _mk_account(db_session, default_store, "mine")
        rec_id = _mk_recovery(db_session, mine.id)

        resp = client.post(
            f"/accounts/{mine.id}/recoveries/{rec_id}/delete", follow_redirects=False
        )

        assert resp.status_code == 303
        assert db_session.query(Recovery).filter(Recovery.id == rec_id).first() is None


class TestSnapshotIdValidation:
    @patch("mailfallback.services.recovery_service.create_recovery")
    def test_backup_restore_rejects_flag_like_snapshot_id(
        self, mock_create, client, db_session, default_store
    ):
        """A snapshot_id that isn't hex or 'latest' must never reach restic."""
        _login_admin(client, db_session, default_store)
        acct = _mk_account(db_session, default_store, "a1")

        resp = client.post(f"/accounts/{acct.id}/backup/restore/--use-fuse", follow_redirects=False)

        assert resp.status_code == 303
        mock_create.assert_not_called()

    @patch("mailfallback.services.recovery_service.create_recovery")
    def test_backup_restore_rejects_trailing_newline(
        self, mock_create, client, db_session, default_store
    ):
        """`$` matches before a trailing newline — the check must use fullmatch (%0A bypass)."""
        _login_admin(client, db_session, default_store)
        acct = _mk_account(db_session, default_store, "a1")

        resp = client.post(
            f"/accounts/{acct.id}/backup/restore/ab12cd34%0A", follow_redirects=False
        )

        assert resp.status_code == 303
        mock_create.assert_not_called()

    @patch("mailfallback.services.recovery_service.create_recovery")
    def test_backup_restore_accepts_hex_snapshot_id(
        self, mock_create, client, db_session, default_store
    ):
        _login_admin(client, db_session, default_store)
        acct = _mk_account(db_session, default_store, "a1")
        recovery = MagicMock()
        recovery.status.value = "ready"
        recovery.id = "rec-1"
        mock_create.return_value = recovery

        resp = client.post(f"/accounts/{acct.id}/backup/restore/ab12cd34", follow_redirects=False)

        assert resp.status_code == 303
        mock_create.assert_called_once()

    @patch("mailfallback.services.recovery_service.create_recovery")
    def test_backup_restore_accepts_latest(self, mock_create, client, db_session, default_store):
        _login_admin(client, db_session, default_store)
        acct = _mk_account(db_session, default_store, "a1")
        recovery = MagicMock()
        recovery.status.value = "ready"
        recovery.id = "rec-1"
        mock_create.return_value = recovery

        resp = client.post(f"/accounts/{acct.id}/backup/restore/latest", follow_redirects=False)

        assert resp.status_code == 303
        mock_create.assert_called_once()

    @patch("mailfallback.services.recovery_service.create_recovery")
    def test_attachment_restore_rejects_flag_like_snapshot_id(
        self, mock_create, client, db_session, default_store
    ):
        """Same validation on the attachment-restore route."""
        _login_admin(client, db_session, default_store)
        acct = _mk_account(db_session, default_store, "a1")
        repo = Repository(
            name="r",
            backend_type=BackendType.local,
            local_path="/tmp/r",
            restic_password="enc-secret",  # pragma: allowlist secret
        )
        db_session.add(repo)
        db_session.flush()
        att = RepositoryAttachment(repository_id=repo.id, account_id=acct.id, prefix="old")
        db_session.add(att)
        db_session.commit()

        resp = client.post(
            f"/accounts/{acct.id}/backup/attachments/{att.id}/restore/--no-lock",
            follow_redirects=False,
        )

        assert resp.status_code == 303
        mock_create.assert_not_called()
