"""Tests for search_service — Phase 1 header search via mail_index."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from mailfallback.models import (
    Account,
    MailIndexMessage,
    User,
    UserRole,
)
from mailfallback.security import hash_password
from mailfallback.services import search_service


@pytest.fixture
def search_setup(db_session, default_store, tmp_path):
    user = User(
        username="u",
        password_hash=hash_password("p"),
        role=UserRole.admin,
        enabled=True,
        store_id=default_store.id,
    )
    db_session.add(user)

    acct = Account(
        name="a",
        store=default_store,
        maildir_path=str(tmp_path),
        imap_host="imap.example.com",
    )
    db_session.add(acct)
    db_session.flush()
    acct.owners.append(user)

    # Add three messages directly (skip the file walk — already tested)
    now = datetime.now(UTC)
    db_session.add_all(
        [
            MailIndexMessage(
                account_id=acct.id,
                message_id_hash=b"\x01" * 20,
                message_id="<1@h>",
                subject="fattura marzo",
                from_addr="boss@ditta.it",
                from_name="Boss",
                date_sent=now - timedelta(days=2),
                folder_path="INBOX",
                maildir_filename="1.host:2,",
            ),
            MailIndexMessage(
                account_id=acct.id,
                message_id_hash=b"\x02" * 20,
                message_id="<2@h>",
                subject="hello world",
                from_addr="bob@x",
                from_name="Bob",
                date_sent=now - timedelta(days=10),
                folder_path="INBOX",
                maildir_filename="2.host:2,",
            ),
            MailIndexMessage(
                account_id=acct.id,
                message_id_hash=b"\x03" * 20,
                message_id="<3@h>",
                subject="old fattura",
                from_addr="boss@ditta.it",
                from_name="Boss",
                date_sent=now - timedelta(days=100),
                folder_path="INBOX",
                maildir_filename="3.host:2,",
            ),
        ]
    )
    db_session.commit()
    return {"user": user, "account": acct}


def test_search_returns_matching_subject(db_session, search_setup):
    result = search_service.search_messages(
        db_session,
        user=search_setup["user"],
        query="fattura",
    )
    subjects = [r["subject"] for r in result["results"]]
    assert "fattura marzo" in subjects
    assert "old fattura" in subjects
    assert "hello world" not in subjects


def test_search_filters_by_date_range(db_session, search_setup):
    now = datetime.now(UTC)
    result = search_service.search_messages(
        db_session,
        user=search_setup["user"],
        query="fattura",
        range_start=now - timedelta(days=7),
        range_end=now,
    )
    subjects = [r["subject"] for r in result["results"]]
    assert subjects == ["fattura marzo"]  # only the recent one


def test_search_respects_account_visibility(db_session, search_setup, default_store):
    # Create a separate account NOT owned by the user
    other = Account(
        name="o",
        store=default_store,
        maildir_path="/x",
        imap_host="i",
    )
    db_session.add(other)
    db_session.flush()
    db_session.add(
        MailIndexMessage(
            account_id=other.id,
            message_id_hash=b"\x09" * 20,
            message_id="<9@h>",
            subject="fattura nascosta",
            folder_path="INBOX",
            maildir_filename="9",
        )
    )
    db_session.commit()

    result = search_service.search_messages(
        db_session,
        user=search_setup["user"],
        query="fattura",
    )
    assert "fattura nascosta" not in [r["subject"] for r in result["results"]]


def test_search_pagination(db_session, search_setup):
    page1 = search_service.search_messages(
        db_session,
        user=search_setup["user"],
        query="",
        page=1,
        page_size=2,
    )
    assert len(page1["results"]) == 2
    assert page1["total"] == 3


@patch("mailfallback.services.search_service._dovecot_filter_body")
def test_phase2_body_filter_marks_survivors(mock_filter, db_session, search_setup):
    """When body=True, _dovecot_filter_body returns a subset of message_id_hashes
    that match the body keyword. search_messages flags those as body_matched=True."""
    # Mock returns: only the first message hash matches body
    mock_filter.return_value = {b"\x01" * 20}

    result = search_service.search_messages(
        db_session,
        user=search_setup["user"],
        query="fattura",
        body=True,
    )
    by_subject = {r["subject"]: r for r in result["results"]}
    assert by_subject["fattura marzo"]["body_matched"] is True
    assert by_subject["old fattura"]["body_matched"] is False


def test_phase2_sanitises_crlf_in_keyword(db_session, search_setup, monkeypatch):
    """Phase 2 keyword/Message-Id sanitisation strips control chars (CRLF)
    so a malicious input can't break out of the IMAP quoted string."""
    captured_searches: list[tuple] = []

    class FakeConn:
        def select(self, *args, **kwargs):
            return ("OK", [b"0"])

        def uid(self, *args):
            captured_searches.append(args)
            # Reply with no UIDs so the inner loop is skipped
            return ("OK", [b""])

        def logout(self):
            pass

    def fake_connect(db, account):
        return FakeConn(), "_restore_test"

    def fake_delete_temp(db, username):
        pass

    monkeypatch.setattr("mailfallback.routers.restore._connect_dovecot_for_account", fake_connect)
    monkeypatch.setattr(
        "mailfallback.services.dovecot_auth.delete_temp_imap_user", fake_delete_temp
    )

    # Inject CRLF + quote into the keyword. After sanitisation the IMAP
    # SEARCH should NOT contain those bytes.
    result = search_service.search_messages(
        db_session,
        user=search_setup["user"],
        query='evil"\r\nLOGOUT',
        body=True,
    )
    assert result["total"] >= 0  # search ran without raising
    # Verify no captured SEARCH arg contains the unsanitised payload
    for args in captured_searches:
        joined = " ".join(str(a) for a in args)
        assert "\r" not in joined
        assert "\n" not in joined
        assert '"\r\n' not in joined
