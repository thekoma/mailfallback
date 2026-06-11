"""API tests for the staging endpoints — GET/POST/DELETE /api/restore/staging*.

Login flow mirrors tests/test_restore_preview_api.py; Maildir + index fixtures
mirror tests/test_staging_service.py (real on-disk store: staging_dir() writes
under the USER's store, so its path must exist).
"""

import os
from email.message import EmailMessage

import pytest

from mailfallback.config import settings
from mailfallback.models import (
    Account,
    AuditLog,
    MailIndexMessage,
    MailStore,
    StagingArea,
    User,
    UserRole,
)
from mailfallback.security import hash_password
from mailfallback.services import index_service, staging_service


def _write_maildir_message(maildir_root, filename, msg):
    cur = os.path.join(maildir_root, "cur")
    os.makedirs(cur, exist_ok=True)
    with open(os.path.join(cur, filename), "wb") as f:
        f.write(msg.as_bytes())


def _msg(msgid, subject="hello"):
    msg = EmailMessage()
    msg["Message-Id"] = msgid
    msg["From"] = "Mittente <sender@example.com>"
    msg["To"] = "dest@example.com"
    msg["Subject"] = subject
    msg["Date"] = "Thu, 11 Jun 2026 10:00:00 +0200"
    msg.set_content("body text")
    return msg


@pytest.fixture
def real_store(db_session, tmp_path):
    """MailStore whose path actually exists on disk — staging_dir() writes under it."""
    store = MailStore(name="staging-store", path=str(tmp_path / "store"))
    db_session.add(store)
    db_session.commit()
    return store


def _mk_user(db_session, store, username="mario", role=UserRole.user):
    user = User(
        username=username,
        password_hash=hash_password("x"),
        role=role,
        enabled=True,
        store_id=store.id,
    )
    db_session.add(user)
    db_session.commit()
    return user


def _mk_indexed_account(db_session, store, tmp_path, owner=None, name="acc1", msgid="<m1@x>"):
    acc = Account(
        name=name,
        imap_host="h",
        maildir_path=str(tmp_path / f"mail-{name}"),
        store_id=store.id,
    )
    db_session.add(acc)
    db_session.flush()
    if owner is not None:
        acc.owners.append(owner)
    db_session.commit()
    _write_maildir_message(acc.maildir_path, "100.m1.host:2,S", _msg(msgid))
    index_service.upsert_message_set(db_session, acc.id)
    row = db_session.query(MailIndexMessage).filter_by(account_id=acc.id).one()
    return acc, row


def _staged_files(user):
    cur = os.path.join(staging_service.staging_dir(user), "cur")
    return sorted(os.listdir(cur)) if os.path.isdir(cur) else []


def _login(client, username):
    resp = client.post("/api/auth/login", json={"username": username, "password": "x"})
    assert resp.status_code == 200, resp.text


def _items(acc, row):
    return [{"account_id": acc.id, "message_id_hash": row.message_id_hash.hex()}]


def test_get_status_without_area_reports_empty_shape(client, db_session, real_store):
    """Also guards route ordering: /staging must not be swallowed by GET /{job_id}."""
    _mk_user(db_session, real_store)
    _login(client, "mario")

    resp = client.get("/api/restore/staging")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "exists": False,
        "count": 0,
        "bytes_used": 0,
        "expires_at": None,
        "max_bytes": settings.staging_max_bytes,
    }


def test_post_items_stages_live_message_and_audits(client, db_session, real_store, tmp_path):
    user = _mk_user(db_session, real_store)
    acc, row = _mk_indexed_account(db_session, real_store, tmp_path, owner=user)
    _login(client, "mario")

    resp = client.post("/api/restore/staging/items", json={"items": _items(acc, row)})

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"staged": 1, "skipped": 0, "failed": 0}
    assert len(_staged_files(user)) == 1
    entry = db_session.query(AuditLog).filter_by(action="staging.add").one()
    assert entry.username == "mario"
    assert entry.resource_type == "staging"
    assert entry.details["staged"] == 1
    # No escalation happened: the audit row must not claim include_all.
    assert "include_all" not in entry.details

    status = client.get("/api/restore/staging").json()
    assert status["exists"] is True
    assert status["count"] == 1


