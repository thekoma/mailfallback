# tests/test_accounts.py
from mailfallback.models import UserRole
from mailfallback.services.user_service import create_user


def _login(client, username, password):
    client.post("/api/auth/login", json={"username": username, "password": password})


def test_admin_creates_account(client, db_session):
    admin = create_user(db_session, "admin", "pass", UserRole.admin)
    _login(client, "admin", "pass")
    resp = client.post("/api/accounts", json={
        "name": "Gmail",
        "imap_host": "imap.gmail.com",
        "imap_port": 993,
        "maildir_path": "/data/mailboxes/gmail",
    })
    assert resp.status_code == 200
    assert resp.json()["name"] == "Gmail"


def test_user_cannot_create_account(client, db_session):
    create_user(db_session, "user1", "pass", UserRole.user)
    _login(client, "user1", "pass")
    resp = client.post("/api/accounts", json={
        "name": "Gmail",
        "imap_host": "imap.gmail.com",
        "maildir_path": "/data/mailboxes/gmail",
    })
    assert resp.status_code == 403


def test_user_sees_only_own_accounts(client, db_session):
    from mailfallback.services.account_service import assign_owner, create_account
    admin = create_user(db_session, "admin", "pass", UserRole.admin)
    user = create_user(db_session, "user1", "pass", UserRole.user)
    a1 = create_account(db_session, "Gmail", "imap.gmail.com", 993, "app_password", "/data/gmail")
    a2 = create_account(db_session, "Work", "imap.work.com", 993, "app_password", "/data/work")
    assign_owner(db_session, a1.id, user.id)

    _login(client, "user1", "pass")
    resp = client.get("/api/accounts")
    assert resp.status_code == 200
    accounts = resp.json()
    assert len(accounts) == 1
    assert accounts[0]["name"] == "Gmail"


def test_admin_sees_all_accounts(client, db_session):
    from mailfallback.services.account_service import create_account
    create_user(db_session, "admin", "pass", UserRole.admin)
    create_account(db_session, "Gmail", "imap.gmail.com", 993, "app_password", "/data/gmail")
    create_account(db_session, "Work", "imap.work.com", 993, "app_password", "/data/work")

    _login(client, "admin", "pass")
    resp = client.get("/api/accounts")
    assert len(resp.json()) == 2


def test_assign_and_remove_owner(client, db_session):
    from mailfallback.services.account_service import create_account
    admin = create_user(db_session, "admin", "pass", UserRole.admin)
    user = create_user(db_session, "user1", "pass", UserRole.user)
    account = create_account(db_session, "Gmail", "imap.gmail.com", 993, "app_password", "/data/gmail")

    _login(client, "admin", "pass")
    resp = client.post(f"/api/accounts/{account.id}/owners", json={"user_id": user.id})
    assert resp.status_code == 200

    resp = client.get(f"/api/accounts/{account.id}")
    assert len(resp.json()["owners"]) == 1

    resp = client.delete(f"/api/accounts/{account.id}/owners/{user.id}")
    assert resp.status_code == 200


def test_user_updates_own_account(client, db_session):
    from mailfallback.services.account_service import assign_owner, create_account
    user = create_user(db_session, "user1", "pass", UserRole.user)
    account = create_account(db_session, "Gmail", "imap.gmail.com", 993, "app_password", "/data/gmail")
    assign_owner(db_session, account.id, user.id)

    _login(client, "user1", "pass")
    resp = client.patch(f"/api/accounts/{account.id}", json={"sync_schedule": "*/15 * * * *"})
    assert resp.status_code == 200
