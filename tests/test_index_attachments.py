"""Attachment extraction during the live Maildir index walk."""

import os
from email.message import EmailMessage

import pytest

from mailfallback.models import Account, MailIndexAttachment, MailIndexMessage
from mailfallback.services import index_service


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


class TestAttachmentParse:
    def test_attachments_indexed_on_insert(self, db_session, default_store, tmp_path):
        acc = _mk_account(db_session, default_store, tmp_path)
        _write_maildir_message(
            acc.maildir_path,
            "100.m1.host:2,S",
            _msg("<a1@x>", attachments=[("fattura-113.pdf", b"%PDF-fake")]),
        )

        index_service.upsert_message_set(db_session, acc.id)

        row = db_session.query(MailIndexMessage).filter_by(account_id=acc.id).one()
        assert row.has_attachments is True
        assert row.attachments_indexed_at is not None
        att = db_session.query(MailIndexAttachment).filter_by(account_id=acc.id).one()
        assert att.filename == "fattura-113.pdf"
        assert att.ext == "pdf"
        assert att.size_bytes == len(b"%PDF-fake")
        assert att.content_type == "application/pdf"

    def test_message_without_attachments(self, db_session, default_store, tmp_path):
        acc = _mk_account(db_session, default_store, tmp_path)
        _write_maildir_message(acc.maildir_path, "101.m1.host:2,S", _msg("<a2@x>"))

        index_service.upsert_message_set(db_session, acc.id)

        row = db_session.query(MailIndexMessage).filter_by(account_id=acc.id).one()
        assert row.has_attachments is False
        assert row.attachments_indexed_at is not None
        assert db_session.query(MailIndexAttachment).count() == 0

    def test_part_index_skips_body_and_is_stable(self, db_session, default_store, tmp_path):
        acc = _mk_account(db_session, default_store, tmp_path)
        _write_maildir_message(
            acc.maildir_path,
            "104.m1.host:2,S",
            _msg("<a4@x>", attachments=[("a.pdf", b"A"), ("b.pdf", b"BB")]),
        )
        index_service.upsert_message_set(db_session, acc.id)
        idx = [
            a.part_index
            for a in db_session.query(MailIndexAttachment).order_by(MailIndexAttachment.part_index)
        ]
        assert idx == [2, 3]  # body text/plain consumed index 1

    def test_forwarded_message_attachment_size_none(self, db_session, default_store, tmp_path):
        acc = _mk_account(db_session, default_store, tmp_path)
        inner = EmailMessage()
        inner["Subject"] = "inner"
        inner.set_content("inner body")
        msg = _msg("<a5@x>")
        msg.add_attachment(inner, filename="forwarded.eml")
        _write_maildir_message(acc.maildir_path, "105.m1.host:2,S", msg)

        index_service.upsert_message_set(db_session, acc.id)

        # message/rfc822 has no decodable payload — size must be honest NULL, not 0
        att = db_session.query(MailIndexAttachment).filter_by(account_id=acc.id).one()
        assert att.filename == "forwarded.eml"
        assert att.content_type == "message/rfc822"
        assert att.size_bytes is None

    def test_existing_rows_not_reparsed(self, db_session, default_store, tmp_path):
        acc = _mk_account(db_session, default_store, tmp_path)
        _write_maildir_message(
            acc.maildir_path,
            "102.m1.host:2,S",
            _msg("<a3@x>", attachments=[("doc.pdf", b"x")]),
        )
        index_service.upsert_message_set(db_session, acc.id)
        db_session.query(MailIndexAttachment).delete()
        db_session.commit()

        # Second walk: row exists, attachments must NOT be re-created
        index_service.upsert_message_set(db_session, acc.id)
        assert db_session.query(MailIndexAttachment).count() == 0


