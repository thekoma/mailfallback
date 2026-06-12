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
        # part_index drives the pane's per-chip download URLs. 2 by the
        # indexer's frozen convention: ALL non-multipart leaves count in walk
        # order, and the text/plain body is leaf 1.
        assert out["attachments"] == [
            {"filename": "f.pdf", "ext": "pdf", "size_bytes": 4, "part_index": 2}
        ]

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

    def _wire_snapshot(self, db_session, acc, row, snapshot_ids=("ab12",)):
        """Repository + BackupPolicy + snapshot bits for the message."""
        repo = Repository(name="r", backend_type="local", local_path="/tmp/r", restic_password="x")
        db_session.add(repo)
        db_session.flush()
        db_session.add(BackupPolicy(account_id=acc.id, destination_id=repo.id))
        for sid in snapshot_ids:
            db_session.add(
                SnapshotMessage(
                    snapshot_id=sid, account_id=acc.id, message_id_hash=row.message_id_hash
                )
            )
        db_session.commit()

    def test_snapshot_preview_uses_restic_dump(
        self, db_session, default_store, tmp_path, monkeypatch
    ):
        acc = _mk_account(db_session, default_store, tmp_path)
        row, raw = self._index_then_delete(
            db_session, acc, "300.m1.host:2,S", _msg("<s1@x>", subject="Vecchia fattura")
        )
        self._wire_snapshot(db_session, acc, row)

        dump_calls = []
        # The file lived at the top-level layout — the snapshot only contains
        # that exact path; dumps of other candidates must miss like real restic.
        expected_path = os.path.join(acc.maildir_path, "cur", "300.m1.host:2,S")

        def fake_dump(destination, account_id, snapshot_id, path, **kwargs):
            dump_calls.append((snapshot_id, path))
            return raw if path == expected_path else None

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
        # Pins the candidate order: INBOX/ base first (cur, new), then root.
        assert dump_calls == [
            ("ab12", os.path.join(acc.maildir_path, "INBOX", "cur", "300.m1.host:2,S")),
            ("ab12", os.path.join(acc.maildir_path, "INBOX", "new", "300.m1.host:2,S")),
            ("ab12", expected_path),
        ]

    def test_snapshot_preview_survives_flag_rename_drift(
        self, db_session, default_store, tmp_path, monkeypatch
    ):
        # A webmail read renames the live file (write-seen ACL adds the S
        # flag) AFTER the snapshot was taken: the snapshot holds the OLD
        # name, the index row carries the NEW one. Exact-name dumps all
        # miss; the prefix-based locate on the newest snapshot must recover.
        acc = _mk_account(db_session, default_store, tmp_path)
        _write_maildir_message(acc.maildir_path, "310.m1.host:2,S", _msg("<s4@x>", subject="Drift"))
        index_service.upsert_message_set(db_session, acc.id)
        cur = os.path.join(acc.maildir_path, "cur")
        with open(os.path.join(cur, "310.m1.host:2,S"), "rb") as f:
            raw = f.read()
        os.rename(os.path.join(cur, "310.m1.host:2,S"), os.path.join(cur, "310.m1.host:2,RS"))
        index_service.upsert_message_set(db_session, acc.id)  # row now has the NEW name
        row = db_session.query(MailIndexMessage).filter_by(account_id=acc.id).one()
        assert row.maildir_filename == "310.m1.host:2,RS"
        os.remove(os.path.join(cur, "310.m1.host:2,RS"))
        row.deleted_at = datetime.now(UTC)
        db_session.commit()
        self._wire_snapshot(db_session, acc, row)

        old_path = os.path.join(acc.maildir_path, "cur", "310.m1.host:2,S")
        dump_calls = []

        def fake_dump(destination, account_id, snapshot_id, path, **kwargs):
            dump_calls.append(path)
            return raw if path == old_path else None  # snapshot knows only the OLD name

        monkeypatch.setattr(preview_service.restic_service, "dump_file", fake_dump)
        monkeypatch.setattr(
            preview_service.restic_service,
            "list_snapshots",
            lambda *a, **k: [{"short_id": "ab12", "time": "2026-06-01T00:00:00Z"}],
        )
        monkeypatch.setattr(
            preview_service.restic_service,
            "list_files",
            lambda *a, **k: iter(
                [
                    os.path.join(acc.maildir_path, "state"),  # not cur/new — filtered
                    os.path.join(acc.maildir_path, "cur", "999.other.host:2,S"),  # other msg
                    old_path,
                ]
            ),
        )

        out = preview_service.get_preview(db_session, acc, row.message_id_hash)

        assert out is not None
        assert out["source"] == "snapshot:ab12"
        assert out["subject"] == "Drift"
        assert "body text" in out["body_snippet"]
        assert dump_calls[-1] == old_path  # located by prefix, dumped at the OLD path

    def test_snapshot_exact_name_attempts_capped(
        self, db_session, default_store, tmp_path, monkeypatch
    ):
        # Five snapshots contain the message; exact-name dumps must stop at
        # the MAX_SNAPSHOT_ATTEMPTS newest, then do ONE prefix locate.
        acc = _mk_account(db_session, default_store, tmp_path)
        row, _raw = self._index_then_delete(db_session, acc, "320.m1.host:2,S", _msg("<s5@x>"))
        sids = [f"s{i}" for i in range(5)]
        self._wire_snapshot(db_session, acc, row, snapshot_ids=sids)

        attempted = []

        def fake_dump(destination, account_id, snapshot_id, path, **kwargs):
            attempted.append(snapshot_id)
            return None  # never found

        listed = []

        def fake_list_files(destination, account_id, snapshot_id):
            listed.append(snapshot_id)
            return iter(())

        monkeypatch.setattr(preview_service.restic_service, "dump_file", fake_dump)
        monkeypatch.setattr(preview_service.restic_service, "list_files", fake_list_files)
        monkeypatch.setattr(
            preview_service.restic_service,
            "list_snapshots",
            # newest-first, matching the documented list_snapshots contract
            lambda *a, **k: [
                {"short_id": sid, "time": f"2026-06-0{5 - i}T00:00:00Z"}
                for i, sid in enumerate(sids)
            ],
        )

        assert preview_service.get_preview(db_session, acc, row.message_id_hash) is None
        assert set(attempted) == {"s0", "s1", "s2"}  # newest three only
        assert listed == ["s0"]  # ONE prefix locate, on the newest match

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
        self._wire_snapshot(db_session, acc, row)

        def boom(*a, **k):
            raise RuntimeError("restic unreachable")

        monkeypatch.setattr(preview_service.restic_service, "list_snapshots", boom)

        assert preview_service.get_preview(db_session, acc, row.message_id_hash) is None
