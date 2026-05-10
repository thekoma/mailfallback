# tests/test_recover_routes.py
"""Wave 3: /restore is a chooser, /restore/move is the legacy IMAP form,
/recover lists mailboxes with at least one off-site snapshot."""

from mailfallback.models import (
    Account,
    AccountBackup,
    BackendType,
    BackupDestination,
    UserRole,
)
from mailfallback.services.user_service import create_user


def _login(client, username, password):
    client.post("/api/auth/login", json={"username": username, "password": password})


def _make_destination(db_session, name="rustfs"):
    dest = BackupDestination(
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
    backup = AccountBackup(
        account_id=account.id,
        destination_id=destination.id,
        last_snapshot_count=snapshot_count,
    )
    db_session.add(backup)
    db_session.commit()
    db_session.refresh(account)
    return account, backup


def test_restore_renders_chooser(client, db_session, default_store):
    create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    _login(client, "admin", "pass")
    resp = client.get("/restore")
    assert resp.status_code == 200
    body = resp.text
    assert "Recover from a snapshot" in body
    assert "Move mail between mailboxes" in body
    assert "/recover" in body
    assert "/restore/move" in body


def test_restore_move_renders_legacy_form(client, db_session, default_store):
    create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    _login(client, "admin", "pass")
    resp = client.get("/restore/move")
    assert resp.status_code == 200
    # The legacy IMAP-to-IMAP form's hallmark: source/target account selectors
    assert "Source account" in resp.text
    assert "Destination account" in resp.text


def test_recover_empty_state(client, db_session, default_store):
    create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    _login(client, "admin", "pass")
    resp = client.get("/recover")
    assert resp.status_code == 200
    assert "No mailboxes have off-site snapshots yet" in resp.text


def test_recover_lists_mailboxes_with_snapshots(client, db_session, default_store):
    admin = create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    dest = _make_destination(db_session)
    _make_account_with_backup(
        db_session, default_store, admin, dest, name="WithSnap", snapshot_count=5
    )
    _make_account_with_backup(
        db_session, default_store, admin, dest, name="NoSnap", snapshot_count=0
    )

    _login(client, "admin", "pass")
    resp = client.get("/recover")
    assert resp.status_code == 200
    assert "WithSnap" in resp.text
    # Mailboxes with zero snapshots are filtered out.
    assert "NoSnap" not in resp.text


def test_recover_requires_login(client, db_session):
    resp = client.get("/recover", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/login"
