"""Preview service — headers + body snippet from live Maildir or snapshot."""

import os
from datetime import UTC, datetime
from email.message import EmailMessage

from mailfallback.models import (
    Account,
    BackupPolicy,
    MailIndexMessage,
    Repository,
    SnapshotMessage,
)
from mailfallback.services import index_service, preview_service


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


def _mk_account(db_session, default_store, tmp_path):
    acc = Account(
        name="acc1",
        imap_host="h",
        maildir_path=str(tmp_path / "mail"),
        store_id=default_store.id,
    )
    db_session.add(acc)
    db_session.commit()
    return acc


class TestPreviewLive:
    def test_live_preview_returns_body_and_attachments(self, db_session, default_store, tmp_path):
        acc = _mk_account(db_session, default_store, tmp_path)
        _write_maildir_message(
            acc.maildir_path,
            "200.m1.host:2,S",
            _msg("<p1@x>", subject="Fattura marzo", attachments=[("f.pdf", b"%PDF")]),
        )
        index_service.upsert_message_set(db_session, acc.id)
        row = db_session.query(MailIndexMessage).filter_by(account_id=acc.id).one()

        out = preview_service.get_preview(db_session, acc, row.message_id_hash)

        assert out is not None
        assert out["subject"] == "Fattura marzo"
        assert out["source"] == "live"
        assert out["alive_in_live"] is True
        assert out["folder_path"] == "INBOX"
        assert "body text" in out["body_snippet"]
        assert out["from_addr"] == "sender@example.com"
        assert out["to_addrs"] == ["dest@example.com"]
        assert out["attachments"] == [{"filename": "f.pdf", "ext": "pdf", "size_bytes": 4}]

    def test_live_preview_inbox_subdirectory_layout(self, db_session, default_store, tmp_path):
        # Production layout (mbsync `Inbox {path}/INBOX`): INBOX is a real
        # subdirectory, but folder_path is still "INBOX" — regression guard
        # for the shared both-bases lookup helper.
        acc = _mk_account(db_session, default_store, tmp_path)
        _write_maildir_message(
            os.path.join(acc.maildir_path, "INBOX"),
            "201.m1.host:2,S",
            _msg("<p2@x>", subject="Dentro INBOX"),
        )
        index_service.upsert_message_set(db_session, acc.id)
        row = db_session.query(MailIndexMessage).filter_by(account_id=acc.id).one()
        assert row.folder_path == "INBOX"

        out = preview_service.get_preview(db_session, acc, row.message_id_hash)

        assert out is not None
        assert out["subject"] == "Dentro INBOX"
        assert out["source"] == "live"
        assert "body text" in out["body_snippet"]

    def test_live_preview_survives_flag_suffix_rename(self, db_session, default_store, tmp_path):
        # Dovecot may rename the file (new flags) after the index walk — the
        # locator must fall back to matching on the stable prefix.
        acc = _mk_account(db_session, default_store, tmp_path)
        _write_maildir_message(acc.maildir_path, "202.m1.host:2,S", _msg("<p3@x>"))
        index_service.upsert_message_set(db_session, acc.id)
        row = db_session.query(MailIndexMessage).filter_by(account_id=acc.id).one()
        cur = os.path.join(acc.maildir_path, "cur")
        os.rename(
            os.path.join(cur, "202.m1.host:2,S"),
            os.path.join(cur, "202.m1.host:2,RS"),
        )

        out = preview_service.get_preview(db_session, acc, row.message_id_hash)

        assert out is not None
        assert out["source"] == "live"
        assert "body text" in out["body_snippet"]

    def test_missing_message_returns_none(self, db_session, default_store, tmp_path):
        acc = _mk_account(db_session, default_store, tmp_path)

        assert preview_service.get_preview(db_session, acc, b"\x00" * 20) is None


class TestPreviewSnapshot:
    def _index_then_delete(self, db_session, acc, filename, msg):
        """Index a message, capture its raw bytes, then make it snapshot-only."""
        _write_maildir_message(acc.maildir_path, filename, msg)
        index_service.upsert_message_set(db_session, acc.id)
        row = db_session.query(MailIndexMessage).filter_by(account_id=acc.id).one()
        path = os.path.join(acc.maildir_path, "cur", filename)
        with open(path, "rb") as f:
            raw = f.read()
        os.remove(path)
        row.deleted_at = datetime.now(UTC)
        db_session.commit()
        return row, raw

    def test_snapshot_preview_uses_restic_dump(
        self, db_session, default_store, tmp_path, monkeypatch
    ):
        acc = _mk_account(db_session, default_store, tmp_path)
        row, raw = self._index_then_delete(
            db_session, acc, "300.m1.host:2,S", _msg("<s1@x>", subject="Vecchia fattura")
        )
        repo = Repository(name="r", backend_type="local", local_path="/tmp/r", restic_password="x")
        db_session.add(repo)
        db_session.flush()
        db_session.add(BackupPolicy(account_id=acc.id, destination_id=repo.id))
        db_session.add(
            SnapshotMessage(
                snapshot_id="ab12", account_id=acc.id, message_id_hash=row.message_id_hash
            )
        )
        db_session.commit()

        dump_calls = []

        def fake_dump(destination, account_id, snapshot_id, path, **kwargs):
            dump_calls.append((snapshot_id, path))
            return raw

        monkeypatch.setattr(preview_service.restic_service, "dump_file", fake_dump)
        monkeypatch.setattr(
            preview_service.restic_service,
            "list_snapshots",
            lambda *a, **k: [{"short_id": "ab12", "time": "2026-06-01T00:00:00Z"}],
        )

        out = preview_service.get_preview(db_session, acc, row.message_id_hash)

        assert out is not None
        assert out["source"] == "snapshot:ab12"
        assert out["subject"] == "Vecchia fattura"
        assert out["alive_in_live"] is False
        assert "body text" in out["body_snippet"]
        assert dump_calls and dump_calls[0][0] == "ab12"

    def test_snapshot_preview_no_policy_returns_none(self, db_session, default_store, tmp_path):
        acc = _mk_account(db_session, default_store, tmp_path)
        row, _raw = self._index_then_delete(db_session, acc, "301.m1.host:2,S", _msg("<s2@x>"))
        db_session.add(
            SnapshotMessage(
                snapshot_id="ab12", account_id=acc.id, message_id_hash=row.message_id_hash
            )
        )
        db_session.commit()

        assert preview_service.get_preview(db_session, acc, row.message_id_hash) is None

    def test_snapshot_preview_restic_failure_returns_none(
        self, db_session, default_store, tmp_path, monkeypatch
    ):
        # list_snapshots raises RuntimeError on restic failure — the preview
        # must swallow it and degrade to None, never 500.
        acc = _mk_account(db_session, default_store, tmp_path)
        row, _raw = self._index_then_delete(db_session, acc, "302.m1.host:2,S", _msg("<s3@x>"))
        repo = Repository(name="r", backend_type="local", local_path="/tmp/r", restic_password="x")
        db_session.add(repo)
        db_session.flush()
        db_session.add(BackupPolicy(account_id=acc.id, destination_id=repo.id))
        db_session.add(
            SnapshotMessage(
                snapshot_id="ab12", account_id=acc.id, message_id_hash=row.message_id_hash
            )
        )
        db_session.commit()

        def boom(*a, **k):
            raise RuntimeError("restic unreachable")

        monkeypatch.setattr(preview_service.restic_service, "list_snapshots", boom)

        assert preview_service.get_preview(db_session, acc, row.message_id_hash) is None
