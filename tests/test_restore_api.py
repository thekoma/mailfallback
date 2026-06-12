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


@patch("mailfallback.routers.restore.submit_restore_job")
def test_create_restore_custom_folder_mapping_accepted(
    mock_submit, client, db_session, restore_api_fixtures
):
    """Any non-"original" folder_mapping is a custom destination root (the
    worker nests everything under it) — a clean path passes, stripped."""
    f = restore_api_fixtures
    _login(client, db_session, "apiuser")
    resp = client.post(
        "/api/restore",
        json={
            "source_account_id": f["source"].id,
            "target_account_id": f["target"].id,
            "restore_mode": "full",
            "folder_mapping": " Restored/2026-06-12 ",
        },
    )
    assert resp.status_code == 200, resp.text
    job = db_session.query(RestoreJob).one()
    assert job.folder_mapping == "Restored/2026-06-12"
    mock_submit.assert_called_once()


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "/lead", "trail/", "a//b", "a/../b", 'Rest"ored', "bad\x01name", "x" * 201],
)
def test_create_restore_garbage_folder_mapping_400(client, db_session, restore_api_fixtures, bad):
    """The workspace now feeds user-typed text into folder_mapping; the same
    hygiene as staging custom_folder applies (quoted IMAP atom + Maildir path)."""
    f = restore_api_fixtures
    _login(client, db_session, "apiuser")
    resp = client.post(
        "/api/restore",
        json={
            "source_account_id": f["source"].id,
            "target_account_id": f["target"].id,
            "restore_mode": "full",
            "folder_mapping": bad,
        },
    )
    assert resp.status_code == 400, resp.text
    assert "folder_mapping" in resp.json()["detail"]
    assert db_session.query(RestoreJob).count() == 0


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
