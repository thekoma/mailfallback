"""Tests for search_service — Phase 1 header search via mail_index."""

import time as _time
from datetime import UTC, datetime, timedelta

import pytest

from mailfallback.models import (
    Account,
    MailIndexMessage,
    User,
    UserRole,
)
from mailfallback.security import hash_password
from mailfallback.services import search_service
from mailfallback.services.search_service import (
    _dovecot_body_search,
    _parse_message_id_from_fetch,
)


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


def _install_fake_dovecot(monkeypatch, conn):
    def fake_connect(db, account):
        return conn, "_restore_test"

    def fake_delete_temp(db, username):
        pass

    monkeypatch.setattr("mailfallback.routers.restore._connect_dovecot_for_account", fake_connect)
    monkeypatch.setattr(
        "mailfallback.services.dovecot_auth.delete_temp_imap_user", fake_delete_temp
    )
    monkeypatch.setattr("mailfallback.routers.restore.account_namespace_prefix", lambda a: "")


def test_dovecot_body_search_returns_hashes_for_matched_uids(db_session, search_setup, monkeypatch):
    from mailfallback.services.index_service import _hash_message_id

    selected = []

    class FakeConn:
        def select(self, target, readonly=True):
            selected.append(target)
            return ("OK", [b"3"])

        def uid(self, *args):
            if args[0] == "SEARCH":
                return ("OK", [b"7"])
            if args[0] == "FETCH":
                return (
                    "OK",
                    [(b"1 (UID 7 ...", b"Message-ID: <2@h>\r\n"), b")"],
                )
            return ("NO", [b""])

        def logout(self):
            pass

    _install_fake_dovecot(monkeypatch, FakeConn())
    acct = search_setup["account"]
    deadline = _time.monotonic() + 10
    matched, partial = _dovecot_body_search(db_session, [acct.id], "hello", deadline)

    assert _hash_message_id("<2@h>") in matched
    assert partial is False


def test_dovecot_body_search_only_selects_live_folders(db_session, search_setup, monkeypatch):
    """A folder that exists only via a deleted (snapshot-only) message must NOT
    be body-searched — deep search is live-only."""
    from mailfallback.models import MailIndexMessage

    acct = search_setup["account"]
    db_session.add(
        MailIndexMessage(
            account_id=acct.id,
            message_id_hash=b"\x08" * 20,
            message_id="<8@h>",
            subject="deleted only",
            folder_path="Trash",
            maildir_filename="8",
            deleted_at=datetime.now(UTC),
        )
    )
    db_session.commit()

    selected = []

    class FakeConn:
        def select(self, target, readonly=True):
            selected.append(target)
            return ("OK", [b"0"])

        def uid(self, *args):
            return ("OK", [b""])

        def logout(self):
            pass

    _install_fake_dovecot(monkeypatch, FakeConn())
    deadline = _time.monotonic() + 10
    _dovecot_body_search(db_session, [acct.id], "x", deadline)

    assert '"Trash"' not in selected
    assert '"INBOX"' in selected


def test_dovecot_body_search_timeout_sets_partial(db_session, search_setup, monkeypatch):
    class FakeConn:
        def select(self, target, readonly=True):
            return ("OK", [b"0"])

        def uid(self, *args):
            return ("OK", [b""])

        def logout(self):
            pass

    _install_fake_dovecot(monkeypatch, FakeConn())
    acct = search_setup["account"]
    deadline = _time.monotonic() - 1  # already expired
    matched, partial = _dovecot_body_search(db_session, [acct.id], "x", deadline)

    assert matched == set()
    assert partial is True


def test_dovecot_body_search_sanitises_crlf_in_keyword(db_session, search_setup, monkeypatch):
    captured = []

    class FakeConn:
        def select(self, target, readonly=True):
            captured.append(target)
            return ("OK", [b"0"])

        def uid(self, *args):
            captured.append(args)
            return ("OK", [b""])  # no UIDs -> FETCH not reached

        def logout(self):
            pass

    _install_fake_dovecot(monkeypatch, FakeConn())
    acct = search_setup["account"]
    deadline = _time.monotonic() + 10
    _dovecot_body_search(db_session, [acct.id], 'evil"\r\nLOGOUT', deadline)

    for entry in captured:
        joined = " ".join(str(a) for a in (entry if isinstance(entry, tuple) else (entry,)))
        assert "\r" not in joined
        assert "\n" not in joined


def test_parse_message_id_from_fetch_tuple():
    item = (
        b"1 (UID 7 BODY[HEADER.FIELDS (MESSAGE-ID)] {38}",
        b"Message-ID: <abc@example.com>\r\n\r\n",
    )
    assert _parse_message_id_from_fetch(item) == "<abc@example.com>"


def test_parse_message_id_from_fetch_case_insensitive():
    item = (b"meta", b"message-id:   <X@y>\r\n")
    assert _parse_message_id_from_fetch(item) == "<X@y>"


def test_parse_message_id_from_fetch_non_tuple_returns_none():
    assert _parse_message_id_from_fetch(b")") is None


def test_parse_message_id_from_fetch_missing_header_returns_none():
    assert _parse_message_id_from_fetch((b"meta", b"Subject: hi\r\n")) is None