def test_post_items_quota_exceeded_returns_413_without_audit(
    client, db_session, real_store, tmp_path, monkeypatch
):
    user = _mk_user(db_session, real_store)
    acc, row = _mk_indexed_account(db_session, real_store, tmp_path, owner=user)
    monkeypatch.setattr(settings, "staging_max_bytes", 10)
    _login(client, "mario")

    resp = client.post("/api/restore/staging/items", json={"items": _items(acc, row)})

    assert resp.status_code == 413, resp.text
    assert "quota" in resp.json()["detail"].lower()
    assert db_session.query(AuditLog).filter_by(action="staging.add").count() == 0
    assert _staged_files(user) == []


def test_post_items_foreign_account_returns_403_without_audit(
    client, db_session, real_store, tmp_path
):
    _mk_user(db_session, real_store, username="luigi")
    acc, row = _mk_indexed_account(db_session, real_store, tmp_path, owner=None)
    _login(client, "luigi")

    resp = client.post("/api/restore/staging/items", json={"items": _items(acc, row)})

    assert resp.status_code == 403, resp.text
    assert "not accessible" in resp.json()["detail"]

    # include_all is ignored for non-admins — still rejected.
    resp = client.post(
        "/api/restore/staging/items",
        json={"items": _items(acc, row), "include_all": True},
    )

    assert resp.status_code == 403, resp.text
    assert db_session.query(AuditLog).filter_by(action="staging.add").count() == 0
    assert db_session.query(StagingArea).count() == 0


def test_post_items_admin_include_all_stages_foreign_and_audits(
    client, db_session, real_store, tmp_path
):
    admin = _mk_user(db_session, real_store, username="root", role=UserRole.admin)
    acc, row = _mk_indexed_account(db_session, real_store, tmp_path, owner=None)
    _login(client, "root")

    resp = client.post(
        "/api/restore/staging/items",
        json={"items": _items(acc, row), "include_all": True},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"staged": 1, "skipped": 0, "failed": 0}
    assert len(_staged_files(admin)) == 1
    entry = db_session.query(AuditLog).filter_by(action="staging.add").one()
    assert entry.username == "root"
    assert entry.details["staged"] == 1
    # Escalated add: the audit row is self-describing.
    assert entry.details["include_all"] is True


def test_post_items_malformed_returns_400(client, db_session, real_store):
    _mk_user(db_session, real_store)
    _login(client, "mario")

    missing_key = client.post("/api/restore/staging/items", json={"items": [{"account_id": "a"}]})
    bad_hex = client.post(
        "/api/restore/staging/items",
        json={"items": [{"account_id": "a", "message_id_hash": "zz-not-hex"}]},
    )

    assert missing_key.status_code == 400
    assert bad_hex.status_code == 400
    assert db_session.query(AuditLog).filter_by(action="staging.add").count() == 0


def test_delete_empties_area_and_audits(client, db_session, real_store, tmp_path):
    user = _mk_user(db_session, real_store)
    acc, row = _mk_indexed_account(db_session, real_store, tmp_path, owner=user)
    _login(client, "mario")
    add = client.post("/api/restore/staging/items", json={"items": _items(acc, row)})
    assert add.status_code == 200, add.text
    assert len(_staged_files(user)) == 1

    resp = client.delete("/api/restore/staging")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True}
    assert not os.path.isdir(staging_service.staging_dir(user))
    assert db_session.query(StagingArea).count() == 0
    entry = db_session.query(AuditLog).filter_by(action="staging.empty").one()
    assert entry.username == "mario"
    assert entry.resource_type == "staging"


def test_delete_without_area_is_ok(client, db_session, real_store):
    """Emptying an already-empty staging area is idempotent (double-click safe)."""
    _mk_user(db_session, real_store)
    _login(client, "mario")

    resp = client.delete("/api/restore/staging")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True}


def test_unauthenticated_get_returns_401(client):
    resp = client.get("/api/restore/staging")

    assert resp.status_code == 401


def test_action_labels_cover_staging_actions():
    from mailfallback.services.audit_service import get_action_label

    assert get_action_label("staging.add") != "staging.add"
    assert get_action_label("staging.empty") != "staging.empty"
