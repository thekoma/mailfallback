"""Access-token lifecycle and verification."""

from datetime import UTC, datetime, timedelta

import pytest

from mailfallback.models import AppCredential, User, UserRole
from mailfallback.services import app_credential_service as svc


@pytest.fixture
def token_user(db_session, default_store):
    user = User(
        username="agentuser",
        password_hash="x",
        role=UserRole.user,
        enabled=True,
        store_id=default_store.id,
    )
    db_session.add(user)
    db_session.commit()
    return user


class TestCreate:
    def test_create_returns_a_token_that_is_not_stored(self, db_session, token_user):
        cred, token = svc.create_credential(
            db_session, token_user, name="Hermes", scopes=[svc.SCOPE_IMAP]
        )

        assert token.startswith("mfb_")
        assert token.count("_") == 2
        _, prefix, secret = token.split("_")
        assert prefix == cred.token_prefix
        # The secret itself must be unrecoverable from the row.
        assert secret not in cred.secret_hash
        assert cred.scopes == "imap"
        assert cred.expires_at is None
        assert cred.last_used_at is None

    def test_create_with_ttl_sets_expiry(self, db_session, token_user):
        cred, _ = svc.create_credential(
            db_session, token_user, name="t", scopes=[svc.SCOPE_IMAP], ttl_days=30
        )

        expires = cred.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        delta = expires - datetime.now(UTC)
        assert timedelta(days=29) < delta < timedelta(days=31)

    def test_create_rejects_an_unknown_scope(self, db_session, token_user):
        with pytest.raises(ValueError, match="scope"):
            svc.create_credential(db_session, token_user, name="t", scopes=["mail:write"])

    def test_create_rejects_an_empty_scope_list(self, db_session, token_user):
        with pytest.raises(ValueError, match="scope"):
            svc.create_credential(db_session, token_user, name="t", scopes=[])

    def test_two_credentials_never_share_a_prefix(self, db_session, token_user):
        a, _ = svc.create_credential(db_session, token_user, name="a", scopes=[svc.SCOPE_IMAP])
        b, _ = svc.create_credential(db_session, token_user, name="b", scopes=[svc.SCOPE_IMAP])
        assert a.token_prefix != b.token_prefix


