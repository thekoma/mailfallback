"""API tests for attachment search + download.

POST /api/restore/attachments/search and
GET /api/restore/attachments/{account_id}/{hash}/{part_index}/download.

Login/ownership flow mirrors tests/test_restore_preview_api.py; real Maildir
messages with attachments mirror tests/test_index_attachments.py; the snapshot
mocking pattern comes from tests/test_preview_service.py.
"""

import os
from datetime import UTC, datetime
from email.message import EmailMessage

from mailfallback.config import settings
from mailfallback.models import (
    Account,
    AuditLog,
    BackupPolicy,
    MailIndexAttachment,
    MailIndexMessage,
    Repository,
    SnapshotMessage,
    User,
    UserRole,
)
from mailfallback.security import hash_password
from mailfallback.services import index_service, preview_service, staging_service

PDF_BYTES = b"%PDF-1.4 finta fattura con numero 0042"
SEARCH_URL = "/api/restore/attachments/search"


def _write_maildir_message(maildir_root, filename, msg):
    cur = os.path.join(maildir_root, "cur")
    os.makedirs(cur, exist_ok=True)
    with open(os.path.join(cur, filename), "wb") as f:
        f.write(msg.as_bytes())


def _msg(msgid, subject="hello", attachments=()):
    msg = EmailMessage()
    msg["Message-Id"] = msgid
    msg["From"] = "Mittente <sender@example.com>"
    msg["To"] = "dest@example.com"
    msg["Subject"] = subject
    msg["Date"] = "Thu, 11 Jun 2026 10:00:00 +0200"
    msg.set_content("body text")
    for name, payload in attachments:
        msg.add_attachment(payload, maintype="application", subtype="pdf", filename=name)
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


def _mk_account_with_attachment(
    db_session,
    default_store,
    tmp_path,
    owner=None,
    name="acc1",
    msgid="<att1@x>",
    filename="fattura-novità.pdf",
    payload=PDF_BYTES,
):
    acc = Account(
        name=name,
        imap_host="h",
        maildir_path=str(tmp_path / f"mail-{name}"),
        store_id=default_store.id,
    )
    db_session.add(acc)
    db_session.flush()
    if owner is not None:
        acc.owners.append(owner)
    db_session.commit()
    _write_maildir_message(
        acc.maildir_path,
        "500.m1.host:2,S",
        _msg(msgid, subject="Con allegato", attachments=[(filename, payload)]),
    )
    index_service.upsert_message_set(db_session, acc.id)
    row = db_session.query(MailIndexMessage).filter_by(account_id=acc.id).one()
    att = db_session.query(MailIndexAttachment).filter_by(account_id=acc.id).one()
    return acc, row, att


def _login(client, username):
    resp = client.post("/api/auth/login", json={"username": username, "password": "x"})
    assert resp.status_code == 200, resp.text


def _download_url(acc, row, part_index):
    return f"/api/restore/attachments/{acc.id}/{row.message_id_hash.hex()}/{part_index}/download"


# ---------------------------------------------------------------------------
# POST /api/restore/attachments/search
# ---------------------------------------------------------------------------


