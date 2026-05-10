# tests/test_promote_recovered.py
"""Tests for the Wave 1b 'Promote to live' button and route."""

from mailfallback.models import Account, UserRole
from mailfallback.services.user_service import create_user


def _login(client, username, password):
    client.post("/api/auth/login", json={"username": username, "password": password})


def _make_account(db_session, name, default_store, owner_id, *, suspended=True):
    """Build a placeholder Account directly (mimics the restore flow)."""
    account = Account(
        name=name,
        email_address="test@example.com",
        imap_host="restored",
        imap_port=0,
        maildir_path=f"/tmp/recovered/{name}",
        store_id=default_store.id,
        suspended=suspended,
    )
    db_session.add(account)
    db_session.flush()
    from mailfallback.services.account_service import assign_owner

    assign_owner(db_session, account.id, owner_id)
    db_session.commit()
    db_session.refresh(account)
    return account


def test_recovered_account_can_be_promoted(client, db_session, default_store):
    admin = create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    account = _make_account(
        db_session, "Recovered Gmail (2026-05-10)", default_store, admin.id, suspended=True
    )
    _login(client, "admin", "pass")

    resp = client.post(f"/accounts/{account.id}/promote-recovered", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/accounts/{account.id}"

    db_session.refresh(account)
    assert account.suspended is False


def test_non_recovered_account_cannot_be_promoted(client, db_session, default_store):
    admin = create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    # Note: name does not start with "Recovered "
    account = _make_account(db_session, "Gmail", default_store, admin.id, suspended=True)
    _login(client, "admin", "pass")

    resp = client.post(f"/accounts/{account.id}/promote-recovered", follow_redirects=False)
    assert resp.status_code == 303

    db_session.refresh(account)
    # Promotion was blocked; account stays suspended.
    assert account.suspended is True


def test_promote_requires_login(client, db_session, default_store):
    admin = create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    account = _make_account(
        db_session, "Recovered Gmail (2026-05-10)", default_store, admin.id, suspended=True
    )
    # No login.
    resp = client.post(f"/accounts/{account.id}/promote-recovered", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
    db_session.refresh(account)
    assert account.suspended is True