class TestVerify:
    def _cred(self, db_session, user, scopes=(svc.SCOPE_IMAP,)):
        return svc.create_credential(db_session, user, name="t", scopes=list(scopes))

    def test_a_valid_token_verifies_and_records_usage(self, db_session, token_user):
        cred, token = self._cred(db_session, token_user)

        result, found = svc.verify_credential(
            db_session,
            username="agentuser",
            token=token,
            required_scope=svc.SCOPE_IMAP,
            kind="imap",
        )

        assert result is svc.VerifyResult.ok
        assert found.id == cred.id
        assert cred.last_used_at is not None
        assert cred.last_used_kind == "imap"

    def test_a_plain_password_is_not_a_token(self, db_session, token_user):
        result, found = svc.verify_credential(
            db_session,
            username="agentuser",
            token="my-real-password",
            required_scope=svc.SCOPE_IMAP,
            kind="imap",
        )

        assert result is svc.VerifyResult.not_a_token
        assert found is None

    def test_an_unknown_prefix_is_unknown(self, db_session, token_user):
        result, _ = svc.verify_credential(
            db_session,
            username="agentuser",
            token="mfb_nosuchprefix_secret",
            required_scope=svc.SCOPE_IMAP,
            kind="imap",
        )
        assert result is svc.VerifyResult.unknown

    def test_a_token_belonging_to_another_user_is_unknown(
        self, db_session, default_store, token_user
    ):
        _, token = self._cred(db_session, token_user)
        other = User(
            username="someoneelse",
            password_hash="x",
            role=UserRole.user,
            enabled=True,
            store_id=default_store.id,
        )
        db_session.add(other)
        db_session.commit()

        result, _ = svc.verify_credential(
            db_session,
            username="someoneelse",
            token=token,
            required_scope=svc.SCOPE_IMAP,
            kind="imap",
        )
        assert result is svc.VerifyResult.unknown

    def test_a_wrong_secret_is_rejected(self, db_session, token_user):
        cred, _ = self._cred(db_session, token_user)

        result, _ = svc.verify_credential(
            db_session,
            username="agentuser",
            token=f"mfb_{cred.token_prefix}_wrongsecret",
            required_scope=svc.SCOPE_IMAP,
            kind="imap",
        )
        assert result is svc.VerifyResult.rejected

    def test_a_revoked_token_is_rejected(self, db_session, token_user):
        cred, token = self._cred(db_session, token_user)
        cred.revoked_at = datetime.now(UTC)
        db_session.commit()

        result, _ = svc.verify_credential(
            db_session,
            username="agentuser",
            token=token,
            required_scope=svc.SCOPE_IMAP,
            kind="imap",
        )
        assert result is svc.VerifyResult.rejected

    def test_an_expired_token_is_rejected(self, db_session, token_user):
        cred, token = self._cred(db_session, token_user)
        cred.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db_session.commit()

        result, _ = svc.verify_credential(
            db_session,
            username="agentuser",
            token=token,
            required_scope=svc.SCOPE_IMAP,
            kind="imap",
        )
        assert result is svc.VerifyResult.rejected

    def test_a_token_without_the_required_scope_is_rejected(self, db_session, token_user):
        _, token = self._cred(db_session, token_user, scopes=(svc.SCOPE_MAIL_READ,))

        result, _ = svc.verify_credential(
            db_session,
            username="agentuser",
            token=token,
            required_scope=svc.SCOPE_IMAP,
            kind="imap",
        )
        assert result is svc.VerifyResult.rejected

    def test_a_disabled_user_is_rejected(self, db_session, token_user):
        _, token = self._cred(db_session, token_user)
        token_user.enabled = False
        db_session.commit()

        result, _ = svc.verify_credential(
            db_session,
            username="agentuser",
            token=token,
            required_scope=svc.SCOPE_IMAP,
            kind="imap",
        )
        assert result is svc.VerifyResult.rejected

    def test_a_migrating_user_is_rejected(self, db_session, token_user):
        _, token = self._cred(db_session, token_user)
        token_user.migrating = True
        db_session.commit()

        result, _ = svc.verify_credential(
            db_session,
            username="agentuser",
            token=token,
            required_scope=svc.SCOPE_IMAP,
            kind="imap",
        )
        assert result is svc.VerifyResult.rejected

    def test_a_malformed_token_is_not_a_token(self, db_session, token_user):
        for bad in ("mfb_", "mfb_onlyprefix", "mfb__emptyprefix"):
            result, _ = svc.verify_credential(
                db_session,
                username="agentuser",
                token=bad,
                required_scope=svc.SCOPE_IMAP,
                kind="imap",
            )
            assert result is svc.VerifyResult.not_a_token, bad

    def test_a_valid_token_verifies_without_a_username(self, db_session, token_user):
        """HTTP bearer auth has no username in hand — the token identifies the user."""
        cred, token = self._cred(db_session, token_user)

        result, found = svc.verify_credential(
            db_session,
            username=None,
            token=token,
            required_scope=svc.SCOPE_IMAP,
            kind="api",
        )

        assert result is svc.VerifyResult.ok
        assert found.id == cred.id
        assert found.user.id == token_user.id
        assert cred.last_used_kind == "api"

    def test_username_less_verification_still_enforces_every_check(self, db_session, token_user):
        """Dropping the username must not drop the rest of the ladder."""
        # revoked
        cred, token = self._cred(db_session, token_user)
        cred.revoked_at = datetime.now(UTC)
        db_session.commit()
        result, _ = svc.verify_credential(
            db_session, username=None, token=token, required_scope=svc.SCOPE_IMAP, kind="api"
        )
        assert result is svc.VerifyResult.rejected

        # expired
        cred2, token2 = self._cred(db_session, token_user)
        cred2.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db_session.commit()
        result, _ = svc.verify_credential(
            db_session, username=None, token=token2, required_scope=svc.SCOPE_IMAP, kind="api"
        )
        assert result is svc.VerifyResult.rejected

        # missing scope
        _, token3 = self._cred(db_session, token_user, scopes=(svc.SCOPE_MAIL_READ,))
        result, _ = svc.verify_credential(
            db_session, username=None, token=token3, required_scope=svc.SCOPE_IMAP, kind="api"
        )
        assert result is svc.VerifyResult.rejected

        # wrong secret
        cred4, _ = self._cred(db_session, token_user)
        result, _ = svc.verify_credential(
            db_session,
            username=None,
            token=f"mfb_{cred4.token_prefix}_wrong",
            required_scope=svc.SCOPE_IMAP,
            kind="api",
        )
        assert result is svc.VerifyResult.rejected

        # disabled user
        _, token5 = self._cred(db_session, token_user)
        token_user.enabled = False
        db_session.commit()
        result, _ = svc.verify_credential(
            db_session, username=None, token=token5, required_scope=svc.SCOPE_IMAP, kind="api"
        )
        assert result is svc.VerifyResult.rejected

    def test_username_less_verification_rejects_a_non_token(self, db_session, token_user):
        result, found = svc.verify_credential(
            db_session,
            username=None,
            token="not-a-token",
            required_scope=svc.SCOPE_MAIL_READ,
            kind="api",
        )
        assert result is svc.VerifyResult.not_a_token
        assert found is None

    def test_passing_a_username_still_matches_it(self, db_session, default_store, token_user):
        """The passdb path must be unchanged: a username that does not own the
        token still resolves to `unknown`."""
        _, token = self._cred(db_session, token_user)
        other = User(
            username="notmine",
            password_hash="x",
            role=UserRole.user,
            enabled=True,
            store_id=default_store.id,
        )
        db_session.add(other)
        db_session.commit()

        result, _ = svc.verify_credential(
            db_session,
            username="notmine",
            token=token,
            required_scope=svc.SCOPE_IMAP,
            kind="imap",
        )
        assert result is svc.VerifyResult.unknown


