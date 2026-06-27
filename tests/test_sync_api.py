# tests/test_sync_api.py
from unittest.mock import MagicMock, patch

from mailfallback.models import UserRole
from mailfallback.services.account_service import assign_owner, create_account
from mailfallback.services.user_service import create_user


def _login(client, username, password):
    client.post("/api/auth/login", json={"username": username, "password": password})


def test_trigger_sync_api(client, db_session, default_store):
    user = create_user(db_session, "user1", "pass", UserRole.user, store_id=default_store.id)
    account = create_account(
        db_session, "Gmail", "imap.gmail.com", 993, "app_password", store=default_store
    )
    assign_owner(db_session, account.id, user.id)

    _login(client, "user1", "pass")
    resp = client.post(f"/api/sync/{account.id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"

    resp2 = client.post(f"/api/sync/{account.id}")
    assert resp2.status_code == 409


def test_sync_all_is_audited(client, db_session, default_store):
    """Bulk 'Sync all' must record an audit-log entry, like single-account
    sync does (2026-06-27: the action was silently unaudited)."""
    from mailfallback.models import AuditLog

    user = create_user(db_session, "user1", "pass", UserRole.user, store_id=default_store.id)
    account = create_account(
        db_session, "Gmail", "imap.gmail.com", 993, "app_password", store=default_store
    )
    assign_owner(db_session, account.id, user.id)

    _login(client, "user1", "pass")
    resp = client.post("/api/sync/all")
    assert resp.status_code == 200
    assert resp.json()["triggered"] >= 1

    rows = db_session.query(AuditLog).filter(AuditLog.action == "account.sync_all").all()
    assert len(rows) == 1
    assert rows[0].user_id == user.id


def test_get_job_api(client, db_session, default_store):
    user = create_user(db_session, "user1", "pass", UserRole.user, store_id=default_store.id)
    account = create_account(
        db_session, "Gmail", "imap.gmail.com", 993, "app_password", store=default_store
    )
    assign_owner(db_session, account.id, user.id)

    _login(client, "user1", "pass")
    resp = client.post(f"/api/sync/{account.id}")
    job_id = resp.json()["job_id"]

    resp = client.get(f"/api/sync/jobs/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"


def test_list_jobs_api(client, db_session, default_store):
    user = create_user(db_session, "user1", "pass", UserRole.user, store_id=default_store.id)
    account = create_account(
        db_session, "Gmail", "imap.gmail.com", 993, "app_password", store=default_store
    )
    assign_owner(db_session, account.id, user.id)

    _login(client, "user1", "pass")
    client.post(f"/api/sync/{account.id}")

    resp = client.get(f"/api/sync/jobs?account_id={account.id}")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_trigger_sync_unauthorized(client, db_session, default_store):
    create_user(db_session, "user1", "pass", UserRole.user, store_id=default_store.id)
    account = create_account(
        db_session, "Gmail", "imap.gmail.com", 993, "app_password", store=default_store
    )

    _login(client, "user1", "pass")
    resp = client.post(f"/api/sync/{account.id}")
    assert resp.status_code == 404


def _mock_imap_conn(capabilities=None, login_ok=True, login_error="bad credentials"):
    conn = MagicMock()
    conn.welcome = b"* OK IMAP server ready"
    caps = capabilities or ["IMAP4rev1", "AUTH=PLAIN", "AUTH=LOGIN"]
    conn.capability.return_value = ("OK", [" ".join(caps).encode()])
    if login_ok:
        conn.login.return_value = ("OK", [b"LOGIN completed"])
    else:
        import imaplib

        conn.login.side_effect = imaplib.IMAP4.error(login_error)
    return conn


def test_test_connection_basic(client, db_session, default_store):
    create_user(db_session, "user1", "pass", UserRole.user, store_id=default_store.id)
    _login(client, "user1", "pass")

    conn = _mock_imap_conn()
    with patch("mailfallback.services.imap_check.imaplib.IMAP4_SSL", return_value=conn):
        resp = client.post(
            "/api/sync/test-connection",
            json={"imap_host": "imap.example.com", "imap_port": 993, "tls_type": "IMAPS"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["auth_mechs"] == ["LOGIN", "PLAIN"]
    assert data["login_ok"] is None


def test_test_connection_with_login_success(client, db_session, default_store):
    create_user(db_session, "user1", "pass", UserRole.user, store_id=default_store.id)
    _login(client, "user1", "pass")

    conn = _mock_imap_conn(login_ok=True)
    with patch("mailfallback.services.imap_check.imaplib.IMAP4_SSL", return_value=conn):
        resp = client.post(
            "/api/sync/test-connection",
            json={
                "imap_host": "imap.example.com",
                "imap_port": 993,
                "tls_type": "IMAPS",
                "username": "user@example.com",
                "password": "secret",
            },
        )
    data = resp.json()
    assert data["ok"] is True
    assert data["login_ok"] is True
    assert data["login_message"] == "Login successful"
    conn.login.assert_called_once_with("user@example.com", "secret")


def test_test_connection_with_login_failure(client, db_session, default_store):
    create_user(db_session, "user1", "pass", UserRole.user, store_id=default_store.id)
    _login(client, "user1", "pass")

    conn = _mock_imap_conn(login_ok=False, login_error="AUTHENTICATIONFAILED")
    with patch("mailfallback.services.imap_check.imaplib.IMAP4_SSL", return_value=conn):
        resp = client.post(
            "/api/sync/test-connection",
            json={
                "imap_host": "imap.example.com",
                "imap_port": 993,
                "tls_type": "IMAPS",
                "username": "user@example.com",
                "password": "wrong",
            },
        )
    data = resp.json()
    assert data["ok"] is True
    assert data["login_ok"] is False
    assert "AUTHENTICATIONFAILED" in data["login_message"]


def test_test_connection_returns_auth_capabilities(client, db_session, default_store):
    create_user(db_session, "user1", "pass", UserRole.user, store_id=default_store.id)
    _login(client, "user1", "pass")

    conn = _mock_imap_conn(capabilities=["IMAP4rev1", "AUTH=PLAIN", "AUTH=LOGIN", "AUTH=XOAUTH2"])
    with patch("mailfallback.services.imap_check.imaplib.IMAP4_SSL", return_value=conn):
        resp = client.post(
            "/api/sync/test-connection",
            json={"imap_host": "imap.example.com", "imap_port": 993, "tls_type": "IMAPS"},
        )
    data = resp.json()
    assert data["auth_mechs"] == ["LOGIN", "PLAIN", "XOAUTH2"]


def test_test_connection_starttls(client, db_session, default_store):
    create_user(db_session, "user1", "pass", UserRole.user, store_id=default_store.id)
    _login(client, "user1", "pass")

    conn = _mock_imap_conn()
    with patch("mailfallback.services.imap_check.imaplib.IMAP4", return_value=conn):
        resp = client.post(
            "/api/sync/test-connection",
            json={"imap_host": "imap.example.com", "imap_port": 143, "tls_type": "STARTTLS"},
        )
    assert resp.json()["ok"] is True
    conn.starttls.assert_called_once()


def test_test_connection_failure(client, db_session, default_store):
    create_user(db_session, "user1", "pass", UserRole.user, store_id=default_store.id)
    _login(client, "user1", "pass")

    with patch(
        "mailfallback.services.imap_check.imaplib.IMAP4_SSL",
        side_effect=OSError("Connection refused"),
    ):
        resp = client.post(
            "/api/sync/test-connection",
            json={"imap_host": "bad.host", "imap_port": 993, "tls_type": "IMAPS"},
        )
    data = resp.json()
    assert data["ok"] is False
    assert "Connection refused" in data["message"]


# ---------------------------------------------------------------------------
# Manual override of self-recovering pauses (sync-budget Task 7, spec §6)
# ---------------------------------------------------------------------------

BUDGET_WARNING = (
    "Manual sync may exhaust the provider's daily quota; "
    "other IMAP clients for this mailbox could be affected."
)


def _paused_account(db_session, default_store, reason):
    from datetime import UTC, datetime, timedelta

    account = create_account(
        db_session, "Paused", "imap.gmail.com", 993, "app_password", store=default_store
    )
    account.sync_paused_until = datetime.now(UTC) + timedelta(hours=6)
    account.pause_reason = reason
    db_session.commit()
    return account


def test_manual_sync_on_budget_pause_warns_and_clears(client, db_session, default_store):
    """Manual sync overrides a budget pause: columns clear, the job starts,
    and the response carries the EXACT quota warning copy."""
    user = create_user(db_session, "user1", "pass", UserRole.user, store_id=default_store.id)
    account = _paused_account(db_session, default_store, "budget")
    assign_owner(db_session, account.id, user.id)

    _login(client, "user1", "pass")
    resp = client.post(f"/api/sync/{account.id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "pending"
    assert body["warning"] == BUDGET_WARNING
    db_session.refresh(account)
    assert account.sync_paused_until is None
    assert account.pause_reason is None


def test_manual_sync_on_throttle_pause_clears_without_warning(client, db_session, default_store):
    user = create_user(db_session, "user1", "pass", UserRole.user, store_id=default_store.id)
    account = _paused_account(db_session, default_store, "throttle")
    assign_owner(db_session, account.id, user.id)

    _login(client, "user1", "pass")
    resp = client.post(f"/api/sync/{account.id}")

    assert resp.status_code == 200
    assert "warning" not in resp.json()
    db_session.refresh(account)
    assert account.sync_paused_until is None
    assert account.pause_reason is None


def test_manual_sync_unpaused_has_no_warning(client, db_session, default_store):
    user = create_user(db_session, "user1", "pass", UserRole.user, store_id=default_store.id)
    account = create_account(
        db_session, "Plain", "imap.gmail.com", 993, "app_password", store=default_store
    )
    assign_owner(db_session, account.id, user.id)

    _login(client, "user1", "pass")
    resp = client.post(f"/api/sync/{account.id}")

    assert resp.status_code == 200
    assert "warning" not in resp.json()