class TestBackfillAttachments:
    def test_backfill_fills_old_rows_and_resumes(self, db_session, default_store, tmp_path):
        acc = _mk_account(db_session, default_store, tmp_path)
        _write_maildir_message(
            acc.maildir_path,
            "103.m1.host:2,S",
            _msg("<b1@x>", attachments=[("a.pdf", b"xx")]),
        )
        index_service.upsert_message_set(db_session, acc.id)
        # Simulate a pre-attachment-era row
        row = db_session.query(MailIndexMessage).filter_by(account_id=acc.id).one()
        row.attachments_indexed_at = None
        row.has_attachments = False
        db_session.query(MailIndexAttachment).delete()
        db_session.commit()

        n = index_service.backfill_attachments(db_session, acc.id)
        assert n == 1
        row = db_session.query(MailIndexMessage).filter_by(account_id=acc.id).one()
        assert row.has_attachments is True
        assert db_session.query(MailIndexAttachment).count() == 1

        # Resume: nothing left to do
        assert index_service.backfill_attachments(db_session, acc.id) == 0

    def test_backfill_handles_inbox_subdirectory_layout(self, db_session, default_store, tmp_path):
        # Production layout (mbsync `Inbox {path}/INBOX`): INBOX is a real
        # subdirectory; _walk_maildir stores folder_path="INBOX" for it too.
        acc = _mk_account(db_session, default_store, tmp_path)
        _write_maildir_message(
            os.path.join(acc.maildir_path, "INBOX"),
            "106.m1.host:2,S",
            _msg("<b3@x>", attachments=[("a.pdf", b"xx")]),
        )
        index_service.upsert_message_set(db_session, acc.id)
        row = db_session.query(MailIndexMessage).filter_by(account_id=acc.id).one()
        assert row.folder_path == "INBOX"  # same folder_path as the top-level layout
        row.attachments_indexed_at = None
        row.has_attachments = False
        db_session.query(MailIndexAttachment).delete()
        db_session.commit()

        n = index_service.backfill_attachments(db_session, acc.id)

        assert n == 1
        row = db_session.query(MailIndexMessage).filter_by(account_id=acc.id).one()
        assert row.has_attachments is True
        assert db_session.query(MailIndexAttachment).count() == 1

    def test_backfill_skips_missing_file_without_marking(self, db_session, default_store, tmp_path):
        acc = _mk_account(db_session, default_store, tmp_path)
        _write_maildir_message(
            acc.maildir_path,
            "105.m1.host:2,S",
            _msg("<b2@x>", attachments=[("a.pdf", b"xx")]),
        )
        index_service.upsert_message_set(db_session, acc.id)
        row = db_session.query(MailIndexMessage).filter_by(account_id=acc.id).one()
        row.attachments_indexed_at = None
        row.has_attachments = False
        db_session.query(MailIndexAttachment).delete()
        db_session.commit()
        # File disappears (flag-rename race / deleted between index and backfill):
        # must be SKIPPED, not baked as has_attachments=False forever.
        os.remove(os.path.join(acc.maildir_path, "cur", "105.m1.host:2,S"))

        n = index_service.backfill_attachments(db_session, acc.id)

        assert n == 0  # skipped rows are not counted as processed
        row = db_session.query(MailIndexMessage).filter_by(account_id=acc.id).one()
        assert row.attachments_indexed_at is None  # left pending for the next walk
        assert row.has_attachments is False

        # The next walk reconciles (here: marks the row deleted), so the
        # pending set shrinks instead of being retried forever.
        index_service.upsert_message_set(db_session, acc.id)
        row = db_session.query(MailIndexMessage).filter_by(account_id=acc.id).one()
        assert row.deleted_at is not None
        assert index_service.backfill_attachments(db_session, acc.id) == 0

    @pytest.mark.skipif(os.geteuid() == 0, reason="chmod 0o000 does not block reads as root")
    def test_backfill_marks_present_but_unparsable_processed(
        self, db_session, default_store, tmp_path
    ):
        acc = _mk_account(db_session, default_store, tmp_path)
        _write_maildir_message(
            acc.maildir_path,
            "107.m1.host:2,S",
            _msg("<b4@x>", attachments=[("a.pdf", b"xx")]),
        )
        index_service.upsert_message_set(db_session, acc.id)
        row = db_session.query(MailIndexMessage).filter_by(account_id=acc.id).one()
        row.attachments_indexed_at = None
        row.has_attachments = False
        db_session.query(MailIndexAttachment).delete()
        db_session.commit()
        # Present but unreadable: open() raises OSError, _parse_attachments
        # returns None. Anti-wedge contract: mark processed, never retry.
        path = os.path.join(acc.maildir_path, "cur", "107.m1.host:2,S")
        os.chmod(path, 0o000)
        try:
            n = index_service.backfill_attachments(db_session, acc.id)
        finally:
            os.chmod(path, 0o644)

        assert n == 1
        row = db_session.query(MailIndexMessage).filter_by(account_id=acc.id).one()
        assert row.attachments_indexed_at is not None  # marked, won't retry forever
        assert row.has_attachments is False
        assert db_session.query(MailIndexAttachment).count() == 0
        assert index_service.backfill_attachments(db_session, acc.id) == 0

    def test_backfill_covers_non_inbox_folders(self, db_session, default_store, tmp_path):
        acc = _mk_account(db_session, default_store, tmp_path)
        _write_maildir_message(
            os.path.join(acc.maildir_path, "Sent"),
            "108.m1.host:2,S",
            _msg("<b5@x>", attachments=[("s.pdf", b"xx")]),
        )
        # INBOX subfolder: folder_path "INBOX/Sub" must not get the INBOX
        # base prepended twice.
        _write_maildir_message(
            os.path.join(acc.maildir_path, "INBOX", "Sub"),
            "109.m1.host:2,S",
            _msg("<b6@x>", attachments=[("i.pdf", b"yy")]),
        )
        index_service.upsert_message_set(db_session, acc.id)
        rows = db_session.query(MailIndexMessage).filter_by(account_id=acc.id).all()
        assert sorted(r.folder_path for r in rows) == ["INBOX/Sub", "Sent"]
        for r in rows:
            r.attachments_indexed_at = None
            r.has_attachments = False
        db_session.query(MailIndexAttachment).delete()
        db_session.commit()

        n = index_service.backfill_attachments(db_session, acc.id)

        assert n == 2
        rows = db_session.query(MailIndexMessage).filter_by(account_id=acc.id).all()
        assert all(r.has_attachments for r in rows)
        filenames = {a.filename for a in db_session.query(MailIndexAttachment).all()}
        assert filenames == {"s.pdf", "i.pdf"}
