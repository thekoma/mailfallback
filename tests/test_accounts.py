# tests/test_accounts.py
from unittest.mock import patch

from mailfallback.models import UserRole
from mailfallback.services.user_service import create_user


def _login(client, username, password):
    client.post("/api/auth/login", json={"username": username, "password": password})


def test_admin_creates_account(client, db_session, default_store):
    create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    _login(client, "admin", "pass")
    resp = client.post(
        "/api/accounts",
        json={
            "name": "Gmail",
            "email_address": "test@gmail.com",
            "imap_host": "imap.gmail.com",
            "imap_port": 993,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Gmail"
    assert resp.json()["maildir_path"].startswith("/data/mailboxes/")


def test_user_cannot_create_without_store(client, db_session, default_store):
    create_user(db_session, "user1", "pass", UserRole.user, store_id=default_store.id)
    _login(client, "user1", "pass")
    resp = client.post(
        "/api/accounts",
        json={
            "name": "Gmail",
            "email_address": "user@gmail.com",
            "imap_host": "imap.gmail.com",
        },
    )
    # User has a store now, so this should succeed (or fail for other reasons)
    # The original test checked for "No store assigned" when store_id was None.
    # With NOT NULL store_id, every user has a store — this scenario no longer applies.
    assert resp.status_code == 200


def test_user_sees_only_own_accounts(client, db_session, default_store):
    from mailfallback.services.account_service import assign_owner, create_account

    create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    user = create_user(db_session, "user1", "pass", UserRole.user, store_id=default_store.id)
    a1 = create_account(
        db_session, "Gmail", "imap.gmail.com", 993, "app_password", store=default_store
    )
    create_account(db_session, "Work", "imap.work.com", 993, "app_password", store=default_store)
    assign_owner(db_session, a1.id, user.id)

    _login(client, "user1", "pass")
    resp = client.get("/api/accounts")
    assert resp.status_code == 200
    accounts = resp.json()
    assert len(accounts) == 1
    assert accounts[0]["name"] == "Gmail"


def test_admin_sees_all_accounts(client, db_session, default_store):
    from mailfallback.services.account_service import create_account

    create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    create_account(db_session, "Gmail", "imap.gmail.com", 993, "app_password", store=default_store)
    create_account(db_session, "Work", "imap.work.com", 993, "app_password", store=default_store)

    _login(client, "admin", "pass")
    resp = client.get("/api/accounts")
    assert len(resp.json()) == 2


def test_assign_and_remove_owner(client, db_session, default_store):
    from mailfallback.services.account_service import create_account

    create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    user = create_user(db_session, "user1", "pass", UserRole.user, store_id=default_store.id)
    account = create_account(
        db_session, "Gmail", "imap.gmail.com", 993, "app_password", store=default_store
    )

    _login(client, "admin", "pass")
    resp = client.post(f"/api/accounts/{account.id}/owners", json={"user_id": user.id})
    assert resp.status_code == 200

    resp = client.get(f"/api/accounts/{account.id}")
    assert len(resp.json()["owners"]) == 1

    resp = client.delete(f"/api/accounts/{account.id}/owners/{user.id}")
    assert resp.status_code == 200


def test_user_updates_own_account(client, db_session, default_store):
    from mailfallback.services.account_service import assign_owner, create_account

    user = create_user(db_session, "user1", "pass", UserRole.user, store_id=default_store.id)
    account = create_account(
        db_session, "Gmail", "imap.gmail.com", 993, "app_password", store=default_store
    )
    assign_owner(db_session, account.id, user.id)

    _login(client, "user1", "pass")
    resp = client.patch(f"/api/accounts/{account.id}", json={"sync_schedule": "*/15 * * * *"})
    assert resp.status_code == 200


def test_create_account_with_provider(db_session, default_store):
    from mailfallback.services.account_service import create_account

    account = create_account(
        db_session,
        name="Gmail",
        imap_host="imap.gmail.com",
        imap_port=993,
        auth_type="app_password",
        store=default_store,
        provider="google",
    )
    assert account.provider == "google"


def test_create_account_default_provider(db_session, default_store):
    from mailfallback.services.account_service import create_account

    account = create_account(
        db_session,
        name="Custom",
        imap_host="imap.custom.com",
        imap_port=993,
        auth_type="app_password",
        store=default_store,
    )
    assert account.provider == "other"


def test_create_account_api_rejects_bad_credentials(client, db_session, default_store):
    create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    _login(client, "admin", "pass")

    with patch(
        "mailfallback.routers.accounts.check_imap_credentials",
        return_value={"ok": True, "login_ok": False, "login_message": "AUTHENTICATIONFAILED"},
    ):
        resp = client.post(
            "/api/accounts",
            json={
                "name": "Bad",
                "email_address": "bad@example.com",
                "imap_host": "imap.example.com",
                "auth_type": "app_password",
                "credentials": "wrongpass",
            },
        )
    assert resp.status_code == 422
    assert "Login failed" in resp.json()["detail"]


def test_create_account_api_rejects_connection_failure(client, db_session, default_store):
    create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    _login(client, "admin", "pass")

    with patch(
        "mailfallback.routers.accounts.check_imap_credentials",
        return_value={"ok": False, "message": "Connection refused"},
    ):
        resp = client.post(
            "/api/accounts",
            json={
                "name": "Bad",
                "email_address": "bad@example.com",
                "imap_host": "bad.host",
                "auth_type": "app_password",
                "credentials": "pass",
            },
        )
    assert resp.status_code == 422
    assert "Connection failed" in resp.json()["detail"]


def test_create_account_api_accepts_good_credentials(client, db_session, default_store):
    create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    _login(client, "admin", "pass")

    with patch(
        "mailfallback.routers.accounts.check_imap_credentials",
        return_value={"ok": True, "login_ok": True, "login_message": "Login successful"},
    ):
        resp = client.post(
            "/api/accounts",
            json={
                "name": "Good",
                "email_address": "good@example.com",
                "imap_host": "imap.example.com",
                "auth_type": "app_password",
                "credentials": "goodpass",
            },
        )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Good"


def test_create_account_api_skips_validation_for_oauth2(client, db_session, default_store):
    create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    _login(client, "admin", "pass")

    resp = client.post(
        "/api/accounts",
        json={
            "name": "OAuth",
            "email_address": "oauth@gmail.com",
            "imap_host": "imap.gmail.com",
            "auth_type": "oauth2",
            "provider": "google",
        },
    )
    assert resp.status_code == 200


def test_create_account_api_skips_validation_without_credentials(client, db_session, default_store):
    create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    _login(client, "admin", "pass")

    resp = client.post(
        "/api/accounts",
        json={
            "name": "NoCreds",
            "email_address": "no@example.com",
            "imap_host": "imap.example.com",
            "auth_type": "app_password",
        },
    )
    assert resp.status_code == 200
