"""Attachment extraction during the live Maildir index walk."""

import os
from email.message import EmailMessage

import httpx
import pytest

from mailfallback.config import settings
from mailfallback.models import Account, MailIndexAttachment, MailIndexMessage
from mailfallback.services import index_service


class _FakeResp:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


class _FakeHttpx:
    """Stand-in for the httpx module inside index_service.

    Records every PUT so tests can assert the exact URL shape, headers and
    payload — or that NO call happened at all (the tika-disabled pin).
    """

    def __init__(self, response=None, exc=None):
        self.calls = []
        self.response = response if response is not None else _FakeResp()
        self.exc = exc

    def put(self, url, content=None, headers=None, timeout=None):
        self.calls.append({"url": url, "content": content, "headers": headers, "timeout": timeout})
        if self.exc is not None:
            raise self.exc
        return self.response


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


class TestTikaExtraction:
    """Tika content extraction during the index walk (_parse_attachments)."""

    def test_caps_pinned(self):
        # test_oversized_part patches TIKA_MAX_PART_BYTES, so the real
        # values are pinned here (spec: 20 MB part cap, 200 KB text cap).
        assert index_service.TIKA_MAX_PART_BYTES == 20 * 1024 * 1024
        assert index_service.TIKA_TEXT_CAP == 204_800

    def test_tika_disabled_makes_no_http_call(
        self, db_session, default_store, tmp_path, monkeypatch
    ):
        # Default settings: tika_enabled False. Indexing must not even
        # attempt HTTP — the fake records (and would raise on) any call.
        assert settings.tika_enabled is False
        fake = _FakeHttpx(exc=AssertionError("HTTP must not be attempted"))
        monkeypatch.setattr(index_service, "httpx", fake)
        acc = _mk_account(db_session, default_store, tmp_path)
        _write_maildir_message(
            acc.maildir_path,
            "200.m1.host:2,S",
            _msg("<t1@x>", attachments=[("doc.pdf", b"%PDF-x")]),
        )

        index_service.upsert_message_set(db_session, acc.id)

        assert fake.calls == []
        att = db_session.query(MailIndexAttachment).filter_by(account_id=acc.id).one()
        assert att.content_text is None

    def test_tika_enabled_stores_text_with_dovecot_url_shape(
        self, db_session, default_store, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(settings, "tika_enabled", True)
        monkeypatch.setattr(settings, "tika_url", "http://tika-test:9998")
        fake = _FakeHttpx(response=_FakeResp(200, "Extracted text from the PDF"))
        monkeypatch.setattr(index_service, "httpx", fake)
        acc = _mk_account(db_session, default_store, tmp_path)
        _write_maildir_message(
            acc.maildir_path,
            "201.m1.host:2,S",
            _msg("<t2@x>", attachments=[("doc.pdf", b"%PDF-x")]),
        )

        index_service.upsert_message_set(db_session, acc.id)

        att = db_session.query(MailIndexAttachment).filter_by(account_id=acc.id).one()
        assert att.content_text == "Extracted text from the PDF"
        # Mirror the Dovecot-proven shape: PUT {tika_url}/tika/ (trailing slash)
        assert len(fake.calls) == 1
        call = fake.calls[0]
        assert call["url"] == "http://tika-test:9998/tika/"
        assert call["content"] == b"%PDF-x"
        assert call["headers"] == {"Accept": "text/plain", "Content-Type": "application/pdf"}
        assert call["timeout"] == 10.0

    def test_oversized_response_truncated_to_cap(
        self, db_session, default_store, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(settings, "tika_enabled", True)
        huge = "T" * (index_service.TIKA_TEXT_CAP + 5000)
        fake = _FakeHttpx(response=_FakeResp(200, huge))
        monkeypatch.setattr(index_service, "httpx", fake)
        acc = _mk_account(db_session, default_store, tmp_path)
        _write_maildir_message(
            acc.maildir_path,
            "202.m1.host:2,S",
            _msg("<t3@x>", attachments=[("big.pdf", b"%PDF-x")]),
        )

        index_service.upsert_message_set(db_session, acc.id)

        att = db_session.query(MailIndexAttachment).filter_by(account_id=acc.id).one()
        assert att.content_text == huge[: index_service.TIKA_TEXT_CAP]
        assert len(att.content_text) == index_service.TIKA_TEXT_CAP

    def test_httpx_timeout_yields_null_and_indexing_completes(
        self, db_session, default_store, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(settings, "tika_enabled", True)
        fake = _FakeHttpx(exc=httpx.TimeoutException("tika too slow"))
        monkeypatch.setattr(index_service, "httpx", fake)
        acc = _mk_account(db_session, default_store, tmp_path)
        _write_maildir_message(
            acc.maildir_path,
            "203.m1.host:2,S",
            _msg("<t4@x>", attachments=[("doc.pdf", b"%PDF-x")]),
        )

        index_service.upsert_message_set(db_session, acc.id)

        # Row created, message fully indexed — extraction misses never fail sync
        row = db_session.query(MailIndexMessage).filter_by(account_id=acc.id).one()
        assert row.has_attachments is True
        assert row.attachments_indexed_at is not None
        att = db_session.query(MailIndexAttachment).filter_by(account_id=acc.id).one()
        assert att.content_text is None
        assert len(fake.calls) == 1

    def test_non_200_yields_null(self, db_session, default_store, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "tika_enabled", True)
        fake = _FakeHttpx(response=_FakeResp(422, "Unprocessable"))
        monkeypatch.setattr(index_service, "httpx", fake)
        acc = _mk_account(db_session, default_store, tmp_path)
        _write_maildir_message(
            acc.maildir_path,
            "204.m1.host:2,S",
            _msg("<t5@x>", attachments=[("doc.pdf", b"%PDF-x")]),
        )

        index_service.upsert_message_set(db_session, acc.id)

        att = db_session.query(MailIndexAttachment).filter_by(account_id=acc.id).one()
        assert att.content_text is None

    def test_whitespace_only_response_yields_null(
        self, db_session, default_store, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(settings, "tika_enabled", True)
        fake = _FakeHttpx(response=_FakeResp(200, "  \n\t  \n"))
        monkeypatch.setattr(index_service, "httpx", fake)
        acc = _mk_account(db_session, default_store, tmp_path)
        _write_maildir_message(
            acc.maildir_path,
            "205.m1.host:2,S",
            _msg("<t6@x>", attachments=[("scan.pdf", b"%PDF-x")]),
        )

        index_service.upsert_message_set(db_session, acc.id)

        att = db_session.query(MailIndexAttachment).filter_by(account_id=acc.id).one()
        assert att.content_text is None

    def test_oversized_part_skipped_others_extracted(
        self, db_session, default_store, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(settings, "tika_enabled", True)
        monkeypatch.setattr(index_service, "TIKA_MAX_PART_BYTES", 5)
        fake = _FakeHttpx(response=_FakeResp(200, "small text"))
        monkeypatch.setattr(index_service, "httpx", fake)
        acc = _mk_account(db_session, default_store, tmp_path)
        _write_maildir_message(
            acc.maildir_path,
            "206.m1.host:2,S",
            _msg("<t7@x>", attachments=[("big.pdf", b"BIGGER!"), ("small.pdf", b"abc")]),
        )

        index_service.upsert_message_set(db_session, acc.id)

        # Only the small part hit Tika
        assert len(fake.calls) == 1
        assert fake.calls[0]["content"] == b"abc"
        atts = {
            a.filename: a.content_text
            for a in db_session.query(MailIndexAttachment).filter_by(account_id=acc.id)
        }
        assert atts == {"big.pdf": None, "small.pdf": "small text"}


class TestBackfillAttachmentContent:
    """`mfb index backfill-attachments --content-only` service layer."""

    def _index_with_tika_off(self, db_session, default_store, tmp_path, attachments):
        """Index one message while Tika is disabled → rows with NULL content."""
        assert settings.tika_enabled is False
        acc = _mk_account(db_session, default_store, tmp_path)
        _write_maildir_message(
            acc.maildir_path,
            "300.m1.host:2,S",
            _msg("<c1@x>", attachments=attachments),
        )
        index_service.upsert_message_set(db_session, acc.id)
        return acc

    def test_refuses_when_tika_disabled(self, db_session, default_store, tmp_path):
        acc = self._index_with_tika_off(db_session, default_store, tmp_path, [("a.pdf", b"AA")])
        with pytest.raises(ValueError, match=r"[Tt]ika"):
            index_service.backfill_attachment_content(db_session, acc.id)

    def test_fills_null_rows_only_returns_row_count(
        self, db_session, default_store, tmp_path, monkeypatch
    ):
        acc = self._index_with_tika_off(
            db_session, default_store, tmp_path, [("a.pdf", b"AA"), ("b.pdf", b"BB")]
        )
        rows = (
            db_session.query(MailIndexAttachment)
            .filter_by(account_id=acc.id)
            .order_by(MailIndexAttachment.part_index)
            .all()
        )
        assert [r.content_text for r in rows] == [None, None]
        # Pre-fill the first row: the backfill must NOT touch it
        rows[0].content_text = "SENTINEL"
        db_session.commit()

        monkeypatch.setattr(settings, "tika_enabled", True)
        fake = _FakeHttpx(response=_FakeResp(200, "fresh text"))
        monkeypatch.setattr(index_service, "httpx", fake)

        n = index_service.backfill_attachment_content(db_session, acc.id)

        assert n == 1  # attachment ROWS filled, not messages
        rows = (
            db_session.query(MailIndexAttachment)
            .filter_by(account_id=acc.id)
            .order_by(MailIndexAttachment.part_index)
            .all()
        )
        assert rows[0].content_text == "SENTINEL"
        assert rows[1].content_text == "fresh text"

        # Re-run: nothing pending — returns 0 and re-walks no file
        calls_after_first = len(fake.calls)
        assert index_service.backfill_attachment_content(db_session, acc.id) == 0
        assert len(fake.calls) == calls_after_first

    def test_skips_missing_file_without_marking(
        self, db_session, default_store, tmp_path, monkeypatch
    ):
        acc = self._index_with_tika_off(db_session, default_store, tmp_path, [("a.pdf", b"AA")])
        os.remove(os.path.join(acc.maildir_path, "cur", "300.m1.host:2,S"))
        monkeypatch.setattr(settings, "tika_enabled", True)
        fake = _FakeHttpx(response=_FakeResp(200, "never used"))
        monkeypatch.setattr(index_service, "httpx", fake)

        n = index_service.backfill_attachment_content(db_session, acc.id)

        assert n == 0
        assert fake.calls == []
        att = db_session.query(MailIndexAttachment).filter_by(account_id=acc.id).one()
        assert att.content_text is None  # left pending, no fake fill
        # Re-runnable: same outcome, no crash
        assert index_service.backfill_attachment_content(db_session, acc.id) == 0

    def test_extraction_failures_stay_null_and_retry_next_run(
        self, db_session, default_store, tmp_path, monkeypatch
    ):
        acc = self._index_with_tika_off(db_session, default_store, tmp_path, [("a.pdf", b"AA")])
        monkeypatch.setattr(settings, "tika_enabled", True)
        broken = _FakeHttpx(exc=httpx.TimeoutException("down"))
        monkeypatch.setattr(index_service, "httpx", broken)

        assert index_service.backfill_attachment_content(db_session, acc.id) == 0
        att = db_session.query(MailIndexAttachment).filter_by(account_id=acc.id).one()
        assert att.content_text is None

        # Tika recovers → the same run fills the row (resumable by construction)
        monkeypatch.setattr(
            index_service, "httpx", _FakeHttpx(response=_FakeResp(200, "recovered"))
        )
        assert index_service.backfill_attachment_content(db_session, acc.id) == 1
        att = db_session.query(MailIndexAttachment).filter_by(account_id=acc.id).one()
        assert att.content_text == "recovered"

    def test_unknown_account_raises(self, db_session, monkeypatch):
        monkeypatch.setattr(settings, "tika_enabled", True)
        with pytest.raises(ValueError, match="not found"):
            index_service.backfill_attachment_content(db_session, "nope")
