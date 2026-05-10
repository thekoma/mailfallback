# tests/test_chain_hero.py
"""Wave 4: dashboard chain hero — empty-state teaching + 4-stage status."""

from mailfallback.models import (
    Account,
    BackendType,
    BackupPolicy,
    BackupStatus,
    Repository,
    UserRole,
)
from mailfallback.services.user_service import create_user


def _login(client, username, password):
    client.post("/api/auth/login", json={"username": username, "password": password})


def test_dashboard_empty_renders_teaching_hero(client, db_session, default_store):
    create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    _login(client, "admin", "pass")
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text
    # Empty-state teaching content (case-sensitive — matches the literal copy in dashboard.html)
    assert "Welcome to MailFallBack" in body
    assert "Connect a mailbox" in body
    assert "local backup" in body
    assert "Repository" in body
    assert "chain-hero-empty" in body


def test_dashboard_populated_renders_chain_hero(client, db_session, default_store):
    admin = create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    account = Account(
        name="Gmail",
        email_address="me@gmail.com",
        imap_host="imap.gmail.com",
        imap_port=993,
        maildir_path="/data/mailboxes/gmail",
        store_id=default_store.id,
    )
    db_session.add(account)
    db_session.flush()
    from mailfallback.services.account_service import assign_owner

    assign_owner(db_session, account.id, admin.id)
    dest = Repository(
        name="rustfs",
        backend_type=BackendType.s3,
        restic_password="encrypted",  # pragma: allowlist secret
    )
    db_session.add(dest)
    db_session.flush()
    backup = BackupPolicy(
        account_id=account.id,
        destination_id=dest.id,
        last_snapshot_count=7,
        last_status=BackupStatus.completed,
    )
    db_session.add(backup)
    db_session.commit()

    _login(client, "admin", "pass")
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text
    # 4-stage chain hero
    assert "Mail safety chain" in body
    assert "1 mailbox connected" in body or "1 mailboxes connected" in body
    assert "1 configured" in body  # Repository stage
    assert "7 stored" in body  # Snapshot stage
    # Empty-state teaching is NOT shown
    assert "Welcome to MailFallBack" not in body


def test_dashboard_no_backup_yet_shows_empty_snapshot_stage(client, db_session, default_store):
    admin = create_user(db_session, "admin", "pass", UserRole.admin, store_id=default_store.id)
    account = Account(
        name="Gmail",
        email_address="me@gmail.com",
        imap_host="imap.gmail.com",
        imap_port=993,
        maildir_path="/data/mailboxes/gmail",
        store_id=default_store.id,
    )
    db_session.add(account)
    db_session.flush()
    from mailfallback.services.account_service import assign_owner

    assign_owner(db_session, account.id, admin.id)
    db_session.commit()
    _login(client, "admin", "pass")
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text
    assert "None configured" in body  # Repository stage with zero
    assert "None yet" in body  # Snapshot stage with zero