class TestListAndRevoke:
    def test_list_returns_only_the_users_own_credentials(
        self, db_session, default_store, token_user
    ):
        svc.create_credential(db_session, token_user, name="mine", scopes=[svc.SCOPE_IMAP])
        other = User(
            username="other",
            password_hash="x",
            role=UserRole.user,
            enabled=True,
            store_id=default_store.id,
        )
        db_session.add(other)
        db_session.commit()
        svc.create_credential(db_session, other, name="theirs", scopes=[svc.SCOPE_IMAP])

        names = [c.name for c in svc.list_credentials(db_session, token_user)]
        assert names == ["mine"]

    def test_revoke_marks_the_row_and_keeps_it(self, db_session, token_user):
        cred, _ = svc.create_credential(db_session, token_user, name="t", scopes=[svc.SCOPE_IMAP])

        assert svc.revoke_credential(db_session, token_user, cred.id) is True

        assert cred.revoked_at is not None
        assert db_session.query(AppCredential).count() == 1

    def test_revoke_refuses_another_users_credential(self, db_session, default_store, token_user):
        cred, _ = svc.create_credential(db_session, token_user, name="t", scopes=[svc.SCOPE_IMAP])
        other = User(
            username="other2",
            password_hash="x",
            role=UserRole.user,
            enabled=True,
            store_id=default_store.id,
        )
        db_session.add(other)
        db_session.commit()

        assert svc.revoke_credential(db_session, other, cred.id) is False
        assert cred.revoked_at is None
