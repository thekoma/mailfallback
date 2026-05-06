from unittest.mock import patch

import pytest

from mailfallback.models import Account, JobStatus, RestoreJob, RestoreMode, User, UserRole


@pytest.fixture
def restore_api_fixtures(db_session, default_store):
    user = User(
        username="apiuser",
        password_hash="x",
        store_id=default_store.id,
        role=UserRole.admin,
    )
    db_session.add(user)
    db_session.flush()

    src = Account(
        name="source",
        email_address="src@example.com",
        imap_host="imap.src.com",
        imap_port=993,
        maildir_path="/data/mailboxes/api-src",
        store_id=default_store.id,
        credentials="encrypted",
    )
    tgt = Account(
        name="target",
        email_address="tgt@example.com",
        imap_host="imap.tgt.com",
        imap_port=993,
        maildir_path="/data/mailboxes/api-tgt",
        store_id=default_store.id,
        credentials="encrypted",
    )
    db_session.add_all([src, tgt])
    db_session.commit()
    db_session.refresh(user)
    db_session.refresh(src)
    db_session.refresh(tgt)
    return {"user": user, "source": src, "target": tgt}


def _login(client, db_session, username="apiuser"):
    """Bypass authentication by injecting user_id directly into the session."""
    from mailfallback.models import User

    user = db_session.query(User).filter(User.username == username).first()
    with patch("mailfallback.routers.auth.authenticate_user", return_value=user):
        client.post("/api/auth/login", json={"username": username, "password": "x"})


@patch("mailfallback.routers.restore.submit_restore_job")
def test_create_restore_job_api(mock_submit, client, db_session, restore_api_fixtures):
    f = restore_api_fixtures
    _login(client, db_session, "apiuser")
    resp = client.post(
        "/api/restore",
        json={
            "source_account_id": f["source"].id,
            "target_account_id": f["target"].id,
            "restore_mode": "full",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "pending"
    assert "job_id" in data
    mock_submit.assert_called_once()


def test_get_restore_job_api(client, db_session, restore_api_fixtures):
    f = restore_api_fixtures
    _login(client, db_session, "apiuser")
    job = RestoreJob(
        source_account_id=f["source"].id,
        target_account_id=f["target"].id,
        restore_mode=RestoreMode.full,
        requested_by=f["user"].id,
    )
    db_session.add(job)
    db_session.commit()

    resp = client.get(f"/api/restore/{job.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "pending"
    assert data["total_messages"] == 0


def test_cancel_restore_job_api(client, db_session, restore_api_fixtures):
    f = restore_api_fixtures
    _login(client, db_session, "apiuser")
    job = RestoreJob(
        source_account_id=f["source"].id,
        target_account_id=f["target"].id,
        restore_mode=RestoreMode.full,
        requested_by=f["user"].id,
        status=JobStatus.running,
    )
    db_session.add(job)
    db_session.commit()

    resp = client.post(f"/api/restore/{job.id}/cancel")
    assert resp.status_code == 200
