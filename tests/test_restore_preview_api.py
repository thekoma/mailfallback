"""API tests for GET /api/restore/preview/{account_id}/{message_id_hash_hex}."""

import os
from email.message import EmailMessage

from mailfallback.models import Account, AuditLog, MailIndexMessage, User, UserRole
from mailfallback.security import hash_password
from mailfallback.services import index_service


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


def _mk_user(db_session, default_store, username, role=UserRole.user):
    u = User(
        username=username,
        password_hash=hash_password("x"),
        role=role,
        enabled=True,
        store_id=default_store.id,
    )
    db_session.add(u)
    db_session.commit()
    return u


def _mk_indexed_account(db_session, default_store, tmp_path, owner=None):
    acc = Account(
        name="acc1",
        imap_host="h",
        maildir_path=str(tmp_path / "mail"),
        store_id=default_store.id,
    )
    db_session.add(acc)
    db_session.flush()
    if owner is not None:
        acc.owners.append(owner)
    db_session.commit()
    _write_maildir_message(
        acc.maildir_path, "400.m1.host:2,S", _msg("<api1@x>", subject="Conferma ordine")
    )
    index_service.upsert_message_set(db_session, acc.id)
    row = db_session.query(MailIndexMessage).filter_by(account_id=acc.id).one()
    return acc, row


def _login(client, username):
    resp = client.post("/api/auth/login", json={"username": username, "password": "x"})
    assert resp.status_code == 200, resp.text


def test_owner_gets_preview(client, db_session, default_store, tmp_path):
    owner = _mk_user(db_session, default_store, "mario")
    acc, row = _mk_indexed_account(db_session, default_store, tmp_path, owner=owner)
    _login(client, "mario")

    resp = client.get(f"/api/restore/preview/{acc.id}/{row.message_id_hash.hex()}")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["subject"] == "Conferma ordine"
    assert body["source"] == "live"
    assert "body text" in body["body_snippet"]


def test_non_owner_gets_404(client, db_session, default_store, tmp_path):
    owner = _mk_user(db_session, default_store, "mario")
    _mk_user(db_session, default_store, "luigi")  # owns nothing
    acc, row = _mk_indexed_account(db_session, default_store, tmp_path, owner=owner)
    _login(client, "luigi")

    resp = client.get(f"/api/restore/preview/{acc.id}/{row.message_id_hash.hex()}")

    assert resp.status_code == 404


def test_admin_include_all_previews_foreign_account_and_audits(
    client, db_session, default_store, tmp_path
):
    """The audited escalation: include_all=true lets an admin preview a message
    in an account outside their accessible set — and writes an audit row."""
    owner = _mk_user(db_session, default_store, "mario")
    _mk_user(db_session, default_store, "root", role=UserRole.admin)
    acc, row = _mk_indexed_account(db_session, default_store, tmp_path, owner=owner)
    _login(client, "root")

    resp = client.get(f"/api/restore/preview/{acc.id}/{row.message_id_hash.hex()}?include_all=true")

    assert resp.status_code == 200, resp.text
    assert resp.json()["subject"] == "Conferma ordine"
    entry = db_session.query(AuditLog).filter_by(action="restore.preview").one()
    assert entry.username == "root"
    assert entry.resource_type == "restore"
    assert entry.resource_id == acc.id
    assert entry.details == {"message_id_hash": row.message_id_hash.hex()}


def test_admin_without_include_all_gets_404_on_foreign_account(
    client, db_session, default_store, tmp_path
):
    """Privacy default: the workspace preview gives admins NO implicit access —
    without the audited escalation a foreign account 404s, exactly like for a
    regular user."""
    owner = _mk_user(db_session, default_store, "mario")
    _mk_user(db_session, default_store, "root", role=UserRole.admin)
    acc, row = _mk_indexed_account(db_session, default_store, tmp_path, owner=owner)
    _login(client, "root")

    resp = client.get(f"/api/restore/preview/{acc.id}/{row.message_id_hash.hex()}")

    assert resp.status_code == 404
    assert db_session.query(AuditLog).filter_by(action="restore.preview").count() == 0


def test_non_admin_include_all_is_ignored(client, db_session, default_store, tmp_path):
    owner = _mk_user(db_session, default_store, "mario")
    _mk_user(db_session, default_store, "luigi")  # owns nothing
    acc, row = _mk_indexed_account(db_session, default_store, tmp_path, owner=owner)
    _login(client, "luigi")

    resp = client.get(f"/api/restore/preview/{acc.id}/{row.message_id_hash.hex()}?include_all=true")

    assert resp.status_code == 404
    assert db_session.query(AuditLog).filter_by(action="restore.preview").count() == 0


def test_admin_include_all_on_accessible_account_not_audited(
    client, db_session, default_store, tmp_path
):
    """No audit noise: an admin previewing mail they could access anyway
    (ownership/groups) is not logged, even with the toggle on."""
    admin = _mk_user(db_session, default_store, "root", role=UserRole.admin)
    acc, row = _mk_indexed_account(db_session, default_store, tmp_path, owner=admin)
    _login(client, "root")

    resp = client.get(f"/api/restore/preview/{acc.id}/{row.message_id_hash.hex()}?include_all=true")

    assert resp.status_code == 200, resp.text
    assert db_session.query(AuditLog).filter_by(action="restore.preview").count() == 0


def test_invalid_hash_hex_returns_400(client, db_session, default_store, tmp_path):
    owner = _mk_user(db_session, default_store, "mario")
    acc, _row = _mk_indexed_account(db_session, default_store, tmp_path, owner=owner)
    _login(client, "mario")

    resp = client.get(f"/api/restore/preview/{acc.id}/zzzz-not-hex")

    assert resp.status_code == 400


def test_unknown_hash_returns_404(client, db_session, default_store, tmp_path):
    owner = _mk_user(db_session, default_store, "mario")
    acc, _row = _mk_indexed_account(db_session, default_store, tmp_path, owner=owner)
    _login(client, "mario")

    resp = client.get(f"/api/restore/preview/{acc.id}/" + "00" * 20)

    assert resp.status_code == 404
