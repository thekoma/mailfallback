# tests/test_security_fixes.py
"""Tests for security fixes: OAuth auth (#13), account update ACL (#12), group ACL (#11)."""

import pytest

from mailfallback.models import MailStore, UserRole
from mailfallback.services.account_service import assign_owner, create_account
from mailfallback.services.group_service import add_member, create_group, set_group_accounts
from mailfallback.services.user_service import create_user


@pytest.fixture
def tmp_store(db_session, tmp_path):
    s = MailStore(name="sec-test", path=str(tmp_path / "mailboxes"))
    db_session.add(s)
    db_session.commit()
    db_session.refresh(s)
    return s


def _login(client, username, password):
    client.post("/api/auth/login", json={"username": username, "password": password})


# === Issue #13: OAuth start requires auth + ownership ===


def test_oauth_start_requires_login(client, db_session, default_store):
    admin = create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    account = create_account(
        db_session,
        name="Test",
        imap_host="imap.gmail.com",
        imap_port=993,
        auth_type="oauth2",
        store=default_store,
        provider="google",
    )
    assign_owner(db_session, account.id, admin.id)
    resp = client.get(f"/auth/google/start?account_id={account.id}", follow_redirects=False)
    assert resp.status_code == 401


def test_oauth_start_requires_ownership(client, db_session, default_store):
    owner = create_user(db_session, "owner", "pass", UserRole.user, store_id=default_store.id)
    create_user(db_session, "other", "pass", UserRole.user, store_id=default_store.id)
    account = create_account(
        db_session,
        name="Test",
        imap_host="imap.gmail.com",
        imap_port=993,
        auth_type="oauth2",
        store=default_store,
        provider="google",
    )
    assign_owner(db_session, account.id, owner.id)
    _login(client, "other", "pass")
    resp = client.get(f"/auth/google/start?account_id={account.id}", follow_redirects=False)
    assert resp.status_code == 403


def test_oauth_start_allowed_for_owner(client, db_session, default_store):
    owner = create_user(db_session, "owner", "pass", UserRole.user, store_id=default_store.id)
    account = create_account(
        db_session,
        name="Test",
        imap_host="imap.gmail.com",
        imap_port=993,
        auth_type="oauth2",
        store=default_store,
        provider="google",
    )
    assign_owner(db_session, account.id, owner.id)
    _login(client, "owner", "pass")
    resp = client.get(f"/auth/google/start?account_id={account.id}", follow_redirects=False)
    # Should redirect to Google OAuth (302/307), not 401/403
    assert resp.status_code in (302, 307)


def test_microsoft_oauth_start_requires_login(client, db_session, default_store):
    admin = create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    account = create_account(
        db_session,
        name="Test",
        imap_host="outlook.office365.com",
        imap_port=993,
        auth_type="oauth2",
        store=default_store,
        provider="microsoft",
    )
    assign_owner(db_session, account.id, admin.id)
    resp = client.get(f"/auth/microsoft/start?account_id={account.id}", follow_redirects=False)
    assert resp.status_code == 401


# === Issue #12: Group members cannot modify accounts ===


def test_group_member_cannot_update_account(client, db_session, tmp_store):
    owner = create_user(db_session, "owner", "pass", UserRole.user, store_id=tmp_store.id)
    member = create_user(db_session, "member", "pass", UserRole.user, store_id=tmp_store.id)
    account = create_account(
        db_session,
        name="SharedAcct",
        email_address="shared@example.com",
        imap_host="imap.example.com",
        imap_port=993,
        auth_type="app_password",
        store=tmp_store,
    )
    assign_owner(db_session, account.id, owner.id)
    group = create_group(db_session, "readers", owner.id)
    add_member(db_session, group.id, member.id)
    set_group_accounts(db_session, group.id, [account.id])

    _login(client, "member", "pass")
    resp = client.patch(
        f"/api/accounts/{account.id}",
        json={"imap_host": "evil.example.com"},
    )
    assert resp.status_code == 404