def test_search_owner_sees_own_hits_only(client, db_session, default_store, tmp_path):
    owner = _mk_user(db_session, default_store, "mario")
    acc, row, att = _mk_account_with_attachment(db_session, default_store, tmp_path, owner=owner)
    # Same filename in a foreign account — must stay invisible to mario.
    _mk_account_with_attachment(
        db_session, default_store, tmp_path, owner=None, name="foreign", msgid="<att2@x>"
    )
    _login(client, "mario")

    resp = client.post(SEARCH_URL, json={"query": "fattura"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert body["content_search_available"] is False  # tika disabled in tests
    hit = body["results"][0]
    assert hit["account_id"] == acc.id
    assert hit["message_id_hash"] == row.message_id_hash.hex()
    assert hit["part_index"] == att.part_index
    assert hit["filename"] == "fattura-novità.pdf"
    assert hit["ext"] == "pdf"
    assert hit["size_bytes"] == len(PDF_BYTES)
    assert hit["subject"] == "Con allegato"
    assert hit["alive_in_live"] is True
    assert hit["has_live_or_snapshot"] is True
    assert db_session.query(AuditLog).filter_by(action="restore.search_all").count() == 0


def test_search_foreign_invisible_even_with_include_all_for_non_admin(
    client, db_session, default_store, tmp_path
):
    _mk_user(db_session, default_store, "luigi")  # owns nothing
    _mk_account_with_attachment(db_session, default_store, tmp_path, owner=None)
    _login(client, "luigi")

    resp = client.post(SEARCH_URL, json={"query": "fattura", "include_all": True})

    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 0
    assert db_session.query(AuditLog).filter_by(action="restore.search_all").count() == 0


def test_search_admin_include_all_sees_foreign_and_audits(
    client, db_session, default_store, tmp_path
):
    """Admin + include_all widens scope to every account and writes exactly one
    restore.search_all row per request, tagged kind=attachments."""
    _mk_user(db_session, default_store, "root", role=UserRole.admin)
    _mk_account_with_attachment(db_session, default_store, tmp_path, owner=None)
    _login(client, "root")

    # Privacy default first: no flag, no hit, no audit.
    resp = client.post(SEARCH_URL, json={"query": "fattura"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 0
    assert db_session.query(AuditLog).filter_by(action="restore.search_all").count() == 0

    resp = client.post(SEARCH_URL, json={"query": "fattura", "include_all": True})

    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 1
    entry = db_session.query(AuditLog).filter_by(action="restore.search_all").one()
    assert entry.username == "root"
    assert entry.resource_type == "restore"
    assert entry.details == {"kind": "attachments", "query": "fattura", "accounts": "all"}


def test_search_include_content_with_tika_disabled_is_filename_only(
    client, db_session, default_store, tmp_path
):
    """include_content is forced off while Tika is disabled: a content_text
    match must NOT surface, and the response says content search is off."""
    owner = _mk_user(db_session, default_store, "mario")
    _acc, _row, att = _mk_account_with_attachment(db_session, default_store, tmp_path, owner=owner)
    att.content_text = "magic-token-inside-pdf"
    db_session.commit()
    _login(client, "mario")

    resp = client.post(
        SEARCH_URL, json={"query": "magic-token-inside-pdf", "include_content": True}
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["content_search_available"] is False
    assert body["total"] == 0


def test_search_include_content_with_tika_enabled_matches_content(
    client, db_session, default_store, tmp_path, monkeypatch
):
    owner = _mk_user(db_session, default_store, "mario")
    _acc, _row, att = _mk_account_with_attachment(db_session, default_store, tmp_path, owner=owner)
    att.content_text = "magic-token-inside-pdf"
    db_session.commit()
    monkeypatch.setattr(settings, "tika_enabled", True)
    _login(client, "mario")

    resp = client.post(
        SEARCH_URL, json={"query": "magic-token-inside-pdf", "include_content": True}
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["content_search_available"] is True
    assert body["total"] == 1


def test_search_filters_pass_through(client, db_session, default_store, tmp_path):
    owner = _mk_user(db_session, default_store, "mario")
    _mk_account_with_attachment(db_session, default_store, tmp_path, owner=owner)
    _login(client, "mario")

    miss = client.post(SEARCH_URL, json={"exts": ["png"]})
    hit = client.post(SEARCH_URL, json={"exts": ["pdf"], "min_size": 1})

    assert miss.status_code == 200 and miss.json()["total"] == 0
    assert hit.status_code == 200 and hit.json()["total"] == 1


def test_search_garbage_types_422(client, db_session, default_store):
    _mk_user(db_session, default_store, "mario")
    _login(client, "mario")

    bad_query = client.post(SEARCH_URL, json={"query": {"x": 1}})
    bad_exts = client.post(SEARCH_URL, json={"exts": "pdf"})
    bad_page = client.post(SEARCH_URL, json={"page": "abc"})
    bad_size = client.post(SEARCH_URL, json={"min_size": {"gt": 1}})

    assert bad_query.status_code == 422
    assert bad_exts.status_code == 422
    assert bad_page.status_code == 422
    assert bad_size.status_code == 422


def test_search_unauthenticated_401(client):
    resp = client.post(SEARCH_URL, json={"query": "x"})

    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/restore/attachments/{account_id}/{hash}/{part_index}/download
# ---------------------------------------------------------------------------


def test_download_owner_gets_exact_bytes_headers_and_audit(
    client, db_session, default_store, tmp_path
):
    owner = _mk_user(db_session, default_store, "mario")
    acc, row, att = _mk_account_with_attachment(db_session, default_store, tmp_path, owner=owner)
    _login(client, "mario")

    resp = client.get(_download_url(acc, row, att.part_index))

    assert resp.status_code == 200, resp.text
    assert resp.content == PDF_BYTES
    # ALWAYS octet-stream: hostile HTML/SVG must not execute on our origin.
    assert resp.headers["content-type"] == "application/octet-stream"
    cd = resp.headers["content-disposition"]
    assert cd.startswith("attachment; ")
    assert 'filename="fattura-novit_.pdf"' in cd  # ASCII fallback: à -> _
    assert "filename*=UTF-8''fattura-novit%C3%A0.pdf" in cd
    entry = db_session.query(AuditLog).filter_by(action="attachment.download").one()
    assert entry.username == "mario"
    assert entry.resource_type == "attachment"
    assert entry.resource_id == acc.id
    assert entry.resource_name == "fattura-novità.pdf"
    assert entry.details == {
        "message_id_hash": row.message_id_hash.hex(),
        "part_index": att.part_index,
        "source": "live",
    }


def test_download_non_owner_404_without_audit(client, db_session, default_store, tmp_path):
    _mk_user(db_session, default_store, "luigi")  # owns nothing
    acc, row, att = _mk_account_with_attachment(db_session, default_store, tmp_path, owner=None)
    _login(client, "luigi")

    resp = client.get(_download_url(acc, row, att.part_index))

    assert resp.status_code == 404
    assert db_session.query(AuditLog).filter_by(action="attachment.download").count() == 0


def test_download_admin_needs_include_all_for_foreign_account(
    client, db_session, default_store, tmp_path
):
    """Privacy default: foreign account 404s for an admin too; include_all=true
    unlocks it and the (always-on) attachment.download row is the audit trail —
    no second escalation row."""
    _mk_user(db_session, default_store, "root", role=UserRole.admin)
    acc, row, att = _mk_account_with_attachment(db_session, default_store, tmp_path, owner=None)
    _login(client, "root")

    denied = client.get(_download_url(acc, row, att.part_index))
    assert denied.status_code == 404
    assert db_session.query(AuditLog).filter_by(action="attachment.download").count() == 0

    resp = client.get(_download_url(acc, row, att.part_index) + "?include_all=true")

    assert resp.status_code == 200, resp.text
    assert resp.content == PDF_BYTES
    entry = db_session.query(AuditLog).filter_by(action="attachment.download").one()
    assert entry.username == "root"
    assert entry.details["source"] == "live"
    # No second escalation row — the always-on download row IS the trail.
    actions = [a.action for a in db_session.query(AuditLog).all()]
    assert "restore.search_all" not in actions
    assert "restore.preview" not in actions


def test_download_bad_hex_400(client, db_session, default_store, tmp_path):
    owner = _mk_user(db_session, default_store, "mario")
    acc, _row, _att = _mk_account_with_attachment(db_session, default_store, tmp_path, owner=owner)
    _login(client, "mario")

    resp = client.get(f"/api/restore/attachments/{acc.id}/zzzz-not-hex/2/download")

    assert resp.status_code == 400


def test_download_unknown_part_index_404(client, db_session, default_store, tmp_path):
    owner = _mk_user(db_session, default_store, "mario")
    acc, row, _att = _mk_account_with_attachment(db_session, default_store, tmp_path, owner=owner)
    _login(client, "mario")

    # part 1 is the text/plain body leaf — a real MIME part, but NOT an
    # attachment row; part 99 does not exist at all. Both must 404.
    body_leaf = client.get(_download_url(acc, row, 1))
    beyond = client.get(_download_url(acc, row, 99))

    assert body_leaf.status_code == 404
    assert beyond.status_code == 404
    assert db_session.query(AuditLog).filter_by(action="attachment.download").count() == 0


def test_download_snapshot_only_message(client, db_session, default_store, tmp_path, monkeypatch):
    """Live file gone (deleted upstream): bytes come from the newest snapshot
    via the preview locator stack, and the audit row says so."""
    owner = _mk_user(db_session, default_store, "mario")
    acc, row, att = _mk_account_with_attachment(db_session, default_store, tmp_path, owner=owner)
    path = os.path.join(acc.maildir_path, "cur", "500.m1.host:2,S")
    with open(path, "rb") as f:
        raw = f.read()
    os.remove(path)
    row.deleted_at = datetime.now(UTC)
    repo = Repository(name="r", backend_type="local", local_path="/tmp/r", restic_password="x")
    db_session.add(repo)
    db_session.flush()
    db_session.add(BackupPolicy(account_id=acc.id, destination_id=repo.id))
    db_session.add(
        SnapshotMessage(snapshot_id="ab12", account_id=acc.id, message_id_hash=row.message_id_hash)
    )
    db_session.commit()

    def fake_dump(destination, account_id, snapshot_id, dump_path, **kwargs):
        return raw if dump_path.endswith("500.m1.host:2,S") else None

    monkeypatch.setattr(preview_service.restic_service, "dump_file", fake_dump)
    monkeypatch.setattr(
        preview_service.restic_service,
        "list_snapshots",
        lambda *a, **k: [{"short_id": "ab12", "time": "2026-06-01T00:00:00Z"}],
    )
    _login(client, "mario")

    resp = client.get(_download_url(acc, row, att.part_index))

    assert resp.status_code == 200, resp.text
    assert resp.content == PDF_BYTES
    entry = db_session.query(AuditLog).filter_by(action="attachment.download").one()
    assert entry.details["source"] == "snapshot:ab12"


def test_download_oversize_message_502(client, db_session, default_store, tmp_path, monkeypatch):
    """A cap-sized read is presumed truncated (the Plan-2 C1 lesson): refuse
    with 502 instead of handing the user silently corrupted bytes."""
    owner = _mk_user(db_session, default_store, "mario")
    acc, row, att = _mk_account_with_attachment(db_session, default_store, tmp_path, owner=owner)
    monkeypatch.setattr(staging_service, "STAGING_DUMP_MAX_BYTES", 16)
    _login(client, "mario")

    resp = client.get(_download_url(acc, row, att.part_index))

    assert resp.status_code == 502
    assert resp.json()["detail"] == "attachment too large to extract"
    assert db_session.query(AuditLog).filter_by(action="attachment.download").count() == 0


def test_download_filename_sanitized_against_header_injection(
    client, db_session, default_store, tmp_path
):
    """A stored filename with quotes and a newline must yield a clean
    Content-Disposition — no header splitting, no quoted-string escape."""
    owner = _mk_user(db_session, default_store, "mario")
    acc, row, att = _mk_account_with_attachment(db_session, default_store, tmp_path, owner=owner)
    att.filename = 'evil".pdf"\r\nX-Injected: yes'
    db_session.commit()
    _login(client, "mario")

    resp = client.get(_download_url(acc, row, att.part_index))

    assert resp.status_code == 200, resp.text
    assert "x-injected" not in resp.headers
    cd = resp.headers["content-disposition"]
    assert "\r" not in cd and "\n" not in cd
    assert 'filename="evil.pdfX-Injected: yes"' in cd
    assert "filename*=UTF-8''evil.pdfX-Injected%3A%20yes" in cd


def test_download_unauthenticated_401(client, db_session, default_store, tmp_path):
    acc, row, att = _mk_account_with_attachment(db_session, default_store, tmp_path, owner=None)

    resp = client.get(_download_url(acc, row, att.part_index))

    assert resp.status_code == 401


def test_action_label_covers_attachment_download():
    from mailfallback.services.audit_service import get_action_label

    assert get_action_label("attachment.download") != "attachment.download"
