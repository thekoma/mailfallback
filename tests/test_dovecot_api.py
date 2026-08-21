# tests/test_dovecot_api.py
from datetime import UTC, datetime, timedelta

from mailfallback.models import Account, Recovery, RecoveryStatus, StagingArea, User, UserRole
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


def test_userdb_filters_suspended_accounts(client, db_session, default_store):
    """Suspended accounts must NOT appear in the namespaces list."""
    user = _create_user(db_session, default_store)
    live = _create_account(
        db_session,
        default_store,
        name="Live",
        email="alice@live.com",
        maildir_path="/data/mailboxes/uuid-live",
    )
    recovered = _create_account(
        db_session,
        default_store,
        name="Recovered alice (2026-05-10)",
        email="alice@live.com",
        maildir_path="/data/mailboxes/uuid-recovered",
    )
    recovered.suspended = True
    live.owners.append(user)
    recovered.owners.append(user)
    db_session.commit()

    resp = client.get("/api/internal/dovecot/userdb/alice", headers=HEADERS)
    assert resp.status_code == 200
    namespaces = resp.json()["namespaces"]

    # Only the live account is exposed; the suspended placeholder is hidden
    assert len(namespaces) == 1
    assert namespaces[0]["mail_path"] == "/data/mailboxes/uuid-live"


def test_userdb_includes_ready_recoveries_as_namespaces(client, db_session, default_store):
    """Each ready Recovery becomes an extra read-only namespace."""
    user = _create_user(db_session, default_store)
    acc = _create_account(
        db_session,
        default_store,
        name="Gmail",
        email="alice@gmail.com",
        maildir_path="/data/mailboxes/uuid-gmail",
    )
    acc.owners.append(user)

    ready = Recovery(
        account_id=acc.id,
        snapshot_id="abcd1234",
        restore_path="/data/mailboxes/.offsite-restore/uuid-gmail-20260510/data/mailboxes/uuid-gmail",
        status=RecoveryStatus.ready,
        restored_at=datetime.now(UTC),
    )
    pending = Recovery(
        account_id=acc.id,
        snapshot_id="ef567890",
        restore_path="/data/mailboxes/.offsite-restore/uuid-gmail-pending",
        status=RecoveryStatus.restoring,
        restored_at=datetime.now(UTC),
    )
    db_session.add_all([ready, pending])
    db_session.commit()

    resp = client.get("/api/internal/dovecot/userdb/alice", headers=HEADERS)
    assert resp.status_code == 200
    namespaces = resp.json()["namespaces"]

    # 1 account + 1 ready recovery (the restoring one is excluded)
    assert len(namespaces) == 2
    rec_ns = [n for n in namespaces if n["name"].startswith("rec_")]
    assert len(rec_ns) == 1
    assert rec_ns[0]["mail_path"] == ready.restore_path
    assert rec_ns[0]["inbox"] is False
    assert "Recovery" in rec_ns[0]["prefix"]


def test_userdb_dedupes_duplicate_recoveries(client, db_session, default_store):
    """Two Recovery rows for the same (account_id, snapshot_id) collapse to ONE namespace.

    mount_service.ensure_mounted has a documented race that can produce duplicate
    Recovery rows for the same snapshot. Dovecot rejects userdb responses with
    duplicate namespace prefixes, so the userdb endpoint MUST dedupe.
    """
    user = _create_user(db_session, default_store)
    acc = _create_account(
        db_session,
        default_store,
        name="Koma",
        email="koma@example.com",
        maildir_path="/data/mailboxes/uuid-koma",
    )
    acc.owners.append(user)

    now = datetime.now(UTC)
    older = Recovery(
        account_id=acc.id,
        snapshot_id="dup-snap",
        restore_path="/data/mailboxes/.offsite-restore/older",
        status=RecoveryStatus.ready,
        restored_at=now - timedelta(minutes=10),
    )
    newer = Recovery(
        account_id=acc.id,
        snapshot_id="dup-snap",
        restore_path="/data/mailboxes/.offsite-restore/newer",
        status=RecoveryStatus.ready,
        restored_at=now,
    )
    db_session.add_all([older, newer])
    db_session.commit()

    resp = client.get("/api/internal/dovecot/userdb/alice", headers=HEADERS)
    assert resp.status_code == 200
    namespaces = resp.json()["namespaces"]

    rec_ns = [n for n in namespaces if n["name"].startswith("rec_")]
    # Only ONE recovery namespace for the duplicate snapshot
    assert len(rec_ns) == 1
    # Newest wins (recoveries are ordered restored_at DESC)
    assert rec_ns[0]["name"] == f"rec_{newer.id}"
    assert rec_ns[0]["mail_path"] == newer.restore_path
    # All prefixes in the response must be unique (Dovecot's hard requirement)
    prefixes = [n["prefix"] for n in namespaces]
    assert len(prefixes) == len(set(prefixes))


def test_userdb_publishes_no_staging_namespace(client, db_session, default_store):
    """Staging is a mailbox in the root namespace, never a namespace of its own.

    A dedicated "Staging/" namespace made the mailbox unreachable and read-only:
    Dovecot's ACL `mailbox` filters match the namespace-INTERNAL name, so the
    mailbox behind a "Staging/" prefix was seen as "INBOX" and inherited the
    global read-only grant. The Maildir now lives at root-inbox/Staging, inside
    the mfb_root namespace the Lua userdb always creates, where the internal
    name is "Staging" and the `mailbox Staging` ACL filter applies.
    """
    user = _create_user(db_session, default_store)
    acc = _create_account(db_session, default_store)
    acc.owners.append(user)
    db_session.add(
        StagingArea(
            user_id=user.id,
            expires_at=datetime.now(UTC) + timedelta(hours=4),
        )
    )
    db_session.commit()

    resp = client.get("/api/internal/dovecot/userdb/alice", headers=HEADERS)
    assert resp.status_code == 200
    namespaces = resp.json()["namespaces"]

    assert [n for n in namespaces if n["name"].startswith("stg_")] == []
    assert all("Staging" not in (n["prefix"] or "") for n in namespaces)


def test_userdb_no_staging_namespace_without_area(client, db_session, default_store):
    """A user without a StagingArea row gets no stg_ namespace."""
    user = _create_user(db_session, default_store)
    acc = _create_account(db_session, default_store)
    acc.owners.append(user)
    db_session.commit()

    resp = client.get("/api/internal/dovecot/userdb/alice", headers=HEADERS)
    assert resp.status_code == 200
    namespaces = resp.json()["namespaces"]
    assert [n for n in namespaces if n["name"].startswith("stg_")] == []


def test_userdb_staging_only_user_gets_no_namespaces(client, db_session, default_store):
    """A user with only a staging area gets an empty namespace list.

    Login still works: the Lua userdb (mfb-lua-userdb.lua) UNCONDITIONALLY adds
    the mfb_root inbox namespace before consuming the API's list, and the
    staging Maildir now lives inside mfb_root's mail_path as the "Staging"
    mailbox, so it needs no namespace of its own.
    """
    user = _create_user(db_session, default_store)
    db_session.add(
        StagingArea(
            user_id=user.id,
            expires_at=datetime.now(UTC) + timedelta(hours=4),
        )
    )
    db_session.commit()

    resp = client.get("/api/internal/dovecot/userdb/alice", headers=HEADERS)
    assert resp.status_code == 200
    assert resp.json()["namespaces"] == []
