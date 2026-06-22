# tests/test_integration.py
import io
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from mailfallback.models import MailStore, UserRole
from mailfallback.services.account_service import assign_owner, create_account
from mailfallback.services.user_service import create_user


@pytest.fixture(autouse=True)
def _offline_worker(monkeypatch):
    """The worker's sampler thread opens sessions via sync_worker.SessionLocal
    and its initial-sync STATUS pass dials the upstream (non-fatal) — keep
    these flows OFFLINE-deterministic in the test suite."""
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from mailfallback.db import Base
    from mailfallback.services import sync_worker

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("ATTACH DATABASE ':memory:' AS mail_index")
        cursor.close()

    Base.metadata.create_all(engine)
    monkeypatch.setattr(sync_worker, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(
        "mailfallback.services.imap_check.connect_imap",
        MagicMock(side_effect=OSError("offline tests")),
    )


@pytest.fixture
def tmp_store(db_session):
    """Create a MailStore with a writable temp directory for integration tests."""
    tmp_dir = tempfile.mkdtemp(prefix="mfb_test_")
    store = MailStore(name="tmp-store", path=tmp_dir)
    db_session.add(store)
    db_session.commit()
    db_session.refresh(store)
    return store


def test_full_sync_flow(client, db_session, tmp_store):
    admin = create_user(db_session, "admin", "pass", UserRole.admin, store_id=tmp_store.id)
    account = create_account(
        db_session,
        name="Test Gmail",
        imap_host="imap.gmail.com",
        imap_port=993,
        auth_type="app_password",
        store=tmp_store,
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

    mock_proc = MagicMock()
    mock_proc.stdout = io.StringIO("synced\n")
    mock_proc.returncode = 0
    with patch("mailfallback.services.sync_worker.subprocess.Popen", return_value=mock_proc):
        from mailfallback.services.sync_worker import execute_sync_job

        execute_sync_job(db_session, job_id)

    resp = client.get(f"/api/sync/jobs/{job_id}")
    assert resp.json()["status"] == "completed"
    assert resp.json()["exit_code"] == 0

    resp = client.get(f"/api/accounts/{account.id}")
    assert resp.json()["sync_state"] == "idle"
    assert resp.json()["last_sync_at"] is not None


def test_full_sync_flow_failure(client, db_session, tmp_store):
    admin = create_user(db_session, "admin", "pass", UserRole.admin, store_id=tmp_store.id)
    account = create_account(
        db_session,
        name="Failing",
        imap_host="imap.fail.com",
        imap_port=993,
        auth_type="app_password",
        store=tmp_store,
    )
    assign_owner(db_session, account.id, admin.id)

    client.post("/api/auth/login", json={"username": "admin", "password": "pass"})
    resp = client.post(f"/api/sync/{account.id}")
    job_id = resp.json()["job_id"]

    mock_proc = MagicMock()
    mock_proc.stdout = io.StringIO("auth error\n")
    mock_proc.returncode = 1
    with patch("mailfallback.services.sync_worker.subprocess.Popen", return_value=mock_proc):
        from mailfallback.services.sync_worker import execute_sync_job

        execute_sync_job(db_session, job_id)

    resp = client.get(f"/api/sync/jobs/{job_id}")
    assert resp.json()["status"] == "failed"

    resp = client.get(f"/api/accounts/{account.id}")
    assert resp.json()["sync_state"] == "error"
    assert "auth error" in resp.json()["last_error"]
