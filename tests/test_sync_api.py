# tests/test_sync_api.py
from mailfallback.models import UserRole
from mailfallback.services.account_service import assign_owner, create_account
from mailfallback.services.user_service import create_user


def _login(client, username, password):
    client.post("/api/auth/login", json={"username": username, "password": password})


def test_trigger_sync_api(client, db_session):
    user = create_user(db_session, "user1", "pass", UserRole.user)
    account = create_account(db_session, "Gmail", "imap.gmail.com", 993, "app_password", "/data/g")
    assign_owner(db_session, account.id, user.id)

    _login(client, "user1", "pass")
    resp = client.post(f"/api/sync/{account.id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"

    resp2 = client.post(f"/api/sync/{account.id}")
    assert resp2.status_code == 409


def test_get_job_api(client, db_session):
    user = create_user(db_session, "user1", "pass", UserRole.user)
    account = create_account(db_session, "Gmail", "imap.gmail.com", 993, "app_password", "/data/g")
    assign_owner(db_session, account.id, user.id)

    _login(client, "user1", "pass")
    resp = client.post(f"/api/sync/{account.id}")
    job_id = resp.json()["job_id"]

    resp = client.get(f"/api/sync/jobs/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"


def test_list_jobs_api(client, db_session):
    user = create_user(db_session, "user1", "pass", UserRole.user)
    account = create_account(db_session, "Gmail", "imap.gmail.com", 993, "app_password", "/data/g")
    assign_owner(db_session, account.id, user.id)

    _login(client, "user1", "pass")
    client.post(f"/api/sync/{account.id}")

    resp = client.get(f"/api/sync/jobs?account_id={account.id}")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_trigger_sync_unauthorized(client, db_session):
    create_user(db_session, "user1", "pass", UserRole.user)
    account = create_account(db_session, "Gmail", "imap.gmail.com", 993, "app_password", "/data/g")

    _login(client, "user1", "pass")
    resp = client.post(f"/api/sync/{account.id}")
    assert resp.status_code == 404
