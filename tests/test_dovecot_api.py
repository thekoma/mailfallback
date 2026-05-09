# tests/test_dovecot_api.py
from datetime import UTC, datetime, timedelta

from mailfallback.models import Account, User, UserRole
from mailfallback.security import hash_password

API_KEY = "test-key"
HEADERS = {"X-API-Key": API_KEY}


def _create_user(db_session, default_store, username="alice", enabled=True, migrating=False):
    user = User(
        username=username,
        password_hash=hash_password("password"),
        role=UserRole.user,
        store_id=default_store.id,
        enabled=enabled,
        migrating=migrating,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _create_account(
    db_session,
    default_store,
    name="Work",
    email="alice@work.com",
    maildir_path=None,
    created_at=None,
):
    account = Account(
        name=name,
        email_address=email,
        imap_host="imap.work.com",
        imap_port=993,
        auth_type="app_password",
        maildir_path=maildir_path or f"/data/mailboxes/uuid-{name.lower()}",
        store_id=default_store.id,
    )
    if created_at:
        account.created_at = created_at
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


def test_userdb_returns_namespaces(client, db_session, default_store):
    """User with 2 accounts returns correct namespace structure."""
    user = _create_user(db_session, default_store)
    now = datetime.now(UTC)
    acc1 = _create_account(
        db_session,
        default_store,
        name="Work",
        email="alice@work.com",
        maildir_path="/data/mailboxes/uuid-1",
        created_at=now - timedelta(hours=1),
    )
    acc2 = _create_account(
        db_session,
        default_store,
        name="Personal",
        email="me@gmail.com",
        maildir_path="/data/mailboxes/uuid-2",
        created_at=now,
    )
    acc1.owners.append(user)
    acc2.owners.append(user)
    db_session.commit()

    resp = client.get("/api/internal/dovecot/userdb/alice", headers=HEADERS)
    assert resp.status_code == 200
    data = resp.json()

    assert data["uid"] == 1000
    assert data["gid"] == 1000
    assert data["home"] == "/data/mailboxes/.dovecot-home/alice"

    ns = data["namespaces"]
    assert len(ns) == 2

    # First account (earliest created_at) is inbox
    assert ns[0]["name"] == f"acc_{acc1.id}"
    assert ns[0]["prefix"] == f"Work (alice@work.com) [{acc1.id[-4:]}]/"
    assert ns[0]["mail_driver"] == "maildir"
    assert ns[0]["mail_path"] == "/data/mailboxes/uuid-1"
    assert "mailbox_list_layout" not in ns[0]
    assert ns[0]["inbox"] is True

    # Second account gets prefix
    assert ns[1]["name"] == f"acc_{acc2.id}"
    assert ns[1]["prefix"] == f"Personal (me@gmail.com) [{acc2.id[-4:]}]/"
    assert ns[1]["mail_driver"] == "maildir"
    assert ns[1]["mail_path"] == "/data/mailboxes/uuid-2"
    assert "mailbox_list_layout" not in ns[1]
    assert ns[1]["inbox"] is False


def test_userdb_no_accounts(client, db_session, default_store):
    """User with no accounts returns empty namespaces but still has home."""
    _create_user(db_session, default_store)

    resp = client.get("/api/internal/dovecot/userdb/alice", headers=HEADERS)
    assert resp.status_code == 200
    data = resp.json()

    assert data["uid"] == 1000
    assert data["gid"] == 1000
    assert data["home"] == "/data/mailboxes/.dovecot-home/alice"
    assert data["namespaces"] == []


def test_userdb_unknown_user(client, db_session):
    """Unknown username returns 404."""
    resp = client.get("/api/internal/dovecot/userdb/nobody", headers=HEADERS)
    assert resp.status_code == 404


def test_userdb_disabled_user(client, db_session, default_store):
    """Disabled user returns 404."""
    _create_user(db_session, default_store, enabled=False)

    resp = client.get("/api/internal/dovecot/userdb/alice", headers=HEADERS)
    assert resp.status_code == 404


def test_userdb_migrating_user(client, db_session, default_store):
    """Migrating user returns 404 (uniform response to avoid state leakage)."""
    _create_user(db_session, default_store, migrating=True)

    resp = client.get("/api/internal/dovecot/userdb/alice", headers=HEADERS)
    assert resp.status_code == 404


def test_userdb_shared_account(client, db_session, default_store):
    """Two users sharing same account get same mail_path."""
    alice = _create_user(db_session, default_store, username="alice")
    bob = _create_user(db_session, default_store, username="bob")

    shared_account = _create_account(
        db_session,
        default_store,
        name="Shared",
        email="shared@work.com",
        maildir_path="/data/mailboxes/uuid-shared",
    )
    shared_account.owners.append(alice)
    shared_account.owners.append(bob)
    db_session.commit()

    resp_alice = client.get("/api/internal/dovecot/userdb/alice", headers=HEADERS)
    resp_bob = client.get("/api/internal/dovecot/userdb/bob", headers=HEADERS)

    assert resp_alice.status_code == 200
    assert resp_bob.status_code == 200

    alice_path = resp_alice.json()["namespaces"][0]["mail_path"]
    bob_path = resp_bob.json()["namespaces"][0]["mail_path"]
    assert alice_path == bob_path == "/data/mailboxes/uuid-shared"


def test_userdb_requires_api_key(client, db_session):
    """Request without X-API-Key header returns 401."""
    resp = client.get("/api/internal/dovecot/userdb/alice")
    assert resp.status_code == 401


def test_userdb_wrong_api_key(client, db_session):
    """Request with wrong X-API-Key returns 401."""
    resp = client.get("/api/internal/dovecot/userdb/alice", headers={"X-API-Key": "wrong"})
    assert resp.status_code == 401