def test_owner_can_update_account(client, db_session, tmp_store):
    owner = create_user(db_session, "owner", "pass", UserRole.user, store_id=tmp_store.id)
    account = create_account(
        db_session,
        name="MyAcct",
        email_address="me@example.com",
        imap_host="imap.example.com",
        imap_port=993,
        auth_type="app_password",
        store=tmp_store,
    )
    assign_owner(db_session, account.id, owner.id)

    _login(client, "owner", "pass")
    resp = client.patch(
        f"/api/accounts/{account.id}",
        json={"name": "Updated Name"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Updated Name"


def test_group_member_can_view_account(client, db_session, tmp_store):
    owner = create_user(db_session, "owner", "pass", UserRole.user, store_id=tmp_store.id)
    member = create_user(db_session, "member", "pass", UserRole.user, store_id=tmp_store.id)
    account = create_account(
        db_session,
        name="SharedAcct",
        email_address="shared@example.com",
        imap_host="imap.example.com",
        imap_port=993,
        auth_type="app_password",
        store=tmp_store,
    )
    assign_owner(db_session, account.id, owner.id)
    group = create_group(db_session, "readers", owner.id)
    add_member(db_session, group.id, member.id)
    set_group_accounts(db_session, group.id, [account.id])

    _login(client, "member", "pass")
    resp = client.get(f"/api/accounts/{account.id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "SharedAcct"


# === Issue #11: Non-admin group owners restricted to own accounts ===


def test_nonadmin_group_owner_cannot_add_others_accounts(client, db_session, tmp_store):
    owner = create_user(db_session, "gowner", "pass", UserRole.user, store_id=tmp_store.id)
    victim = create_user(db_session, "victim", "pass", UserRole.user, store_id=tmp_store.id)
    own_account = create_account(
        db_session,
        name="OwnAcct",
        email_address="own@example.com",
        imap_host="imap.example.com",
        imap_port=993,
        auth_type="app_password",
        store=tmp_store,
    )
    assign_owner(db_session, own_account.id, owner.id)
    victim_account = create_account(
        db_session,
        name="VictimAcct",
        email_address="victim@example.com",
        imap_host="imap.example.com",
        imap_port=993,
        auth_type="app_password",
        store=tmp_store,
    )
    assign_owner(db_session, victim_account.id, victim.id)
    group = create_group(db_session, "mygroup", owner.id)

    _login(client, "gowner", "pass")
    resp = client.post(
        f"/admin/groups/{group.id}/edit",
        data={"account_ids": [own_account.id, victim_account.id], "sso_sync": ""},
        follow_redirects=False,
    )
    assert resp.status_code in (303, 302)

    db_session.refresh(group)
    group_account_ids = {a.id for a in group.accounts}
    assert own_account.id in group_account_ids
    assert victim_account.id not in group_account_ids


def test_admin_can_add_any_account_to_group(client, db_session, tmp_store):
    admin = create_user(db_session, "admin", "pass", UserRole.admin, store_id=tmp_store.id)
    user = create_user(db_session, "user1", "pass", UserRole.user, store_id=tmp_store.id)
    account = create_account(
        db_session,
        name="UserAcct",
        email_address="user@example.com",
        imap_host="imap.example.com",
        imap_port=993,
        auth_type="app_password",
        store=tmp_store,
    )
    assign_owner(db_session, account.id, user.id)
    group = create_group(db_session, "admingroup", admin.id)

    _login(client, "admin", "pass")
    resp = client.post(
        f"/admin/groups/{group.id}/edit",
        data={"account_ids": [account.id], "member_ids": [user.id], "sso_sync": ""},
        follow_redirects=False,
    )
    assert resp.status_code in (303, 302)

    db_session.refresh(group)
    assert account.id in {a.id for a in group.accounts}
    assert user.id in {m.id for m in group.members}
