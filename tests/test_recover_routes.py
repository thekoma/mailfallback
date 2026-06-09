# tests/test_recover_routes.py
"""/restore is now the unified Calendar of Safety. /recover 301-redirects to it.
/restore/move is still the IMAP-to-IMAP form."""

from unittest.mock import patch

from mailfallback.models import (
    Account,
    BackendType,
    BackupPolicy,
    Repository,
    UserRole,
)
from mailfallback.services.user_service import create_user


def _login(client, username, password):
    client.post("/api/auth/login", json={"username": username, "password": password})


def _make_destination(db_session, name="rustfs"):
    dest = Repository(
        name=name,
        backend_type=BackendType.s3,
        restic_password="encrypted",  # pragma: allowlist secret
    )
    db_session.add(dest)
    db_session.commit()
    db_session.refresh(dest)
    return dest


def _make_account_with_backup(
    db_session,
    default_store,
    owner,
    destination,
    name="Gmail",
    snapshot_count=0,
):
    account = Account(
        name=name,
        email_address=f"{name.lower()}@example.com",
        imap_host="imap.example.com",
        imap_port=993,
        maildir_path=f"/data/mailboxes/{name.lower()}",
        store_id=default_store.id,
    )
    db_session.add(account)
    db_session.flush()
    from mailfallback.services.account_service import assign_owner

    assign_owner(db_session, account.id, owner.id)
    backup = BackupPolicy(
        account_id=account.id,
        destination_id=destination.id,
        last_snapshot_count=snapshot_count,
    )
    db_session.add(backup)
    db_session.commit()
    db_session.refresh(account)
    return account, backup


def test_restore_renders_calendar_page(client, db_session, default_store):
    create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    _login(client, "admin", "pass")
    resp = client.get("/restore")
    assert resp.status_code == 200
    body = resp.text
    # Workspace landmarks (Calendar of Safety demoted into the status strip)
    assert "page-restore-workspace" in body
    # Alpine-driven workspace component (replaces former data-preset chips)
    assert 'x-data="restoreWorkspace()"' in body
    assert "/static/vendor/alpine.min.js" in body  # vendored Alpine script tag
    # The old chooser copy is gone
    assert "Recover from a snapshot" not in body


def test_restore_move_renders_legacy_form(client, db_session, default_store):
    create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    _login(client, "admin", "pass")
    resp = client.get("/restore/move")
    assert resp.status_code == 200
    # The IMAP-to-IMAP form's hallmark: source/target account selectors
    assert "Source account" in resp.text
    assert "Destination account" in resp.text


def test_restore_move_show_all_toggle_url_is_correct(client, db_session, default_store):
    """The toggle's onchange URL must stay on /restore/move. Previously it pointed
    at /restore which dumped the user back at the chooser."""
    create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    _login(client, "admin", "pass")
    resp = client.get("/restore/move")
    assert resp.status_code == 200
    assert "window.location.href='/restore/move'" in resp.text
    assert "window.location.href='/restore'" not in resp.text


def test_restore_unprotected_state(client, db_session, default_store):
    create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    _login(client, "admin", "pass")
    resp = client.get("/restore")
    assert resp.status_code == 200
    # No backup policies → unprotected line in the demoted status strip
    assert "No safety net configured yet" in resp.text


def test_restore_lists_mailboxes_with_snapshots(client, db_session, default_store):
    admin = create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    dest = _make_destination(db_session)
    _make_account_with_backup(
        db_session, default_store, admin, dest, name="WithSnap", snapshot_count=5
    )
    _make_account_with_backup(
        db_session, default_store, admin, dest, name="NoSnap", snapshot_count=0
    )

    _login(client, "admin", "pass")
    resp = client.get("/restore")
    assert resp.status_code == 200
    # Both protected mailboxes show in "What we're holding for you" (snapshot_count
    # only affects the dot strip — both rows render with their HTMX placeholder).
    assert "WithSnap" in resp.text
    assert "NoSnap" in resp.text


def test_recover_redirects_to_restore(client, db_session, default_store):
    create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    _login(client, "admin", "pass")
    resp = client.get("/recover", follow_redirects=False)
    assert resp.status_code == 301
    assert resp.headers["location"] == "/restore"


def test_restore_requires_login(client, db_session):
    resp = client.get("/restore", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/login"


def test_calendar_partial_degrades_when_restic_fails(client, db_session, default_store):
    """If restic times out / errors, the row partial renders a degraded message
    instead of bubbling up a 500."""
    admin = create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    dest = _make_destination(db_session)
    account, _ = _make_account_with_backup(
        db_session, default_store, admin, dest, name="Brokenrepo", snapshot_count=3
    )
    _login(client, "admin", "pass")

    with patch("mailfallback.services.restic_service.list_snapshots") as mock_ls:
        mock_ls.side_effect = RuntimeError("dial tcp: connection refused")
        resp = client.get(f"/restore/partials/calendar/{account.id}")

    assert resp.status_code == 200
    assert "couldn't reach repository" in resp.text


def test_calendar_partial_renders_dots_when_restic_returns_snapshots(
    client, db_session, default_store
):
    """Happy-path: restic returns a few snapshots, we render the 30-day strip."""
    from datetime import UTC, datetime, timedelta

    admin = create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    dest = _make_destination(db_session)
    account, _ = _make_account_with_backup(
        db_session, default_store, admin, dest, name="Healthy", snapshot_count=2
    )
    _login(client, "admin", "pass")

    now = datetime.now(UTC)
    fake_snaps = [
        {
            "short_id": "abc12345",
            "id": "abc12345" + "0" * 56,
            "time": (now - timedelta(days=2)).isoformat().replace("+00:00", "Z"),
        },
        {
            "short_id": "def67890",
            "id": "def67890" + "0" * 56,
            "time": (now - timedelta(hours=3)).isoformat().replace("+00:00", "Z"),
        },
    ]
    with patch("mailfallback.services.restic_service.list_snapshots") as mock_ls:
        mock_ls.return_value = fake_snaps
        resp = client.get(f"/restore/partials/calendar/{account.id}")

    assert resp.status_code == 200
    body = resp.text
    assert "snap-dot-filled" in body  # at least one filled dot
    assert "couldn't reach repository" not in body
    # Filled count badge: "2/30"
    assert "2/30" in body
