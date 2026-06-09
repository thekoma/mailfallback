# tests/test_account_hero_state.py
"""Hero panel state for the account detail page."""

from mailfallback.models import SyncState, UserRole
from mailfallback.routers.ui_accounts import _compute_hero_state
from mailfallback.services.account_service import create_account
from mailfallback.services.sync_worker import TOKEN_REFRESH_FAILED
from mailfallback.services.user_service import create_user


def _oauth_account(db_session, default_store):
    create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    account = create_account(
        db_session,
        name="Gmail",
        imap_host="imap.gmail.com",
        imap_port=993,
        auth_type="oauth2",
        store=default_store,
        provider="google",
    )
    account.credentials = "encrypted-but-stale"
    db_session.commit()
    return account


def test_token_refresh_failure_maps_to_sign_in_needed(db_session, default_store):
    """A revoked/expired refresh token leaves credentials in place, so
    is_authenticated stays True — the hero must still ask to reconnect
    instead of showing 'Backup failed — unknown error'."""
    account = _oauth_account(db_session, default_store)
    account.sync_state = SyncState.error
    account.last_error = TOKEN_REFRESH_FAILED
    db_session.commit()

    state, _snap, _job = _compute_hero_state(account, db_session)
    assert state == "sign-in-needed"


def test_other_errors_still_map_to_error_state(db_session, default_store):
    account = _oauth_account(db_session, default_store)
    account.sync_state = SyncState.error
    account.last_error = "mbsync exited with code 1"
    db_session.commit()

    state, _snap, _job = _compute_hero_state(account, db_session)
    assert state == "error"
