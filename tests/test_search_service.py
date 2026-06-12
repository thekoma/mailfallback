"""Tests for search_service — Phase 1 header search via mail_index."""

import time as _time
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from mailfallback.models import (
    Account,
    MailIndexAttachment,
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


@pytest.fixture
def foreign_setup(db_session, default_store):
    """An admin who owns nothing + an account owned by a different user."""
    admin = User(
        username="root",
        password_hash=hash_password("p"),
        role=UserRole.admin,
        enabled=True,
        store_id=default_store.id,
    )
    luigi = User(
        username="luigi",
        password_hash=hash_password("p"),
        role=UserRole.user,
        enabled=True,
        store_id=default_store.id,
    )
    owner = User(
        username="owner",
        password_hash=hash_password("p"),
        role=UserRole.user,
        enabled=True,
        store_id=default_store.id,
    )
    db_session.add_all([admin, luigi, owner])

    acct = Account(
        name="foreign",
        store=default_store,
        maildir_path="/f",
        imap_host="i",
    )
    db_session.add(acct)
    db_session.flush()
    acct.owners.append(owner)
    db_session.add(
        MailIndexMessage(
            account_id=acct.id,
            message_id_hash=b"\x0a" * 20,
            message_id="<f1@h>",
            subject="fattura riservata",
            folder_path="INBOX",
            maildir_filename="f1",
        )
    )
    db_session.commit()
    return {"admin": admin, "luigi": luigi, "account": acct}


def test_admin_include_all_searches_foreign_accounts(db_session, foreign_setup):
    """The audited escalation: include_all widens an admin's scope to every
    account, even ones outside their ownership/groups."""
    out = search_service.search_messages(
        db_session,
        user=foreign_setup["admin"],
        query="riservata",
        include_all=True,
    )
    assert [r["subject"] for r in out["results"]] == ["fattura riservata"]
    assert out["total"] == 1


def test_admin_without_include_all_stays_scoped(db_session, foreign_setup):
    """Privacy default: even admins only search their accessible accounts."""
    out = search_service.search_messages(
        db_session,
        user=foreign_setup["admin"],
        query="riservata",
    )
    assert out["results"] == []
    assert out["total"] == 0


def test_non_admin_include_all_is_ignored(db_session, foreign_setup):
    out = search_service.search_messages(
        db_session,
        user=foreign_setup["luigi"],
        query="riservata",
        include_all=True,
    )
    assert out["results"] == []
    assert out["total"] == 0


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


def test_results_include_attachments(db_session, search_setup):
    """Each hit carries attachment chip data ({filename, ext, size_bytes}) and
    the hex message_id_hash the preview pane needs to call the preview API."""
    acct = search_setup["account"]
    row = MailIndexMessage(
        account_id=acct.id,
        message_id_hash=b"\x04" * 20,
        message_id="<4@h>",
        subject="report allegato",
        from_addr="boss@ditta.it",
        from_name="Boss",
        date_sent=datetime.now(UTC),
        folder_path="INBOX",
        maildir_filename="4.host:2,",
        has_attachments=True,
    )
    db_session.add(row)
    db_session.add(
        MailIndexAttachment(
            account_id=acct.id,
            message_id_hash=b"\x04" * 20,
            part_index=2,
            filename="a.pdf",
            ext="pdf",
            size_bytes=10,
        )
    )
    db_session.commit()

    out = search_service.search_messages(
        db_session,
        user=search_setup["user"],
        query="allegato",
    )
    assert out["total"] == 1
    hit = out["results"][0]
    assert hit["has_attachments"] is True
    assert hit["attachments"] == [{"filename": "a.pdf", "ext": "pdf", "size_bytes": 10}]
    # bytes -> hex string contract, consumed by the preview endpoint URL
    assert hit["message_id_hash"] == row.message_id_hash.hex()


def test_attachment_enrichment_never_selects_content_text(db_session, search_setup):
    """The attachment chip fetch must use explicit column projection: once Tika
    populates content_text (up to 200 KB per row), a whole-entity fetch would
    drag the extracted text into every search page."""
    from sqlalchemy import event

    acct = search_setup["account"]
    db_session.add(
        MailIndexAttachment(
            account_id=acct.id,
            message_id_hash=b"\x01" * 20,
            part_index=2,
            filename="a.pdf",
            ext="pdf",
            size_bytes=10,
            content_text="huge extracted text",
        )
    )
    db_session.commit()

    statements: list[str] = []
    engine = db_session.get_bind()

    @event.listens_for(engine, "before_cursor_execute")
    def _capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    try:
        out = search_service.search_messages(db_session, user=search_setup["user"], query="fattura")
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    att_stmts = [s for s in statements if "mail_index.attachments" in s]
    assert att_stmts, "expected an attachment enrichment query"
    assert all("content_text" not in s for s in att_stmts)
    # the chips still come through
    by_subject = {r["subject"]: r for r in out["results"]}
    assert by_subject["fattura marzo"]["attachments"] == [
        {"filename": "a.pdf", "ext": "pdf", "size_bytes": 10}
    ]


def test_results_without_attachments_have_empty_list(db_session, search_setup):
    """has_attachments=False (the column default) yields attachments == [] so
    the UI can iterate without guarding for a missing key."""
    out = search_service.search_messages(
        db_session,
        user=search_setup["user"],
        query="hello",
    )
    hit = out["results"][0]
    assert hit["has_attachments"] is False
    assert hit["attachments"] == []


# ---------------------------------------------------------------------------
# search_attachments
# ---------------------------------------------------------------------------


@pytest.fixture
def att_setup(db_session, search_setup):
    """Attachment rows hanging off search_setup's messages.

    \\x01 "fattura marzo"  (now-2d):   Invoice_March.pdf  pdf  2 MB   part 2
    \\x02 "hello world"    (now-10d):  photo.jpg          jpg  500 KB part 1
                                       contract_scan.pdf  pdf  1234   part 3
                                       (content_text = secret clause...)
    \\x03 "old fattura"    (now-100d): invoice_old.zip    zip  NULL   part 1
    """
    acct = search_setup["account"]
    db_session.add_all(
        [
            MailIndexAttachment(
                account_id=acct.id,
                message_id_hash=b"\x01" * 20,
                part_index=2,
                filename="Invoice_March.pdf",
                ext="pdf",
                size_bytes=2_000_000,
            ),
            MailIndexAttachment(
                account_id=acct.id,
                message_id_hash=b"\x02" * 20,
                part_index=1,
                filename="photo.jpg",
                ext="jpg",
                size_bytes=500_000,
            ),
            MailIndexAttachment(
                account_id=acct.id,
                message_id_hash=b"\x02" * 20,
                part_index=3,
                filename="contract_scan.pdf",
                ext="pdf",
                size_bytes=1_234,
                content_text="the secret clause about payment terms",
            ),
            MailIndexAttachment(
                account_id=acct.id,
                message_id_hash=b"\x03" * 20,
                part_index=1,
                filename="invoice_old.zip",
                ext="zip",
                size_bytes=None,
            ),
        ]
    )
    db_session.commit()
    return search_setup


def test_attachment_search_filename_terms_are_anded(db_session, att_setup):
    """Whitespace-split terms must ALL match the filename (case-insensitive)."""
    out = search_service.search_attachments(db_session, user=att_setup["user"], query="invoice")
    # ordered by containing-message date DESC
    assert [r["filename"] for r in out["results"]] == ["Invoice_March.pdf", "invoice_old.zip"]
    assert out["total"] == 2

    out = search_service.search_attachments(
        db_session, user=att_setup["user"], query="invoice march"
    )
    assert [r["filename"] for r in out["results"]] == ["Invoice_March.pdf"]


def test_attachment_search_empty_query_returns_all_in_scope(db_session, att_setup):
    out = search_service.search_attachments(db_session, user=att_setup["user"], query="")
    assert out["total"] == 4


def test_attachment_hit_shape_and_snapshot_presence(db_session, att_setup):
    from mailfallback.models import SnapshotMessage

    acct = att_setup["account"]
    db_session.add(
        SnapshotMessage(snapshot_id="snapA", account_id=acct.id, message_id_hash=b"\x01" * 20)
    )
    db_session.commit()

    out = search_service.search_attachments(
        db_session, user=att_setup["user"], query="invoice march"
    )
    hit = out["results"][0]
    assert hit["account_id"] == acct.id
    assert hit["message_id_hash"] == (b"\x01" * 20).hex()  # hex contract
    assert hit["part_index"] == 2
    assert hit["filename"] == "Invoice_March.pdf"
    assert hit["ext"] == "pdf"
    assert hit["size_bytes"] == 2_000_000
    assert hit["content_snippet"] is None  # SQLite: no ts_headline
    assert hit["subject"] == "fattura marzo"
    assert hit["from_addr"] == "boss@ditta.it"
    assert hit["folder_path"] == "INBOX"
    assert hit["date_sent"] is not None
    assert hit["alive_in_live"] is True
    assert hit["snapshots"] == ["snapA"]
    assert hit["has_live_or_snapshot"] is True


def test_attachment_search_ext_filter(db_session, att_setup):
    out = search_service.search_attachments(
        db_session, user=att_setup["user"], query="", exts=["pdf"]
    )
    assert {r["filename"] for r in out["results"]} == {"Invoice_March.pdf", "contract_scan.pdf"}


def test_attachment_search_size_filters_exclude_null_sizes(db_session, att_setup):
    """size_bytes IS NULL rows are excluded by size filters but included
    otherwise (SQL comparison semantics, pinned on purpose)."""
    out = search_service.search_attachments(
        db_session, user=att_setup["user"], query="", min_size=1_000_000
    )
    assert [r["filename"] for r in out["results"]] == ["Invoice_March.pdf"]

    out = search_service.search_attachments(
        db_session, user=att_setup["user"], query="", max_size=600_000
    )
    assert {r["filename"] for r in out["results"]} == {"photo.jpg", "contract_scan.pdf"}

    out = search_service.search_attachments(db_session, user=att_setup["user"], query="")
    assert "invoice_old.zip" in {r["filename"] for r in out["results"]}


def test_attachment_search_range_filters_on_message_date(db_session, att_setup):
    """range_start/range_end filter on the joined message's date_sent with the
    SAME null-tolerant semantics as search_messages: NULL date_sent rows are
    kept in any range — a message whose Date: header didn't parse must not
    disappear from date-filtered searches."""
    acct = att_setup["account"]
    db_session.add(
        MailIndexMessage(
            account_id=acct.id,
            message_id_hash=b"\x04" * 20,
            message_id="<4@h>",
            subject="dateless",
            folder_path="INBOX",
            maildir_filename="4.host:2,",
        )
    )
    db_session.add(
        MailIndexAttachment(
            account_id=acct.id,
            message_id_hash=b"\x04" * 20,
            part_index=1,
            filename="dateless.pdf",
            ext="pdf",
            size_bytes=10,
        )
    )
    db_session.commit()
    now = datetime.now(UTC)

    # Open-ended range (the fixed chips: range_start only).
    out = search_service.search_attachments(
        db_session, user=att_setup["user"], query="", range_start=now - timedelta(days=7)
    )
    names = {r["filename"] for r in out["results"]}
    assert "Invoice_March.pdf" in names  # 2 days old — in range
    assert "photo.jpg" not in names  # 10 days old — out
    assert "contract_scan.pdf" not in names  # same 10-day-old message — out
    assert "invoice_old.zip" not in names  # 100 days old — out
    assert "dateless.pdf" in names  # NULL date_sent — kept in ANY range

    # Bounded range (the Custom… popover: both ends).
    out = search_service.search_attachments(
        db_session,
        user=att_setup["user"],
        query="",
        range_start=now - timedelta(days=30),
        range_end=now - timedelta(days=5),
    )
    names = {r["filename"] for r in out["results"]}
    assert names == {"photo.jpg", "contract_scan.pdf", "dateless.pdf"}


def test_attachment_content_search_sqlite_like_path(db_session, att_setup, monkeypatch):
    """include_content + tika_enabled: SQLite falls back to LIKE over
    filename OR content_text per term; snippet stays None (no ts_headline)."""
    from mailfallback.config import settings

    monkeypatch.setattr(settings, "tika_enabled", True)
    out = search_service.search_attachments(
        db_session, user=att_setup["user"], query="secret clause", include_content=True
    )
    assert [r["filename"] for r in out["results"]] == ["contract_scan.pdf"]
    assert out["results"][0]["content_snippet"] is None

    # without include_content the same query is filename-only -> no match
    out = search_service.search_attachments(
        db_session, user=att_setup["user"], query="secret clause", include_content=False
    )
    assert out["results"] == []
    assert out["total"] == 0


def test_attachment_include_content_ignored_when_tika_disabled(db_session, att_setup, monkeypatch):
    """copy-must-match-behavior: with Tika off, include_content=True must
    behave exactly like a filename-only search."""
    from mailfallback.config import settings

    monkeypatch.setattr(settings, "tika_enabled", False)
    out = search_service.search_attachments(
        db_session, user=att_setup["user"], query="secret clause", include_content=True
    )
    assert out["results"] == []
    assert out["total"] == 0

    # filename match still works with the flag on
    out = search_service.search_attachments(
        db_session, user=att_setup["user"], query="photo", include_content=True
    )
    assert [r["filename"] for r in out["results"]] == ["photo.jpg"]


def test_attachment_search_scoping(db_session, foreign_setup):
    """Foreign accounts are invisible by default; non-admin include_all is
    ignored; admin include_all widens to every account."""
    acct = foreign_setup["account"]
    db_session.add(
        MailIndexAttachment(
            account_id=acct.id,
            message_id_hash=b"\x0a" * 20,
            part_index=1,
            filename="riservato.pdf",
            ext="pdf",
            size_bytes=10,
        )
    )
    db_session.commit()

    out = search_service.search_attachments(
        db_session, user=foreign_setup["admin"], query="riservato"
    )
    assert out["results"] == [] and out["total"] == 0

    out = search_service.search_attachments(
        db_session, user=foreign_setup["luigi"], query="riservato", include_all=True
    )
    assert out["results"] == [] and out["total"] == 0

    out = search_service.search_attachments(
        db_session, user=foreign_setup["admin"], query="riservato", include_all=True
    )
    assert [r["filename"] for r in out["results"]] == ["riservato.pdf"]
    assert out["total"] == 1


def test_attachment_search_account_ids_narrowing(db_session, att_setup, foreign_setup):
    """account_ids narrows within the visible set; out-of-scope ids yield
    nothing instead of leaking."""
    out = search_service.search_attachments(
        db_session,
        user=att_setup["user"],
        query="",
        account_ids=[att_setup["account"].id],
    )
    assert out["total"] == 4

    out = search_service.search_attachments(
        db_session,
        user=att_setup["user"],
        query="",
        account_ids=[foreign_setup["account"].id],
    )
    assert out["results"] == [] and out["total"] == 0


def test_attachment_search_pagination(db_session, att_setup):
    out = search_service.search_attachments(
        db_session, user=att_setup["user"], query="", page=1, page_size=2
    )
    assert len(out["results"]) == 2
    assert out["total"] == 4
    assert out["page"] == 1
    assert out["page_size"] == 2


def test_attachment_pg_content_query_uses_fts_expression_verbatim(db_session, att_setup):
    """Compile-only pin of the GIN-index contract: PG matches expression
    indexes structurally, so the WHERE clause must contain
    models.ATTACHMENTS_FTS_EXPR verbatim. No PG server needed."""
    from sqlalchemy.dialects import postgresql

    from mailfallback.models import ATTACHMENTS_FTS_EXPR

    q = search_service._build_attachment_query(
        db_session,
        scope=[att_setup["account"].id],
        query="secret",
        content_mode=True,
        exts=None,
        min_size=None,
        max_size=None,
        dialect_name="postgresql",
    )
    sql = str(q.statement.compile(dialect=postgresql.dialect()))
    assert ATTACHMENTS_FTS_EXPR in sql
    assert "plainto_tsquery" in sql
    assert "ts_headline" in sql
    # tie ATTACHMENT_HEADLINE_OPTS to the call site: the marker contract must
    # appear in the fully rendered statement, not just in the constant
    literal_sql = str(
        q.statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )
    assert "StartSel=[[[" in literal_sql


def test_attachment_pg_empty_query_content_mode_skips_headline_columns(db_session, att_setup):
    """Empty query + content toggle ON is the default UI state once the toggle
    lands: the SELECT must not compute ts_headline/to_tsvector per row only to
    discard them in Python."""
    from sqlalchemy.dialects import postgresql

    q = search_service._build_attachment_query(
        db_session,
        scope=[att_setup["account"].id],
        query="",
        content_mode=True,
        exts=None,
        min_size=None,
        max_size=None,
        dialect_name="postgresql",
    )
    sql = str(q.statement.compile(dialect=postgresql.dialect()))
    assert "ts_headline" not in sql
    assert "to_tsvector" not in sql


def test_attachment_pg_filename_only_query_skips_fts_and_headline(db_session, att_setup):
    """Without content mode, PG uses plain ILIKE: no tsvector scan, no
    ts_headline evaluation cost."""
    from sqlalchemy.dialects import postgresql

    from mailfallback.models import ATTACHMENTS_FTS_EXPR

    q = search_service._build_attachment_query(
        db_session,
        scope=[att_setup["account"].id],
        query="secret",
        content_mode=False,
        exts=None,
        min_size=None,
        max_size=None,
        dialect_name="postgresql",
    )
    sql = str(q.statement.compile(dialect=postgresql.dialect()))
    assert ATTACHMENTS_FTS_EXPR not in sql
    assert "ts_headline" not in sql
    assert "ILIKE" in sql


def test_attachment_headline_marker_contract():
    """XSS contract (plan fact #8): snippets carry [[[ / ]]] markers the JS
    splits on to build text nodes + <mark> — never raw HTML."""
    assert (
        search_service.ATTACHMENT_HEADLINE_OPTS == "StartSel=[[[,StopSel=]]],MaxWords=18,MinWords=8"
    )


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


def test_dovecot_body_search_deadline_stops_between_fetch_batches(
    db_session, search_setup, monkeypatch
):
    """With >500 matched UIDs the FETCH is chunked; the deadline must be honoured
    between batches so a single huge folder can't blow past the soft timeout."""
    from mailfallback.services import search_service as svc

    uids = b" ".join(str(n).encode() for n in range(1, 601))  # 600 UIDs -> 2 batches
    fetched_batches = []

    class FakeConn:
        def select(self, target, readonly=True):
            return ("OK", [b"600"])

        def uid(self, *args):
            if args[0] == "SEARCH":
                return ("OK", [uids])
            if args[0] == "FETCH":
                fetched_batches.append(args[1])
                return ("OK", [(b"x (UID 1 ...", b"Message-ID: <2@h>\r\n"), b")"])
            return ("NO", [b""])

        def logout(self):
            pass

    class FakeTime:
        # account check, folder check, batch-0 check all pre-deadline; batch-1 trips.
        def __init__(self):
            self._seq = iter([0.0, 0.0, 0.0, 200.0])

        def monotonic(self):
            return next(self._seq, 200.0)

    monkeypatch.setattr(svc, "time", FakeTime())
    _install_fake_dovecot(monkeypatch, FakeConn())
    acct = search_setup["account"]
    _matched, partial = _dovecot_body_search(db_session, [acct.id], "hello", deadline=100.0)

    assert partial is True
    assert len(fetched_batches) == 1  # second batch skipped once the deadline passed


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


def test_dovecot_body_search_non_ascii_keyword_uses_utf8_literal(
    db_session, search_setup, monkeypatch
):
    """imaplib encodes command args as ASCII, so a keyword like "caffè" must be
    sent as a UTF-8 literal with CHARSET UTF-8 instead of an inline quoted
    string — otherwise the whole search 500s with UnicodeEncodeError."""
    from mailfallback.services.index_service import _hash_message_id

    search_calls = []

    class FakeConn:
        def __init__(self):
            self.literal = None

        def select(self, target, readonly=True):
            return ("OK", [b"1"])

        def uid(self, *args):
            for a in args:
                a.encode("ascii")  # raises UnicodeEncodeError like imaplib
            lit, self.literal = self.literal, None
            if args[0] == "SEARCH":
                search_calls.append((args, lit))
                return ("OK", [b"7"])
            if args[0] == "FETCH":
                return ("OK", [(b"1 (UID 7 ...", b"Message-ID: <2@h>\r\n"), b")"])
            return ("NO", [b""])

        def logout(self):
            pass

    _install_fake_dovecot(monkeypatch, FakeConn())
    acct = search_setup["account"]
    deadline = _time.monotonic() + 10
    matched, partial = _dovecot_body_search(db_session, [acct.id], "caffè", deadline)

    assert _hash_message_id("<2@h>") in matched
    assert partial is False
    assert search_calls == [(("SEARCH", "CHARSET", "UTF-8", "BODY"), "caffè".encode())]


def test_dovecot_body_search_swallows_per_folder_errors(db_session, search_setup, monkeypatch):
    """The docstring contract is that per-folder errors never fail the search,
    but an exception from SEARCH/FETCH must not propagate as a 500 either."""

    class FakeConn:
        def select(self, target, readonly=True):
            return ("OK", [b"1"])

        def uid(self, *args):
            raise RuntimeError("boom")

        def logout(self):
            pass

    _install_fake_dovecot(monkeypatch, FakeConn())
    acct = search_setup["account"]
    deadline = _time.monotonic() + 10
    matched, partial = _dovecot_body_search(db_session, [acct.id], "hello", deadline)

    assert matched == set()
    assert partial is False


@patch("mailfallback.services.search_service._dovecot_body_search")
def test_deep_search_unions_body_only_matches(mock_body, db_session, search_setup):
    """deep=True folds body-only matches (whose subject does NOT match the query)
    into the result set and flags them body_matched=True."""
    # "hello world" (hash \x02) does not match query "fattura" by subject,
    # but the body search returns it.
    mock_body.return_value = ({b"\x02" * 20}, False)

    result = search_service.search_messages(
        db_session,
        user=search_setup["user"],
        query="fattura",
        deep=True,
    )
    by_subject = {r["subject"]: r for r in result["results"]}
    # tsv matches + body-only union
    assert "fattura marzo" in by_subject
    assert "old fattura" in by_subject
    assert "hello world" in by_subject  # body-only, unioned in
    assert by_subject["hello world"]["body_matched"] is True
    assert by_subject["fattura marzo"]["body_matched"] is False
    assert result["partial"] is False


@patch("mailfallback.services.search_service._dovecot_body_search")
def test_deep_search_propagates_partial(mock_body, db_session, search_setup):
    mock_body.return_value = (set(), True)
    result = search_service.search_messages(
        db_session, user=search_setup["user"], query="fattura", deep=True
    )
    assert result["partial"] is True


def test_default_search_excludes_body_only_matches(db_session, search_setup):
    """deep defaults to False: a message that only matches by body is NOT
    returned and body_matched is None."""
    result = search_service.search_messages(db_session, user=search_setup["user"], query="fattura")
    by_subject = {r["subject"]: r for r in result["results"]}
    assert "hello world" not in by_subject
    assert by_subject["fattura marzo"]["body_matched"] is None
    assert "phase2_skipped_count" not in result
    assert result["partial"] is False


def test_deep_sanitises_crlf_in_keyword(db_session, search_setup, monkeypatch):
    """Deep search keyword sanitisation strips control chars (CRLF) so a
    malicious input can't break out of the IMAP quoted string."""
    captured_searches: list[tuple] = []

    class FakeConn:
        def select(self, *args, **kwargs):
            return ("OK", [b"0"])

        def uid(self, *args):
            captured_searches.append(args)
            return ("OK", [b""])

        def logout(self):
            pass

    def fake_connect(db, account):
        return FakeConn(), "_restore_test"

    def fake_delete_temp(db, username):
        pass

    monkeypatch.setattr("mailfallback.routers.restore._connect_dovecot_for_account", fake_connect)
    monkeypatch.setattr("mailfallback.routers.restore.account_namespace_prefix", lambda a: "")
    monkeypatch.setattr(
        "mailfallback.services.dovecot_auth.delete_temp_imap_user", fake_delete_temp
    )

    result = search_service.search_messages(
        db_session,
        user=search_setup["user"],
        query='evil"\r\nLOGOUT',
        deep=True,
    )
    assert result["total"] >= 0
    for args in captured_searches:
        joined = " ".join(str(a) for a in args)
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
