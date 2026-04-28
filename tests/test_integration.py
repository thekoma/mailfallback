# tests/test_integration.py
from unittest.mock import patch

from mailfallback.models import UserRole
from mailfallback.services.account_service import assign_owner, create_account
from mailfallback.services.user_service import create_user


def test_full_sync_flow(client, db_session):
    admin = create_user(db_session, "admin", "pass", UserRole.admin)
    account = create_account(
        db_session,
        name="Test Gmail",
        imap_host="imap.gmail.com",
        imap_port=993,
        auth_type="app_password",
        maildir_path="/tmp/test_maildir",
        credentials="test-app-password",
    )
    assign_owner(db_session, account.id, admin.id)

    client.post("/api/auth/login", json={"username": "admin", "password": "pass"})

    resp = client.get("/api/accounts")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    resp = client.post(f"/api/sync/{account.id}")
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]

    resp = client.get(f"/api/sync/jobs/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"

    mock_result = type("Result", (), {"returncode": 0, "stdout": "synced", "stderr": ""})()
    with patch("mailfallback.services.sync_worker.subprocess.run", return_value=mock_result):
        from mailfallback.services.sync_worker import execute_sync_job
        execute_sync_job(db_session, job_id)

    resp = client.get(f"/api/sync/jobs/{job_id}")
    assert resp.json()["status"] == "completed"
    assert resp.json()["exit_code"] == 0

    resp = client.get(f"/api/accounts/{account.id}")
    assert resp.json()["sync_state"] == "idle"
    assert resp.json()["last_sync_at"] is not None


def test_full_sync_flow_failure(client, db_session):
    admin = create_user(db_session, "admin", "pass", UserRole.admin)
    account = create_account(
        db_session,
        name="Failing",
        imap_host="imap.fail.com",
        imap_port=993,
        auth_type="app_password",
        maildir_path="/tmp/test_fail",
    )
    assign_owner(db_session, account.id, admin.id)

    client.post("/api/auth/login", json={"username": "admin", "password": "pass"})
    resp = client.post(f"/api/sync/{account.id}")
    job_id = resp.json()["job_id"]

    mock_result = type("Result", (), {"returncode": 1, "stdout": "", "stderr": "auth error"})()
    with patch("mailfallback.services.sync_worker.subprocess.run", return_value=mock_result):
        from mailfallback.services.sync_worker import execute_sync_job
        execute_sync_job(db_session, job_id)

    resp = client.get(f"/api/sync/jobs/{job_id}")
    assert resp.json()["status"] == "failed"

    resp = client.get(f"/api/accounts/{account.id}")
    assert resp.json()["sync_state"] == "error"
    assert "auth error" in resp.json()["last_error"]
