# Mail Index + Search API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace mount-based snapshot search with a persistent metadata index in PostgreSQL. Search hits the index (sub-100ms) and only escalates to Dovecot FTS for body content on the survivors.

**Architecture:** Schema `mail_index.*` with `messages` (one row per `(account_id, message_id_hash)`), `snapshot_messages` (join table, forward-only bits), `rebuild_status` (per-account watermark). Build pipeline hooks into `sync_worker` (header parse on new files) and `backup_worker` (bulk INSERT bits when restic snapshot completes). New `POST /api/restore/search` endpoint queries the index; existing `/workspace/search` becomes a deprecated wrapper. Feature flag `MAILFALLBACK_USE_INDEX_SEARCH` enables fallback to legacy mount path.

**Tech Stack:** Python 3.12+, SQLAlchemy, Alembic, FastAPI, PostgreSQL (tsvector + GIN), `email.parser.BytesHeaderParser`, restic CLI (json output), `imaplib` (Phase 2 body filter).

**Spec:** [`docs/superpowers/specs/2026-05-11-mail-index-search-api-design.md`](../specs/2026-05-11-mail-index-search-api-design.md)

---

## File Structure

**Created:**
- `src/mailfallback/services/index_service.py` — upsert_message_set, record_snapshot, prune_snapshot, backfill helpers
- `src/mailfallback/services/search_service.py` — search_messages (Phase 1 + Phase 2)
- `src/mailfallback/cli/__init__.py` — CLI package init
- `src/mailfallback/cli/index.py` — `mfb index status|rebuild-account|backfill-snapshots`
- `alembic/versions/014_mail_index_schema.py`
- `tests/test_index_service.py`
- `tests/test_search_service.py`
- `tests/test_cli_index.py`

**Modified:**
- `src/mailfallback/models.py` — add `MailIndexMessage`, `SnapshotMessage`, `MailIndexRebuildStatus` models
- `src/mailfallback/config.py` — add `use_index_search`, `search_body_candidate_cap`
- `src/mailfallback/services/sync_worker.py` — call `index_service.upsert_message_set` after successful sync
- `src/mailfallback/services/backup_worker.py` — call `index_service.record_snapshot` after successful backup, `index_service.prune_snapshot` for each removed snapshot id after prune
- `src/mailfallback/services/restic_service.py` — add `list_files(snapshot_id)` (restic ls --recursive --json) and `forget_with_pruned_ids` helpers
- `src/mailfallback/routers/restore.py` — add new `POST /api/restore/search`; rewrite existing `workspace_search` as deprecated wrapper that translates to the new search call
- `pyproject.toml` — add `[project.scripts] mfb = "mailfallback.cli:app"`
- `tests/conftest.py` — extend with helpers if needed (likely none new)

---

## Phase 0 — Schema + Models

### Task 1: Alembic migration 014 + SQLAlchemy models for `mail_index.*`

**Files:**
- Create: `alembic/versions/014_mail_index_schema.py`
- Modify: `src/mailfallback/models.py` (append new model classes after the existing `Recovery` class)
- Test: `tests/test_models.py` (append round-trip tests)

The project's `alembic-drift` pre-commit hook requires model + migration to land in one atomic commit. Bundle both.

- [ ] **Step 1: Write the failing model test**

Append to `tests/test_models.py`:

```python
def test_mail_index_message_round_trip(db_session, default_store):
    from mailfallback.models import Account, MailIndexMessage
    from datetime import UTC, datetime

    acct = Account(
        name="a",
        store=default_store,
        maildir_path="/data/mailboxes/a",
        imap_host="imap.example.com",
    )
    db_session.add(acct)
    db_session.commit()

    msg = MailIndexMessage(
        account_id=acct.id,
        message_id_hash=b"\x00" * 20,
        message_id="<abc@host>",
        date_sent=datetime(2026, 5, 11, 12, 0, tzinfo=UTC),
        from_addr="alice@example.com",
        from_name="Alice",
        subject="Hello",
        to_addrs=["bob@example.com"],
        folder_path="INBOX",
        maildir_filename="1234.M567.host:2,S",
        size_bytes=1024,
    )
    db_session.add(msg)
    db_session.commit()
    db_session.refresh(msg)

    assert msg.deleted_at is None
    assert msg.first_seen_at is not None
    assert msg.last_seen_at is not None
    assert msg.to_addrs == ["bob@example.com"]


def test_snapshot_message_round_trip(db_session, default_store):
    from mailfallback.models import Account, MailIndexMessage, SnapshotMessage

    acct = Account(
        name="a",
        store=default_store,
        maildir_path="/data/mailboxes/a",
        imap_host="imap.example.com",
    )
    db_session.add(acct)
    db_session.commit()

    msg = MailIndexMessage(
        account_id=acct.id,
        message_id_hash=b"\x01" * 20,
        message_id="<def@host>",
        folder_path="INBOX",
        maildir_filename="2345.host:2,",
    )
    db_session.add(msg)
    db_session.commit()

    snap = SnapshotMessage(
        snapshot_id="abc12345",
        account_id=acct.id,
        message_id_hash=b"\x01" * 20,
    )
    db_session.add(snap)
    db_session.commit()
    db_session.refresh(snap)

    assert snap.snapshot_id == "abc12345"


def test_rebuild_status_defaults(db_session, default_store):
    from mailfallback.models import Account, MailIndexRebuildStatus

    acct = Account(
        name="a",
        store=default_store,
        maildir_path="/data/mailboxes/a",
        imap_host="imap.example.com",
    )
    db_session.add(acct)
    db_session.commit()

    rs = MailIndexRebuildStatus(account_id=acct.id, state="idle")
    db_session.add(rs)
    db_session.commit()
    db_session.refresh(rs)

    assert rs.state == "idle"
    assert rs.last_indexed_at is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/koma/src/mailfallback
uv run pytest tests/test_models.py::test_mail_index_message_round_trip tests/test_models.py::test_snapshot_message_round_trip tests/test_models.py::test_rebuild_status_defaults -v
```

Expected: FAIL — `ImportError: cannot import name 'MailIndexMessage'`.

- [ ] **Step 3: Add the SQLAlchemy models**

In `src/mailfallback/models.py`, find the import block at the top and ensure these are present (add what's missing):

```python
from sqlalchemy import (
    JSON, ARRAY, BigInteger, Boolean, Column, DateTime, Enum, ForeignKey,
    Integer, LargeBinary, String, Table, Text, UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
```

(The import list may differ; add only the missing items: `LargeBinary`, `ARRAY`, and the `TSVECTOR` import.)

Then append at the end of the file:

```python
class MailIndexMessage(Base):
    """Per-account, per-message metadata index used by the search API.

    One row per (account_id, message_id_hash). Headers only — no body content.
    Body search uses Dovecot FTS on the survivors of a query against this index.
    """

    __tablename__ = "messages"
    __table_args__ = {"schema": "mail_index"}

    account_id = Column(
        String, ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
    )
    message_id_hash = Column(LargeBinary(20), primary_key=True)
    message_id = Column(Text, nullable=False)
    date_sent = Column(DateTime(timezone=True))
    from_addr = Column(Text)
    from_name = Column(Text)
    subject = Column(Text)
    to_addrs = Column(ARRAY(Text))
    folder_path = Column(Text, nullable=False)
    maildir_filename = Column(Text, nullable=False)
    size_bytes = Column(Integer)
    first_seen_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, server_default=text("now()"))
    last_seen_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, server_default=text("now()"))
    deleted_at = Column(DateTime(timezone=True))
    tsv = Column(TSVECTOR)


class SnapshotMessage(Base):
    """Join table: which messages exist in which restic snapshots.

    Forward-only at install: bits are set by backup_worker after each restic
    backup succeeds. CLI command `mfb index backfill-snapshots` populates the
    history retroactively.
    """

    __tablename__ = "snapshot_messages"
    __table_args__ = {"schema": "mail_index"}

    snapshot_id = Column(Text, primary_key=True)
    account_id = Column(String, primary_key=True)
    message_id_hash = Column(LargeBinary(20), primary_key=True)


class MailIndexRebuildStatus(Base):
    """Per-account watermark + state for the index lifecycle.

    states: idle | live_indexing | snap_backfilling | failed
    """

    __tablename__ = "rebuild_status"
    __table_args__ = {"schema": "mail_index"}

    account_id = Column(String, ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True)
    state = Column(Text, nullable=False, default="idle", server_default="idle")
    last_indexed_at = Column(DateTime(timezone=True))
    backfill_progress = Column(Integer)
    backfill_total = Column(Integer)
    last_error = Column(Text)
```

- [ ] **Step 4: Create the Alembic migration**

Create `alembic/versions/014_mail_index_schema.py`:

```python
"""mail_index schema with messages, snapshot_messages, rebuild_status

Revision ID: 014
Revises: 013
Create Date: 2026-05-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR

from alembic import op

revision: str = "014"
down_revision: str | Sequence[str] | None = "013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS mail_index")

    op.create_table(
        "messages",
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("message_id_hash", sa.LargeBinary(20), nullable=False),
        sa.Column("message_id", sa.Text(), nullable=False),
        sa.Column("date_sent", sa.DateTime(timezone=True), nullable=True),
        sa.Column("from_addr", sa.Text(), nullable=True),
        sa.Column("from_name", sa.Text(), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("to_addrs", ARRAY(sa.Text()), nullable=True),
        sa.Column("folder_path", sa.Text(), nullable=False),
        sa.Column("maildir_filename", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tsv", TSVECTOR(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("account_id", "message_id_hash"),
        schema="mail_index",
    )
    op.create_index("idx_messages_account_date", "messages", ["account_id", sa.text("date_sent DESC")], schema="mail_index")
    op.create_index("idx_messages_tsv", "messages", ["tsv"], postgresql_using="gin", schema="mail_index")
    op.create_index(
        "idx_messages_account_alive", "messages", ["account_id"],
        postgresql_where=sa.text("deleted_at IS NULL"), schema="mail_index",
    )

    # Trigger that recomputes tsv from subject + from_addr + from_name + to_addrs.
    op.execute("""
        CREATE FUNCTION mail_index.messages_tsv_trigger() RETURNS trigger AS $$
        BEGIN
            NEW.tsv := to_tsvector('simple',
                coalesce(NEW.subject, '') || ' ' ||
                coalesce(NEW.from_addr, '') || ' ' ||
                coalesce(NEW.from_name, '') || ' ' ||
                coalesce(array_to_string(NEW.to_addrs, ' '), '')
            );
            RETURN NEW;
        END
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER messages_tsv_update
        BEFORE INSERT OR UPDATE ON mail_index.messages
        FOR EACH ROW EXECUTE FUNCTION mail_index.messages_tsv_trigger();
    """)

    op.create_table(
        "snapshot_messages",
        sa.Column("snapshot_id", sa.Text(), nullable=False),
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("message_id_hash", sa.LargeBinary(20), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id", "message_id_hash"],
            ["mail_index.messages.account_id", "mail_index.messages.message_id_hash"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("snapshot_id", "account_id", "message_id_hash"),
        schema="mail_index",
    )
    op.create_index("idx_snapmsg_account_msg", "snapshot_messages", ["account_id", "message_id_hash"], schema="mail_index")

    op.create_table(
        "rebuild_status",
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default="idle"),
        sa.Column("last_indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("backfill_progress", sa.Integer(), nullable=True),
        sa.Column("backfill_total", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("account_id"),
        schema="mail_index",
    )


def downgrade() -> None:
    op.drop_table("rebuild_status", schema="mail_index")
    op.drop_table("snapshot_messages", schema="mail_index")
    op.execute("DROP TRIGGER IF EXISTS messages_tsv_update ON mail_index.messages")
    op.execute("DROP FUNCTION IF EXISTS mail_index.messages_tsv_trigger()")
    op.drop_index("idx_messages_account_alive", table_name="messages", schema="mail_index")
    op.drop_index("idx_messages_tsv", table_name="messages", schema="mail_index")
    op.drop_index("idx_messages_account_date", table_name="messages", schema="mail_index")
    op.drop_table("messages", schema="mail_index")
    op.execute("DROP SCHEMA IF EXISTS mail_index")
```

- [ ] **Step 5: SQLite test compatibility shim**

The `__table_args__ = {"schema": "mail_index"}` will fail on SQLite because SQLite doesn't have schemas. Tests use SQLite. Add a startup hook in `tests/conftest.py` to attach a temp DB as `mail_index`. Find the `db_session` fixture and BEFORE `Base.metadata.create_all(engine)`, add:

```python
    # SQLite doesn't have schemas — attach an in-memory DB as mail_index
    # so tables with schema="mail_index" resolve correctly during tests.
    from sqlalchemy import event
    @event.listens_for(engine, "connect")
    def attach_mail_index(dbapi_conn, _):
        dbapi_conn.execute("ATTACH DATABASE ':memory:' AS mail_index")
```

(If a `connect` listener already exists for foreign keys, append the ATTACH statement inside the same listener — don't add two listeners.)

- [ ] **Step 6: Run tests**

```bash
uv run pytest tests/test_models.py::test_mail_index_message_round_trip tests/test_models.py::test_snapshot_message_round_trip tests/test_models.py::test_rebuild_status_defaults -v
uv run pytest tests/test_alembic_sync.py -v
```

All must pass. The alembic-sync test verifies the model and migration agree.

- [ ] **Step 7: Commit**

```bash
git add alembic/versions/014_mail_index_schema.py \
        src/mailfallback/models.py \
        tests/test_models.py \
        tests/conftest.py
git commit -m "feat(mail_index): schema + models for messages/snapshot_messages/rebuild_status"
```

---

## Phase 1 — Index Service (build pipeline)

### Task 2: `index_service.upsert_message_set`

**Files:**
- Create: `src/mailfallback/services/index_service.py`
- Create: `tests/test_index_service.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_index_service.py`:

```python
"""Tests for index_service — Mail Index lifecycle."""
import os
import tempfile
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from mailfallback.models import Account, MailIndexMessage, MailIndexRebuildStatus
from mailfallback.services import index_service


@pytest.fixture
def maildir_account(db_session, default_store, tmp_path):
    """Account with a real on-disk Maildir at tmp_path."""
    acct = Account(
        name="a",
        store=default_store,
        maildir_path=str(tmp_path),
        imap_host="imap.example.com",
    )
    db_session.add(acct)
    db_session.commit()

    # Create INBOX/cur with two mails
    inbox_cur = tmp_path / "INBOX" / "cur"
    inbox_cur.mkdir(parents=True)

    (inbox_cur / "1234567890.M1.host:2,S").write_bytes(
        b"From: alice@example.com\r\n"
        b"Subject: Hello\r\n"
        b"Message-Id: <abc@host>\r\n"
        b"Date: Mon, 11 May 2026 12:00:00 +0000\r\n"
        b"To: bob@example.com\r\n"
        b"\r\n"
        b"body content here"
    )
    (inbox_cur / "1234567891.M2.host:2,").write_bytes(
        b"From: carol@example.com\r\n"
        b"Subject: Howdy\r\n"
        b"Message-Id: <def@host>\r\n"
        b"\r\n"
        b"another body"
    )
    return acct


def test_upsert_message_set_inserts_new_messages(db_session, maildir_account):
    n = index_service.upsert_message_set(db_session, maildir_account.id)
    assert n == 2

    msgs = db_session.query(MailIndexMessage).filter(
        MailIndexMessage.account_id == maildir_account.id
    ).all()
    assert len(msgs) == 2
    by_subject = {m.subject: m for m in msgs}
    assert "Hello" in by_subject
    assert by_subject["Hello"].from_addr == "alice@example.com"
    assert by_subject["Hello"].to_addrs == ["bob@example.com"]
    assert by_subject["Hello"].folder_path == "INBOX"


def test_upsert_message_set_marks_missing_as_deleted(db_session, maildir_account, tmp_path):
    index_service.upsert_message_set(db_session, maildir_account.id)
    # Remove the second file
    (tmp_path / "INBOX" / "cur" / "1234567891.M2.host:2,").unlink()

    index_service.upsert_message_set(db_session, maildir_account.id)
    deleted = (
        db_session.query(MailIndexMessage)
        .filter(MailIndexMessage.account_id == maildir_account.id)
        .filter(MailIndexMessage.deleted_at.is_not(None))
        .all()
    )
    assert len(deleted) == 1
    assert deleted[0].subject == "Howdy"


def test_upsert_message_set_updates_rebuild_status_watermark(db_session, maildir_account):
    index_service.upsert_message_set(db_session, maildir_account.id)
    rs = (
        db_session.query(MailIndexRebuildStatus)
        .filter(MailIndexRebuildStatus.account_id == maildir_account.id)
        .one()
    )
    assert rs.state == "idle"
    assert rs.last_indexed_at is not None
```

- [ ] **Step 2: Run tests — they fail**

```bash
uv run pytest tests/test_index_service.py -v
```

Expected: `ModuleNotFoundError: No module named 'mailfallback.services.index_service'`.

- [ ] **Step 3: Create the service**

Create `src/mailfallback/services/index_service.py`:

```python
"""Mail Index service — build + maintain the per-message metadata catalog.

Public functions:
- upsert_message_set(db, account_id) -> int
- record_snapshot(db, account_id, snapshot_id) -> int   (Task 3)
- prune_snapshot(db, snapshot_id) -> int                (Task 4)
- backfill_snapshots(db, account_id) -> Iterator        (Task 14/15)

The service owns reads from the live Maildir filesystem (header-only via
email.parser.BytesHeaderParser — body is never touched). Snapshot inspection
goes through restic_service.list_files (Task 14).
"""

import hashlib
import logging
import os
from datetime import UTC, datetime
from email import policy
from email.parser import BytesHeaderParser
from email.utils import getaddresses, parsedate_to_datetime

from sqlalchemy.orm import Session

from mailfallback.models import (
    Account,
    MailIndexMessage,
    MailIndexRebuildStatus,
)

logger = logging.getLogger(__name__)


def _hash_message_id(message_id: str) -> bytes:
    """SHA-1 of the bare Message-Id (without angle brackets)."""
    bare = message_id.strip().lstrip("<").rstrip(">")
    return hashlib.sha1(bare.encode("utf-8", errors="replace")).digest()


def _parse_headers(path: str) -> dict | None:
    """Read just the headers from a Maildir file. Returns None if no Message-Id."""
    parser = BytesHeaderParser(policy=policy.default)
    try:
        with open(path, "rb") as f:
            msg = parser.parse(f)
    except OSError:
        return None
    msgid = msg.get("Message-Id") or msg.get("Message-ID")
    if not msgid:
        return None
    msgid = str(msgid).strip()
    date_sent = None
    raw_date = msg.get("Date")
    if raw_date:
        try:
            date_sent = parsedate_to_datetime(str(raw_date))
            if date_sent and date_sent.tzinfo is None:
                date_sent = date_sent.replace(tzinfo=UTC)
        except (TypeError, ValueError):
            date_sent = None
    from_pair = getaddresses([str(msg.get("From", ""))])
    from_name, from_addr = (from_pair[0] if from_pair else ("", ""))
    to_addrs = [a for _, a in getaddresses([str(msg.get("To", ""))]) if a]
    return {
        "message_id": msgid,
        "message_id_hash": _hash_message_id(msgid),
        "date_sent": date_sent,
        "from_addr": from_addr or None,
        "from_name": from_name or None,
        "subject": str(msg.get("Subject", "")) or None,
        "to_addrs": to_addrs or None,
        "size_bytes": os.path.getsize(path),
    }


def _walk_maildir(maildir_root: str):
    """Yield (folder_path, filename, full_path) for every Maildir mail file."""
    for dirpath, _, filenames in os.walk(maildir_root):
        if os.path.basename(dirpath) not in ("cur", "new"):
            continue
        # folder_path is relative to maildir_root, with /cur or /new stripped.
        rel = os.path.relpath(dirpath, maildir_root)
        folder = os.path.dirname(rel) or "INBOX"
        # Normalise: top-level maildir's cur is INBOX
        if rel in ("cur", "new"):
            folder = "INBOX"
        for fn in filenames:
            yield folder, fn, os.path.join(dirpath, fn)


def upsert_message_set(db: Session, account_id: str) -> int:
    """Walk the account's live Maildir, upsert every mail's headers, mark
    rows missing-from-disk as deleted. Returns count of rows touched.
    """
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise ValueError(f"Account {account_id} not found")

    rs = db.query(MailIndexRebuildStatus).filter(
        MailIndexRebuildStatus.account_id == account_id
    ).first()
    if rs is None:
        rs = MailIndexRebuildStatus(account_id=account_id, state="live_indexing")
        db.add(rs)
    else:
        rs.state = "live_indexing"
    db.commit()

    seen_hashes: set[bytes] = set()
    touched = 0
    try:
        for folder, filename, full_path in _walk_maildir(account.maildir_path):
            parsed = _parse_headers(full_path)
            if not parsed:
                continue
            seen_hashes.add(parsed["message_id_hash"])
            existing = db.query(MailIndexMessage).filter(
                MailIndexMessage.account_id == account_id,
                MailIndexMessage.message_id_hash == parsed["message_id_hash"],
            ).first()
            now = datetime.now(UTC)
            if existing:
                existing.last_seen_at = now
                existing.deleted_at = None
                existing.folder_path = folder
                existing.maildir_filename = filename
            else:
                db.add(MailIndexMessage(
                    account_id=account_id,
                    folder_path=folder,
                    maildir_filename=filename,
                    **parsed,
                ))
            touched += 1
        # Mark missing rows as deleted
        alive = db.query(MailIndexMessage).filter(
            MailIndexMessage.account_id == account_id,
            MailIndexMessage.deleted_at.is_(None),
        ).all()
        now = datetime.now(UTC)
        for row in alive:
            if row.message_id_hash not in seen_hashes:
                row.deleted_at = now
                touched += 1
        rs.state = "idle"
        rs.last_indexed_at = now
        rs.last_error = None
    except Exception as e:
        rs.state = "failed"
        rs.last_error = str(e)
        logger.exception("upsert_message_set failed for %s", account_id)
        raise
    finally:
        db.commit()
    return touched
```

- [ ] **Step 4: Run tests — they pass**

```bash
uv run pytest tests/test_index_service.py -v
```

All three tests must pass.

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/services/index_service.py tests/test_index_service.py
git commit -m "feat(mail_index): upsert_message_set walks live Maildir, parses headers"
```

---

### Task 3: `index_service.record_snapshot`

**Files:**
- Modify: `src/mailfallback/services/index_service.py`
- Modify: `tests/test_index_service.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_index_service.py`:

```python
def test_record_snapshot_inserts_bits_for_alive_messages(db_session, maildir_account):
    from mailfallback.models import SnapshotMessage

    index_service.upsert_message_set(db_session, maildir_account.id)
    n = index_service.record_snapshot(db_session, maildir_account.id, "snap00001")
    assert n == 2

    bits = db_session.query(SnapshotMessage).filter(
        SnapshotMessage.snapshot_id == "snap00001",
        SnapshotMessage.account_id == maildir_account.id,
    ).all()
    assert len(bits) == 2


def test_record_snapshot_idempotent(db_session, maildir_account):
    from mailfallback.models import SnapshotMessage

    index_service.upsert_message_set(db_session, maildir_account.id)
    index_service.record_snapshot(db_session, maildir_account.id, "snap00001")
    n = index_service.record_snapshot(db_session, maildir_account.id, "snap00001")
    assert n == 0  # nothing new

    bits = db_session.query(SnapshotMessage).filter(
        SnapshotMessage.snapshot_id == "snap00001"
    ).count()
    assert bits == 2  # still 2, not 4


def test_record_snapshot_excludes_deleted_messages(db_session, maildir_account, tmp_path):
    from mailfallback.models import SnapshotMessage

    index_service.upsert_message_set(db_session, maildir_account.id)
    (tmp_path / "INBOX" / "cur" / "1234567891.M2.host:2,").unlink()
    index_service.upsert_message_set(db_session, maildir_account.id)
    # Now one message is deleted_at-set

    index_service.record_snapshot(db_session, maildir_account.id, "snap00002")
    bits = db_session.query(SnapshotMessage).filter(
        SnapshotMessage.snapshot_id == "snap00002"
    ).count()
    assert bits == 1  # only the alive one
```

- [ ] **Step 2: Run tests — they fail**

```bash
uv run pytest tests/test_index_service.py::test_record_snapshot_inserts_bits_for_alive_messages tests/test_index_service.py::test_record_snapshot_idempotent tests/test_index_service.py::test_record_snapshot_excludes_deleted_messages -v
```

Expected: `AttributeError: module 'mailfallback.services.index_service' has no attribute 'record_snapshot'`.

- [ ] **Step 3: Implement `record_snapshot`**

Append to `src/mailfallback/services/index_service.py`:

```python
def record_snapshot(db: Session, account_id: str, snapshot_id: str) -> int:
    """Bulk INSERT a snapshot_messages row for every alive message in the
    account. Idempotent via INSERT ... ON CONFLICT DO NOTHING.
    Returns count of rows actually inserted.
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from mailfallback.models import MailIndexMessage, SnapshotMessage

    alive = db.query(MailIndexMessage.message_id_hash).filter(
        MailIndexMessage.account_id == account_id,
        MailIndexMessage.deleted_at.is_(None),
    ).all()
    if not alive:
        return 0

    rows = [
        {"snapshot_id": snapshot_id, "account_id": account_id, "message_id_hash": h[0]}
        for h in alive
    ]
    # Use ON CONFLICT for idempotency. SQLite supports the same syntax via
    # sqlalchemy.dialects.sqlite.insert; use the PG one and SQLite tolerates it
    # (sqlalchemy dispatches by dialect).
    if db.bind.dialect.name == "postgresql":
        stmt = pg_insert(SnapshotMessage).values(rows).on_conflict_do_nothing()
    else:
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        stmt = sqlite_insert(SnapshotMessage).values(rows).on_conflict_do_nothing()
    result = db.execute(stmt)
    db.commit()
    return result.rowcount or 0
```

- [ ] **Step 4: Run tests — they pass**

```bash
uv run pytest tests/test_index_service.py -v
```

All five tests must pass.

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/services/index_service.py tests/test_index_service.py
git commit -m "feat(mail_index): record_snapshot — bulk INSERT bits for alive messages"
```

---

### Task 4: `index_service.prune_snapshot`

**Files:**
- Modify: `src/mailfallback/services/index_service.py`
- Modify: `tests/test_index_service.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_index_service.py`:

```python
def test_prune_snapshot_removes_only_target_snapshot_bits(db_session, maildir_account):
    from mailfallback.models import SnapshotMessage

    index_service.upsert_message_set(db_session, maildir_account.id)
    index_service.record_snapshot(db_session, maildir_account.id, "snapA")
    index_service.record_snapshot(db_session, maildir_account.id, "snapB")
    assert db_session.query(SnapshotMessage).count() == 4

    n = index_service.prune_snapshot(db_session, "snapA")
    assert n == 2

    remaining = db_session.query(SnapshotMessage).all()
    assert all(b.snapshot_id == "snapB" for b in remaining)


def test_prune_snapshot_idempotent_for_unknown_id(db_session):
    n = index_service.prune_snapshot(db_session, "nonexistent")
    assert n == 0
```

- [ ] **Step 2: Run tests — they fail**

```bash
uv run pytest tests/test_index_service.py::test_prune_snapshot_removes_only_target_snapshot_bits tests/test_index_service.py::test_prune_snapshot_idempotent_for_unknown_id -v
```

Expected: `AttributeError: ... 'prune_snapshot'`.

- [ ] **Step 3: Implement**

Append to `src/mailfallback/services/index_service.py`:

```python
def prune_snapshot(db: Session, snapshot_id: str) -> int:
    """DELETE all snapshot_messages rows for the given snapshot_id. Returns count."""
    from mailfallback.models import SnapshotMessage

    deleted = (
        db.query(SnapshotMessage)
        .filter(SnapshotMessage.snapshot_id == snapshot_id)
        .delete(synchronize_session=False)
    )
    db.commit()
    return deleted
```

- [ ] **Step 4: Run tests — they pass**

```bash
uv run pytest tests/test_index_service.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/services/index_service.py tests/test_index_service.py
git commit -m "feat(mail_index): prune_snapshot — DELETE bits for a given snapshot_id"
```

---

## Phase 2 — Hooks into Existing Services

### Task 5: `sync_worker` post-sync hook

**Files:**
- Modify: `src/mailfallback/services/sync_worker.py`
- Test: `tests/test_sync_worker.py` (extend existing)

- [ ] **Step 1: Write the failing test**

Find an existing test in `tests/test_sync_worker.py` that exercises a successful sync and add this test in the same file:

```python
@patch("mailfallback.services.sync_worker.index_service")
@patch("mailfallback.services.sync_worker.subprocess.run")
def test_sync_worker_calls_index_service_after_success(
    mock_run, mock_index, db_session, default_store
):
    """A successful mbsync run triggers an index update."""
    from mailfallback.models import Account, AuthType, SyncJob, JobStatus
    from mailfallback.services import sync_worker

    acct = Account(
        name="a",
        store=default_store,
        maildir_path="/data/mailboxes/a",
        imap_host="imap.example.com",
        imap_user="u",
        imap_password=b"x",
        auth_type=AuthType.password,
    )
    db_session.add(acct)
    db_session.commit()

    job = SyncJob(account_id=acct.id, source="manual", status=JobStatus.queued)
    db_session.add(job)
    db_session.commit()

    mock_run.return_value = type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    sync_worker.execute_sync_job(db_session, job.id)

    mock_index.upsert_message_set.assert_called_once_with(db_session, acct.id)
```

(If your existing tests stub `subprocess.run` differently, mirror the pattern they use. The key assertion is that `index_service.upsert_message_set` was called once with the account id after the simulated successful sync.)

- [ ] **Step 2: Run test — it fails**

```bash
uv run pytest tests/test_sync_worker.py::test_sync_worker_calls_index_service_after_success -v
```

Expected: `AttributeError: module 'mailfallback.services.sync_worker' has no attribute 'index_service'`.

- [ ] **Step 3: Wire the hook**

In `src/mailfallback/services/sync_worker.py`, add the import near the top:

```python
from mailfallback.services import index_service
```

Find the `if result_code == 0:` block (around line 168 after the recent edits — search for `account.last_error = None`). After the existing `cleanup_old_jobs` try/except block but before the closing of the `if`, append:

```python
            try:
                index_service.upsert_message_set(db, account.id)
            except Exception:
                logger.warning("Mail index upsert failed for %s", account.name, exc_info=True)
```

The try/except is critical — index errors must not fail syncs.

- [ ] **Step 4: Run test — it passes**

```bash
uv run pytest tests/test_sync_worker.py -v
```

All sync_worker tests must pass (the new one + all pre-existing).

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/services/sync_worker.py tests/test_sync_worker.py
git commit -m "feat(sync): post-sync hook calls index_service.upsert_message_set"
```

---

### Task 6: `backup_worker` post-backup + post-prune hooks

**Files:**
- Modify: `src/mailfallback/services/backup_worker.py`
- Modify: `src/mailfallback/services/restic_service.py` (extract pruned snapshot ids)
- Test: `tests/test_backup_worker.py` (extend existing)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_backup_worker.py`:

```python
@patch("mailfallback.services.backup_worker.index_service")
@patch("mailfallback.services.backup_worker.restic_service")
def test_backup_worker_calls_record_snapshot_after_success(
    mock_restic, mock_index, db_session, account_backup
):
    """After a successful restic backup, record_snapshot is called with the new snapshot id."""
    mock_restic.init_repo.return_value = True
    mock_restic.run_backup.return_value = {
        "snapshot_id": "abc12345",
        "files_new": 5,
        "files_changed": 0,
        "data_added": 1024,
    }
    mock_restic.apply_retention.return_value = {"pruned": False, "removed_snapshot_ids": []}
    mock_restic.list_snapshots.return_value = []

    from mailfallback.services.backup_worker import execute_backup
    execute_backup(db_session, account_backup.id)

    mock_index.record_snapshot.assert_called_once_with(
        db_session, account_backup.account_id, "abc12345"
    )


@patch("mailfallback.services.backup_worker.index_service")
@patch("mailfallback.services.backup_worker.restic_service")
def test_backup_worker_calls_prune_snapshot_for_each_removed(
    mock_restic, mock_index, db_session, account_backup
):
    """When apply_retention prunes snapshots, prune_snapshot is called for each id."""
    mock_restic.init_repo.return_value = True
    mock_restic.run_backup.return_value = {"snapshot_id": "new00001"}
    mock_restic.apply_retention.return_value = {
        "pruned": True,
        "removed_snapshot_ids": ["old00001", "old00002"],
    }
    mock_restic.list_snapshots.return_value = []

    from mailfallback.services.backup_worker import execute_backup
    execute_backup(db_session, account_backup.id)

    assert mock_index.prune_snapshot.call_count == 2
    mock_index.prune_snapshot.assert_any_call(db_session, "old00001")
    mock_index.prune_snapshot.assert_any_call(db_session, "old00002")
```

- [ ] **Step 2: Run tests — they fail**

```bash
uv run pytest tests/test_backup_worker.py -v
```

Expected: `AttributeError ... 'index_service'` and the `apply_retention.return_value` keys don't match what the implementation expects yet.

- [ ] **Step 3: Update `restic_service.apply_retention` to surface removed snapshot IDs**

Open `src/mailfallback/services/restic_service.py` and find `apply_retention`. The current implementation returns whatever restic outputs; we need to also extract the list of removed snapshot IDs from the JSON output. After the existing `_run_restic([... "forget --prune", *retention_args ...])` call:

```python
def apply_retention(
    destination: Repository,
    account_id: str,
    retention_preset: str | None = None,
    custom_args: list[str] | None = None,
) -> dict:
    """Apply retention policy using restic forget --prune.

    Returns dict with at least: pruned (bool), removed_snapshot_ids (list[str]).
    """
    env = build_env(destination, account_id)
    retention_args = get_retention_args(retention_preset, custom_args)
    if not retention_args:
        return {"pruned": False, "removed_snapshot_ids": []}

    result = _run_restic(
        ["forget", "--prune", "--json", *retention_args],
        env,
        _is_insecure(destination),
    )
    removed_ids: list[str] = []
    if result.returncode == 0 and result.stdout:
        # restic forget --json outputs a list of objects; each has "remove" with
        # a list of {short_id, ...}. Be defensive: not all restic versions emit
        # the same keys. Parse line-by-line and tolerate non-json lines.
        import json
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line.startswith("[") and not line.startswith("{"):
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            entries = payload if isinstance(payload, list) else [payload]
            for entry in entries:
                for snap in entry.get("remove", []) or []:
                    sid = snap.get("short_id") or snap.get("id", "")[:8]
                    if sid:
                        removed_ids.append(sid)
    return {"pruned": result.returncode == 0, "removed_snapshot_ids": removed_ids}
```

(The previous return shape may have been simpler — keep any callers that consumed `pruned` working. Search for callers and adjust if needed:

```bash
grep -rn "apply_retention" src/ tests/
```)

- [ ] **Step 4: Wire the hooks in `backup_worker.execute_backup`**

In `src/mailfallback/services/backup_worker.py`, add the import:

```python
from mailfallback.services import index_service
```

Find the section that calls `restic_service.run_backup` (around line 80 area). After the call records `snapshot_id`, add:

```python
            snapshot_id = result.get("snapshot_id")
            if snapshot_id:
                try:
                    index_service.record_snapshot(db, backup.account_id, snapshot_id)
                except Exception:
                    logger.warning("record_snapshot failed for %s/%s",
                                   backup.account_id, snapshot_id, exc_info=True)
```

Then find the section that calls `restic_service.apply_retention`. After the call, iterate the removed ids:

```python
            retention_result = restic_service.apply_retention(
                backup.destination, account.id,
                retention_preset=backup.retention_preset,
                custom_args=backup.retention_custom_args,
            )
            for removed_id in retention_result.get("removed_snapshot_ids", []):
                try:
                    index_service.prune_snapshot(db, removed_id)
                except Exception:
                    logger.warning("prune_snapshot failed for %s", removed_id, exc_info=True)
```

(If your existing code already calls `apply_retention` with a slightly different signature, preserve that call shape and only add the prune loop after.)

- [ ] **Step 5: Run tests — they pass**

```bash
uv run pytest tests/test_backup_worker.py tests/test_restic_service.py -v 2>/dev/null || \
uv run pytest tests/test_backup_worker.py -v
```

All must pass.

- [ ] **Step 6: Commit**

```bash
git add src/mailfallback/services/backup_worker.py \
        src/mailfallback/services/restic_service.py \
        tests/test_backup_worker.py
git commit -m "feat(backup): post-backup record_snapshot + post-prune prune_snapshot hooks"
```

---

## Phase 3 — Search Service + API

### Task 7: `search_service.search_messages` (Phase 1, headers only)

**Files:**
- Create: `src/mailfallback/services/search_service.py`
- Create: `tests/test_search_service.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_search_service.py`:

```python
"""Tests for search_service — Phase 1 header search via mail_index."""
from datetime import UTC, datetime, timedelta

import pytest

from mailfallback.models import (
    Account, MailIndexMessage, SnapshotMessage, User, UserRole,
)
from mailfallback.security import hash_password
from mailfallback.services import index_service, search_service


@pytest.fixture
def search_setup(db_session, default_store, tmp_path):
    user = User(
        username="u", email="u@x", password_hash=hash_password("p"),
        role=UserRole.admin, enabled=True,
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
    db_session.add_all([
        MailIndexMessage(
            account_id=acct.id, message_id_hash=b"\x01" * 20, message_id="<1@h>",
            subject="fattura marzo", from_addr="boss@ditta.it", from_name="Boss",
            date_sent=now - timedelta(days=2),
            folder_path="INBOX", maildir_filename="1.host:2,",
        ),
        MailIndexMessage(
            account_id=acct.id, message_id_hash=b"\x02" * 20, message_id="<2@h>",
            subject="hello world", from_addr="bob@x", from_name="Bob",
            date_sent=now - timedelta(days=10),
            folder_path="INBOX", maildir_filename="2.host:2,",
        ),
        MailIndexMessage(
            account_id=acct.id, message_id_hash=b"\x03" * 20, message_id="<3@h>",
            subject="old fattura", from_addr="boss@ditta.it", from_name="Boss",
            date_sent=now - timedelta(days=100),
            folder_path="INBOX", maildir_filename="3.host:2,",
        ),
    ])
    db_session.commit()
    return {"user": user, "account": acct}


def test_search_returns_matching_subject(db_session, search_setup):
    result = search_service.search_messages(
        db_session, user=search_setup["user"], query="fattura",
    )
    subjects = [r["subject"] for r in result["results"]]
    assert "fattura marzo" in subjects
    assert "old fattura" in subjects
    assert "hello world" not in subjects


def test_search_filters_by_date_range(db_session, search_setup):
    now = datetime.now(UTC)
    result = search_service.search_messages(
        db_session, user=search_setup["user"], query="fattura",
        range_start=now - timedelta(days=7),
        range_end=now,
    )
    subjects = [r["subject"] for r in result["results"]]
    assert subjects == ["fattura marzo"]  # only the recent one


def test_search_respects_account_visibility(db_session, search_setup, default_store):
    # Create a separate account NOT owned by the user
    other = Account(name="o", store=default_store, maildir_path="/x", imap_host="i")
    db_session.add(other)
    db_session.flush()
    db_session.add(MailIndexMessage(
        account_id=other.id, message_id_hash=b"\x09" * 20, message_id="<9@h>",
        subject="fattura nascosta", folder_path="INBOX", maildir_filename="9",
    ))
    db_session.commit()

    result = search_service.search_messages(
        db_session, user=search_setup["user"], query="fattura",
    )
    assert "fattura nascosta" not in [r["subject"] for r in result["results"]]


def test_search_pagination(db_session, search_setup):
    page1 = search_service.search_messages(
        db_session, user=search_setup["user"], query="",
        page=1, page_size=2,
    )
    assert len(page1["results"]) == 2
    assert page1["total"] == 3
```

- [ ] **Step 2: Run tests — they fail**

```bash
uv run pytest tests/test_search_service.py -v
```

Expected: `ModuleNotFoundError: No module named 'mailfallback.services.search_service'`.

- [ ] **Step 3: Create the service**

Create `src/mailfallback/services/search_service.py`:

```python
"""Search service — query mail_index with optional Phase 2 body filter."""

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mailfallback.models import (
    Account,
    MailIndexMessage,
    SnapshotMessage,
    User,
    account_owners,
    account_groups,
    group_members,
)

logger = logging.getLogger(__name__)


def _accessible_account_ids(db: Session, user: User) -> list[str]:
    """Account IDs visible to the user via direct ownership OR group membership."""
    owned = (
        db.query(Account.id)
        .join(account_owners, account_owners.c.account_id == Account.id)
        .filter(account_owners.c.user_id == user.id)
    )
    via_groups = (
        db.query(Account.id)
        .join(account_groups, account_groups.c.account_id == Account.id)
        .join(group_members, group_members.c.group_id == account_groups.c.group_id)
        .filter(group_members.c.user_id == user.id)
    )
    return [r[0] for r in owned.union(via_groups).all()]


def search_messages(
    db: Session,
    *,
    user: User,
    query: str,
    account_ids: list[str] | None = None,
    range_start: datetime | None = None,
    range_end: datetime | None = None,
    include_deleted: bool = True,
    snapshot_id: str | None = None,
    body: bool = False,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    """Phase 1 (always): Postgres index query.
    Phase 2 (if body=True): Dovecot SEARCH body filter on candidates (Task 9).

    Returns: {results, total, page, page_size, phase2_skipped_count}
    """
    visible = _accessible_account_ids(db, user)
    if not visible:
        return {"results": [], "total": 0, "page": page, "page_size": page_size,
                "phase2_skipped_count": 0}
    if account_ids:
        scope = [a for a in account_ids if a in visible]
    else:
        scope = visible
    if not scope:
        return {"results": [], "total": 0, "page": page, "page_size": page_size,
                "phase2_skipped_count": 0}

    q = db.query(MailIndexMessage).filter(MailIndexMessage.account_id.in_(scope))
    if not include_deleted:
        q = q.filter(MailIndexMessage.deleted_at.is_(None))
    if range_start:
        q = q.filter(MailIndexMessage.date_sent >= range_start)
    if range_end:
        q = q.filter(MailIndexMessage.date_sent <= range_end)
    if snapshot_id:
        q = q.join(
            SnapshotMessage,
            (SnapshotMessage.account_id == MailIndexMessage.account_id)
            & (SnapshotMessage.message_id_hash == MailIndexMessage.message_id_hash),
        ).filter(SnapshotMessage.snapshot_id == snapshot_id)
    if query:
        # Use tsvector match on Postgres; fall back to ILIKE on SQLite (tests).
        if db.bind.dialect.name == "postgresql":
            q = q.filter(MailIndexMessage.tsv.op("@@")(func.plainto_tsquery("simple", query)))
        else:
            pat = f"%{query}%"
            q = q.filter(
                (MailIndexMessage.subject.ilike(pat))
                | (MailIndexMessage.from_addr.ilike(pat))
                | (MailIndexMessage.from_name.ilike(pat))
            )
    q = q.order_by(MailIndexMessage.date_sent.desc().nullslast())

    total = q.count()
    rows = q.offset((page - 1) * page_size).limit(page_size).all()

    # Build snapshot membership lookup for the result set
    if rows:
        hashes = [r.message_id_hash for r in rows]
        snap_rows = (
            db.query(SnapshotMessage.account_id, SnapshotMessage.message_id_hash, SnapshotMessage.snapshot_id)
            .filter(SnapshotMessage.message_id_hash.in_(hashes))
            .all()
        )
        snap_by_msg: dict[tuple[str, bytes], list[str]] = {}
        for acc, h, sid in snap_rows:
            snap_by_msg.setdefault((acc, h), []).append(sid)
    else:
        snap_by_msg = {}

    results = []
    for r in rows:
        results.append({
            "message_id": r.message_id,
            "account_id": r.account_id,
            "subject": r.subject,
            "from_addr": r.from_addr,
            "from_name": r.from_name,
            "to_addrs": r.to_addrs or [],
            "date_sent": r.date_sent.isoformat() if r.date_sent else None,
            "folder_path": r.folder_path,
            "alive_in_live": r.deleted_at is None,
            "snapshots": sorted(snap_by_msg.get((r.account_id, r.message_id_hash), [])),
            "body_matched": None,  # Phase 2 fills this in (Task 9)
        })

    return {"results": results, "total": total, "page": page, "page_size": page_size,
            "phase2_skipped_count": 0}
```

- [ ] **Step 4: Run tests — they pass**

```bash
uv run pytest tests/test_search_service.py -v
```

All four tests must pass.

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/services/search_service.py tests/test_search_service.py
git commit -m "feat(search): search_service Phase 1 — Postgres index query with auth + filters"
```

---

### Task 8: `POST /api/restore/search` endpoint

**Files:**
- Modify: `src/mailfallback/routers/restore.py`
- Test: `tests/test_restore_workspace_router.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_restore_workspace_router.py`:

```python
def test_api_restore_search_endpoint(client, db_session, default_store, login_user):
    from mailfallback.models import Account, MailIndexMessage

    acct = Account(
        name="a", store=default_store,
        maildir_path="/x", imap_host="i",
    )
    db_session.add(acct)
    db_session.flush()
    acct.owners.append(login_user)
    db_session.add(MailIndexMessage(
        account_id=acct.id, message_id_hash=b"\x10" * 20, message_id="<10@h>",
        subject="invoice from acme",
        folder_path="INBOX", maildir_filename="1",
    ))
    db_session.commit()

    login = client.post("/api/auth/login", json={"username": "koma", "password": "x"})
    assert login.status_code in (200, 303)

    resp = client.post("/api/restore/search", json={"query": "invoice"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["results"][0]["subject"] == "invoice from acme"
```

- [ ] **Step 2: Run test — it fails**

```bash
uv run pytest tests/test_restore_workspace_router.py::test_api_restore_search_endpoint -v
```

Expected: 404 — endpoint not defined.

- [ ] **Step 3: Add the endpoint**

In `src/mailfallback/routers/restore.py`, near the `workspace_search` endpoint, add:

```python
from mailfallback.services import search_service


class RestoreSearchRequest(BaseModel):
    query: str = ""
    account_ids: list[str] | None = None
    range_start: datetime | None = None
    range_end: datetime | None = None
    include_deleted: bool = True
    snapshot_id: str | None = None
    body: bool = False
    page: int = 1
    page_size: int = 50


@router.post("/search")
def api_restore_search(
    req: RestoreSearchRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return search_service.search_messages(
        db,
        user=user,
        query=req.query,
        account_ids=req.account_ids,
        range_start=req.range_start,
        range_end=req.range_end,
        include_deleted=req.include_deleted,
        snapshot_id=req.snapshot_id,
        body=req.body,
        page=req.page,
        page_size=req.page_size,
    )
```

- [ ] **Step 4: Run test — it passes**

```bash
uv run pytest tests/test_restore_workspace_router.py::test_api_restore_search_endpoint -v
```

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/routers/restore.py tests/test_restore_workspace_router.py
git commit -m "feat(api): POST /api/restore/search endpoint (cross-account, paginated)"
```

---

### Task 9: Phase 2 body filter via Dovecot SEARCH

**Files:**
- Modify: `src/mailfallback/services/search_service.py`
- Modify: `tests/test_search_service.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_search_service.py`:

```python
from unittest.mock import MagicMock, patch


@patch("mailfallback.services.search_service._dovecot_filter_body")
def test_phase2_body_filter_marks_survivors(mock_filter, db_session, search_setup):
    """When body=True, _dovecot_filter_body returns a subset of message_id_hashes
    that match the body keyword. search_messages flags those as body_matched=True."""
    # Mock returns: only the first message hash matches body
    mock_filter.return_value = {b"\x01" * 20}

    result = search_service.search_messages(
        db_session, user=search_setup["user"], query="fattura", body=True,
    )
    by_subject = {r["subject"]: r for r in result["results"]}
    assert by_subject["fattura marzo"]["body_matched"] is True
    assert by_subject["old fattura"]["body_matched"] is False
```

- [ ] **Step 2: Run test — it fails**

```bash
uv run pytest tests/test_search_service.py::test_phase2_body_filter_marks_survivors -v
```

Expected: AttributeError on `_dovecot_filter_body`.

- [ ] **Step 3: Add Phase 2**

In `src/mailfallback/services/search_service.py`, append:

```python
def _dovecot_filter_body(
    db: Session,
    account_id: str,
    candidates: list[tuple[bytes, str, str, str]],  # (hash, folder, maildir_filename, message_id)
    keyword: str,
) -> set[bytes]:
    """Return the subset of candidate message_id_hash values whose body matches keyword.

    Implementation: open one IMAP connection per account, SELECT each folder
    that has candidates, run SEARCH UID <id_list> BODY "<keyword>", collect UIDs
    that came back, and reverse-map to message_id_hash via the maildir_filename.

    Errors here MUST NOT fail the whole search — return an empty set on any
    Dovecot failure (Phase 1 results still ship).
    """
    if not candidates:
        return set()
    # Lazy imports to avoid pulling IMAP machinery into pure-DB tests.
    from mailfallback.models import Account
    from mailfallback.routers.restore import (
        _connect_dovecot_for_account,
        account_namespace_prefix,
    )
    from mailfallback.services.dovecot_auth import delete_temp_imap_user

    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        return set()
    matched: set[bytes] = set()
    try:
        conn, temp_user = _connect_dovecot_for_account(db, account)
    except Exception:
        logger.warning("Phase 2: Dovecot connect failed for %s", account_id, exc_info=True)
        return set()
    try:
        ns = account_namespace_prefix(account)
        # Group candidates by folder
        by_folder: dict[str, list[tuple[bytes, str]]] = {}
        for h, folder, filename, _msgid in candidates:
            by_folder.setdefault(folder, []).append((h, filename))
        # Per-candidate flow: for each (hash, message_id) in our candidates,
        # SEARCH HEADER Message-Id "<id>" to get the UID, then SEARCH UID <uid>
        # BODY "<keyword>" to confirm body match. Two SEARCH calls per candidate;
        # bounded by the Phase 1 cap (default 500). Acceptable cost — runs only
        # when user explicitly opts into body=True.
        msgid_by_hash = {h: msgid for h, _, _, msgid in candidates}
        for folder, items in by_folder.items():
            target = f'"{ns}{folder}"'
            typ, _ = conn.select(target, readonly=True)
            if typ != "OK":
                continue
            for h, _filename in items:
                msgid = msgid_by_hash.get(h)
                if not msgid:
                    continue
                quoted_id = msgid.replace('"', '').replace('\\', '')
                typ, data = conn.uid("SEARCH", "HEADER", "Message-Id", f'"{quoted_id}"')
                if typ != "OK" or not data or not data[0]:
                    continue
                uids = data[0].decode().split()
                if not uids:
                    continue
                uid = uids[0]
                quoted_kw = keyword.replace('"', '').replace('\\', '')
                typ, data = conn.uid("SEARCH", "UID", uid, "BODY", f'"{quoted_kw}"')
                if typ == "OK" and data and data[0]:
                    if uid in data[0].decode().split():
                        matched.add(h)
    finally:
        try:
            conn.logout()
        except Exception:
            pass
        try:
            delete_temp_imap_user(db, temp_user)
        except Exception:
            pass
    return matched
```

Then update `search_messages` to call Phase 2 when `body=True`. Find the `for r in rows: ...` results-building block and replace with this version that runs the body filter first:

```python
    body_matched_set: set[bytes] = set()
    phase2_skipped = 0
    if body and query and rows:
        # Build candidates per account
        from mailfallback.config import settings
        cap = getattr(settings, "search_body_candidate_cap", 500)
        if len(rows) > cap:
            phase2_skipped = len(rows) - cap
            candidates_rows = rows[:cap]
        else:
            candidates_rows = rows
        by_account: dict[str, list[tuple[bytes, str, str, str]]] = {}
        for r in candidates_rows:
            if r.deleted_at is not None:
                continue  # snapshot-only — skip Phase 2 (documented v1 limit)
            by_account.setdefault(r.account_id, []).append(
                (r.message_id_hash, r.folder_path, r.maildir_filename, r.message_id)
            )
        for acc_id, cands in by_account.items():
            body_matched_set.update(_dovecot_filter_body(db, acc_id, cands, query))

    results = []
    for r in rows:
        results.append({
            "message_id": r.message_id,
            "account_id": r.account_id,
            "subject": r.subject,
            "from_addr": r.from_addr,
            "from_name": r.from_name,
            "to_addrs": r.to_addrs or [],
            "date_sent": r.date_sent.isoformat() if r.date_sent else None,
            "folder_path": r.folder_path,
            "alive_in_live": r.deleted_at is None,
            "snapshots": sorted(snap_by_msg.get((r.account_id, r.message_id_hash), [])),
            "body_matched": (r.message_id_hash in body_matched_set) if body else None,
        })

    return {"results": results, "total": total, "page": page, "page_size": page_size,
            "phase2_skipped_count": phase2_skipped}
```

- [ ] **Step 4: Run tests — they pass**

```bash
uv run pytest tests/test_search_service.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/services/search_service.py tests/test_search_service.py
git commit -m "feat(search): Phase 2 body filter via Dovecot SEARCH on Phase 1 survivors"
```

---

### Task 10: Deprecated wrapper for `/api/restore/workspace/search`

**Files:**
- Modify: `src/mailfallback/routers/restore.py` (rewrite existing `workspace_search`)
- Modify: `src/mailfallback/config.py` (add feature flag)
- Test: `tests/test_restore_workspace_router.py`

- [ ] **Step 1: Add the feature flag**

In `src/mailfallback/config.py`, find the `Settings` class and add:

```python
    use_index_search: bool = True
    search_body_candidate_cap: int = 500
```

- [ ] **Step 2: Write the failing test**

Append to `tests/test_restore_workspace_router.py`:

```python
def test_workspace_search_wrapper_uses_new_search_when_flag_on(
    client, db_session, default_store, login_user, monkeypatch
):
    """With use_index_search=True, /workspace/search is a thin wrapper that
    calls search_service and returns the legacy shape."""
    from mailfallback.config import settings
    from mailfallback.models import Account, MailIndexMessage

    monkeypatch.setattr(settings, "use_index_search", True)

    acct = Account(name="a", store=default_store, maildir_path="/x", imap_host="i")
    db_session.add(acct)
    db_session.flush()
    acct.owners.append(login_user)
    db_session.add(MailIndexMessage(
        account_id=acct.id, message_id_hash=b"\x20" * 20, message_id="<20@h>",
        subject="alpha keyword", folder_path="INBOX", maildir_filename="1",
    ))
    db_session.commit()

    login = client.post("/api/auth/login", json={"username": "koma", "password": "x"})
    assert login.status_code in (200, 303)

    resp = client.post("/api/restore/workspace/search", json={
        "account_id": acct.id,
        "query": "alpha",
        "range_start": "2026-01-01T00:00:00Z",
        "range_end": "2026-12-31T23:59:59Z",
        "include_live": True,
        "include_snapshots": True,
    })
    assert resp.status_code == 200
    body = resp.json()
    # Legacy shape: {results: [...], mounted_snapshots: [...]}
    assert "results" in body
    assert "mounted_snapshots" in body
    assert any(r["subject"] == "alpha keyword" for r in body["results"])
```

- [ ] **Step 3: Run test — it fails**

```bash
uv run pytest tests/test_restore_workspace_router.py::test_workspace_search_wrapper_uses_new_search_when_flag_on -v
```

Expected: probably passes if the existing `workspace_search` happens to find the message via Dovecot — but the test probably FAILS because the existing path mounts snapshots and there are no snapshots, so it returns the message via live SELECT (which works). To force the FAIL, you can also assert that `search_service.search_messages` was called: add `from unittest.mock import patch` and wrap the test with `@patch("mailfallback.routers.restore.search_service")` and assert call. Adjust the test to assert the call.

The simpler version: just verify the response shape and content. Skip the call-assert if the existing endpoint already passes by coincidence; the rewrite happens in step 4 regardless.

- [ ] **Step 4: Rewrite the wrapper**

In `src/mailfallback/routers/restore.py`, find the existing `workspace_search` function. REPLACE its body with:

```python
@router.post("/workspace/search")
def workspace_search(
    req: WorkspaceSearchRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """DEPRECATED — use POST /api/restore/search.

    Translates the legacy single-account request to the new search_service
    call and returns the legacy {results, mounted_snapshots} shape so the
    pre-cycle-2 UI keeps working.
    """
    if not settings.use_index_search:
        return _legacy_mount_workspace_search(req, request, user, db)

    # Build criteria fields list as before (subject/from/to/body)
    criteria: list[str] = []
    if req.search_subject: criteria.append("SUBJECT")
    if req.search_from: criteria.append("FROM")
    if req.search_to: criteria.append("TO")
    body = req.search_body or "BODY" in criteria

    new_result = search_service.search_messages(
        db,
        user=user,
        query=req.query,
        account_ids=[req.account_id],
        range_start=req.range_start,
        range_end=req.range_end,
        include_deleted=req.include_snapshots,
        body=body,
        page=1,
        page_size=200,
    )
    legacy_results = []
    for r in new_result["results"]:
        legacy_results.append({
            "message_id": r["message_id"],
            "subject": r["subject"],
            "from": r["from_addr"] or "",
            "folder": r["folder_path"],
            "sources": (["live"] if r["alive_in_live"] else []) + r["snapshots"],
            "locations": [{
                "source": "live" if r["alive_in_live"] else (r["snapshots"][0] if r["snapshots"] else "?"),
                "namespace": "",
                "folder": r["folder_path"],
                "uid": None,  # legacy uid not available from index — restore via Message-Id
            }],
        })
    return {"results": legacy_results, "mounted_snapshots": []}


def _legacy_mount_workspace_search(req, request, user, db):
    """The pre-index-search implementation, kept behind the use_index_search=False
    feature flag for fallback during rollout."""
    # The OLD body of workspace_search goes here. Move the existing
    # implementation into this function verbatim.
    ...
```

Take the OLD body of `workspace_search` (the one that mounts snapshots and runs cross-namespace IMAP SEARCH) and move it verbatim into `_legacy_mount_workspace_search`. The new code path delegates to `search_service`.

- [ ] **Step 5: Run test — it passes**

```bash
uv run pytest tests/test_restore_workspace_router.py -v
```

All workspace tests must pass.

- [ ] **Step 6: Commit**

```bash
git add src/mailfallback/routers/restore.py \
        src/mailfallback/config.py \
        tests/test_restore_workspace_router.py
git commit -m "feat(api): /workspace/search becomes wrapper around search_service (deprecated)"
```

---

## Phase 4 — CLI + Backfill

### Task 11: `mfb` CLI entry point

**Files:**
- Create: `src/mailfallback/cli/__init__.py`
- Modify: `pyproject.toml`
- Test: `tests/test_cli_index.py` (basic smoke)

- [ ] **Step 1: Add the entry point**

In `pyproject.toml`, find or add:

```toml
[project.scripts]
mfb = "mailfallback.cli:app"
```

- [ ] **Step 2: Create the CLI module**

Create `src/mailfallback/cli/__init__.py`:

```python
"""mfb CLI — admin operations.

Invoked via `docker compose exec mailfallback uv run mfb <subcommand>`.
Uses argparse (no new dependency).
"""

import argparse
import sys


def app() -> int:
    parser = argparse.ArgumentParser(prog="mfb")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_index = sub.add_parser("index", help="Mail index admin")
    index_sub = p_index.add_subparsers(dest="index_cmd", required=True)
    index_sub.add_parser("status", help="Show rebuild_status per account")
    p_rebuild = index_sub.add_parser("rebuild-account", help="Re-walk a live Maildir into the index")
    p_rebuild.add_argument("account_id")
    p_backfill = index_sub.add_parser("backfill-snapshots", help="Populate snapshot_messages for existing snapshots")
    p_backfill.add_argument("account_id")

    args = parser.parse_args()
    if args.cmd == "index":
        from mailfallback.cli.index import handle_index
        return handle_index(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(app())
```

- [ ] **Step 3: Create a placeholder index handler**

Create `src/mailfallback/cli/index.py`:

```python
"""`mfb index` subcommand handlers."""

from mailfallback.db import SessionLocal


def handle_index(args) -> int:
    if args.index_cmd == "status":
        return _status()
    if args.index_cmd == "rebuild-account":
        return _rebuild_account(args.account_id)
    if args.index_cmd == "backfill-snapshots":
        return _backfill_snapshots(args.account_id)
    return 1


def _status() -> int:
    from mailfallback.models import MailIndexRebuildStatus

    db = SessionLocal()
    try:
        rows = db.query(MailIndexRebuildStatus).all()
        if not rows:
            print("No rebuild_status rows yet.")
            return 0
        for r in rows:
            print(f"  account={r.account_id[:8]} state={r.state} "
                  f"last_indexed={r.last_indexed_at} "
                  f"backfill={r.backfill_progress}/{r.backfill_total}")
        return 0
    finally:
        db.close()


def _rebuild_account(account_id: str) -> int:
    from mailfallback.services import index_service

    db = SessionLocal()
    try:
        n = index_service.upsert_message_set(db, account_id)
        print(f"Rebuilt {n} rows for {account_id}.")
        return 0
    finally:
        db.close()


def _backfill_snapshots(account_id: str) -> int:
    # Implemented in Task 13.
    print("Not yet implemented — see Task 13.")
    return 1
```

- [ ] **Step 4: Smoke test**

```bash
cd /home/koma/src/mailfallback
uv pip install -e . > /dev/null 2>&1 || uv sync
uv run mfb index status
```

Expected: prints something (likely "No rebuild_status rows yet." on a fresh DB) without crashing.

If `uv run mfb` errors with "command not found", the entry point isn't registered. Verify `[project.scripts]` is present in pyproject.toml and re-run `uv sync`.

- [ ] **Step 5: Add minimal test**

Create `tests/test_cli_index.py`:

```python
"""Tests for `mfb index` CLI subcommands."""
from unittest.mock import patch


def test_cli_index_status_runs(capsys):
    """`mfb index status` doesn't crash on empty DB."""
    from mailfallback.cli import app
    import sys
    with patch.object(sys, "argv", ["mfb", "index", "status"]):
        rc = app()
    assert rc == 0
    captured = capsys.readouterr()
    assert "rebuild_status" in captured.out or "No rebuild_status" in captured.out
```

- [ ] **Step 6: Run test**

```bash
uv run pytest tests/test_cli_index.py -v
```

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/mailfallback/cli/__init__.py src/mailfallback/cli/index.py tests/test_cli_index.py
git commit -m "feat(cli): mfb CLI entry point with `mfb index status|rebuild-account` subcommands"
```

---

### Task 12: `restic_service.list_files` (snapshot file listing)

**Files:**
- Modify: `src/mailfallback/services/restic_service.py`
- Test: `tests/test_restic_service.py` (create if missing)

- [ ] **Step 1: Write the failing test**

Create or extend `tests/test_restic_service.py`:

```python
"""Tests for restic_service helpers."""
import json
from unittest.mock import patch, MagicMock


@patch("mailfallback.services.restic_service._run_restic")
def test_list_files_parses_restic_ls_json(mock_run, db_session, default_store):
    from mailfallback.models import Repository
    from mailfallback.services import restic_service

    repo = Repository(name="r", backend_type="local", local_path="/tmp/r", restic_password="x")
    db_session.add(repo)
    db_session.commit()

    # restic ls --json emits one JSON object per line, type="node" for files
    fake_stdout = "\n".join([
        json.dumps({"struct_type": "snapshot", "id": "abc"}),
        json.dumps({"type": "node", "name": "INBOX", "path": "/INBOX", "struct_type": "node", "node_type": "dir"}),
        json.dumps({"type": "node", "name": "1234.host:2,S", "path": "/INBOX/cur/1234.host:2,S", "struct_type": "node", "node_type": "file"}),
        json.dumps({"type": "node", "name": "1235.host:2,", "path": "/INBOX/cur/1235.host:2,", "struct_type": "node", "node_type": "file"}),
    ])
    mock_run.return_value = MagicMock(returncode=0, stdout=fake_stdout, stderr="")

    files = list(restic_service.list_files(repo, "abc12345", "abc"))
    paths = [f for f in files]
    assert "/INBOX/cur/1234.host:2,S" in paths
    assert "/INBOX/cur/1235.host:2," in paths
    # Directory entries excluded
    assert "/INBOX" not in paths
```

- [ ] **Step 2: Run test — fails**

```bash
uv run pytest tests/test_restic_service.py::test_list_files_parses_restic_ls_json -v
```

Expected: `AttributeError: ... 'list_files'`.

- [ ] **Step 3: Add the helper**

In `src/mailfallback/services/restic_service.py`, append:

```python
def list_files(destination, account_id: str, snapshot_id: str):
    """Yield file paths inside a snapshot. Uses restic ls --json --recursive.

    Returns a generator of strings (paths inside the snapshot). Directory
    entries are filtered out — only file paths are yielded.
    """
    import json as _json

    env = build_env(destination, account_id)
    result = _run_restic(
        ["ls", "--json", "--recursive", snapshot_id],
        env, _is_insecure(destination),
    )
    if result.returncode != 0:
        return
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            entry = _json.loads(line)
        except _json.JSONDecodeError:
            continue
        if entry.get("type") != "node":
            continue
        if entry.get("node_type") != "file":
            continue
        path = entry.get("path")
        if path:
            yield path
```

- [ ] **Step 4: Run test — passes**

```bash
uv run pytest tests/test_restic_service.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/mailfallback/services/restic_service.py tests/test_restic_service.py
git commit -m "feat(restic): list_files helper — restic ls --json --recursive parser"
```

---

### Task 13: `mfb index backfill-snapshots`

**Files:**
- Modify: `src/mailfallback/services/index_service.py`
- Modify: `src/mailfallback/cli/index.py`
- Modify: `tests/test_index_service.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_index_service.py`:

```python
@patch("mailfallback.services.index_service.restic_service")
def test_backfill_snapshots_sets_bits_for_matched_filenames(
    mock_restic, db_session, maildir_account
):
    """For each existing restic snapshot, list files, match Maildir filenames
    against alive messages, bulk INSERT snapshot_messages."""
    from mailfallback.models import BackupPolicy, Repository, SnapshotMessage

    # Create a backup policy so the service can find the destination
    repo = Repository(name="r", backend_type="local", local_path="/tmp/r", restic_password="x")
    db_session.add(repo)
    db_session.flush()
    db_session.add(BackupPolicy(account_id=maildir_account.id, destination_id=repo.id))
    db_session.commit()

    # Walk live first so we have alive messages
    index_service.upsert_message_set(db_session, maildir_account.id)

    mock_restic.list_snapshots.return_value = [
        {"short_id": "snapXXXX", "time": "2026-05-01T10:00:00Z"},
    ]
    # Snapshot lists files matching one of the live filenames
    mock_restic.list_files.return_value = iter([
        "/data/mailboxes/abc/INBOX/cur/1234567890.M1.host:2,S",  # matches first mail
    ])

    list(index_service.backfill_snapshots(db_session, maildir_account.id))

    bits = db_session.query(SnapshotMessage).filter(
        SnapshotMessage.snapshot_id == "snapXXXX"
    ).all()
    assert len(bits) == 1
```

- [ ] **Step 2: Run test — fails**

```bash
uv run pytest tests/test_index_service.py::test_backfill_snapshots_sets_bits_for_matched_filenames -v
```

Expected: `AttributeError: ... 'backfill_snapshots'`.

- [ ] **Step 3: Implement backfill**

In `src/mailfallback/services/index_service.py`, add at the top of the file:

```python
from mailfallback.services import restic_service
```

Then append:

```python
def _filename_prefix(filename: str) -> str:
    """Return the stable prefix of a Maildir filename (everything before the
    flag suffix). E.g. '1234.M5.host:2,RS' -> '1234.M5.host:2,'.
    """
    if ":2," in filename:
        return filename.split(":2,")[0] + ":2,"
    return filename


def backfill_snapshots(db: Session, account_id: str):
    """For each restic snapshot, set snapshot_messages bits for messages
    whose Maildir filename appears in the snapshot file list.

    Yields progress dicts: {snapshot_id, total, processed, bits_inserted}.
    """
    from mailfallback.models import BackupPolicy, MailIndexMessage, MailIndexRebuildStatus, SnapshotMessage

    backup = db.query(BackupPolicy).filter(BackupPolicy.account_id == account_id).first()
    if not backup:
        raise ValueError(f"Account {account_id} has no backup policy")

    # Build a lookup: filename_prefix -> message_id_hash for all alive messages
    alive = db.query(
        MailIndexMessage.message_id_hash, MailIndexMessage.maildir_filename
    ).filter(
        MailIndexMessage.account_id == account_id,
        MailIndexMessage.deleted_at.is_(None),
    ).all()
    prefix_to_hash = {_filename_prefix(fn): h for h, fn in alive}

    snaps = restic_service.list_snapshots(backup.destination, account_id)

    rs = db.query(MailIndexRebuildStatus).filter(
        MailIndexRebuildStatus.account_id == account_id
    ).first()
    if rs:
        rs.state = "snap_backfilling"
        rs.backfill_progress = 0
        rs.backfill_total = len(snaps)
        db.commit()

    try:
        for i, s in enumerate(snaps):
            sid = s.get("short_id") or s.get("id", "")[:8]
            if not sid:
                continue
            seen_hashes: set[bytes] = set()
            for path in restic_service.list_files(backup.destination, account_id, sid):
                fn = path.rsplit("/", 1)[-1]
                if "/cur/" not in path and "/new/" not in path:
                    continue
                h = prefix_to_hash.get(_filename_prefix(fn))
                if h:
                    seen_hashes.add(h)
            inserted = 0
            if seen_hashes:
                from sqlalchemy.dialects.postgresql import insert as pg_insert
                rows = [
                    {"snapshot_id": sid, "account_id": account_id, "message_id_hash": h}
                    for h in seen_hashes
                ]
                if db.bind.dialect.name == "postgresql":
                    stmt = pg_insert(SnapshotMessage).values(rows).on_conflict_do_nothing()
                else:
                    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
                    stmt = sqlite_insert(SnapshotMessage).values(rows).on_conflict_do_nothing()
                result = db.execute(stmt)
                inserted = result.rowcount or 0
                db.commit()
            if rs:
                rs.backfill_progress = i + 1
                db.commit()
            yield {"snapshot_id": sid, "total": len(snaps), "processed": i + 1, "bits_inserted": inserted}
        if rs:
            rs.state = "idle"
            rs.last_error = None
            db.commit()
    except Exception as e:
        if rs:
            rs.state = "failed"
            rs.last_error = str(e)
            db.commit()
        raise
```

- [ ] **Step 4: Wire the CLI**

In `src/mailfallback/cli/index.py`, replace the `_backfill_snapshots` placeholder:

```python
def _backfill_snapshots(account_id: str) -> int:
    from mailfallback.services import index_service

    db = SessionLocal()
    try:
        for progress in index_service.backfill_snapshots(db, account_id):
            print(f"  snap {progress['snapshot_id']}: "
                  f"{progress['processed']}/{progress['total']}, "
                  f"+{progress['bits_inserted']} bits")
        print("Done.")
        return 0
    finally:
        db.close()
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/test_index_service.py tests/test_cli_index.py -v
```

All must pass.

- [ ] **Step 6: Commit**

```bash
git add src/mailfallback/services/index_service.py \
        src/mailfallback/cli/index.py \
        tests/test_index_service.py
git commit -m "feat(cli): mfb index backfill-snapshots — populate bits via restic ls"
```

---

## Phase 5 — Hardening

### Task 14: Lint, format, full smoke

**Files:**
- (none — verification only)

- [ ] **Step 1: Lint + format**

```bash
cd /home/koma/src/mailfallback
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```

Fix any errors reported.

- [ ] **Step 2: Full test suite**

```bash
uv run pytest tests/ -n auto -v 2>&1 | tail -20
```

Expected: all tests pass. Baseline before this plan was ~470 passing; after this plan expect ~485+ (10–15 new tests across 5 phases).

- [ ] **Step 3: Migration smoke against real Postgres**

```bash
docker compose up -d db
uv run alembic upgrade head
docker compose exec db psql -U mailfallback -d mailfallback -c "\dn mail_index"
docker compose exec db psql -U mailfallback -d mailfallback -c "\dt mail_index.*"
```

Expected: schema `mail_index` exists with three tables.

- [ ] **Step 4: Build container + boot smoke**

```bash
docker compose up -d --build mailfallback
sleep 5
curl -s http://localhost:8000/healthz
docker compose exec mailfallback uv run mfb index status
```

Expected: healthz returns `{"status":"ok"}`. CLI runs without traceback.

- [ ] **Step 5: Commit any cleanup**

```bash
git status --short
git add -A
git diff --cached --quiet || git commit -m "chore: format + lint pass after mail_index plan"
```

- [ ] **Step 6: Verify branch state**

```bash
git log --oneline | head -20
git status -sb
```

The branch should be clean, with all 14 task commits visible.

---

## Notes for the executor

- The spec is `docs/superpowers/specs/2026-05-11-mail-index-search-api-design.md`. Re-read sections you implement.
- `index_service` MUST never let exceptions propagate to break the calling sync/backup flow. The pattern is `try / except Exception: logger.warning(...)` at every call site.
- `search_service` Phase 2 talks IMAP. It MUST gracefully degrade — Phase 1 results ship even when Dovecot is unavailable.
- The deprecated `/api/restore/workspace/search` wrapper preserves response shape for the CURRENT UI. Cycle 2 (separate brainstorm) replaces both endpoint and UI.
- The feature flag `MAILFALLBACK_USE_INDEX_SEARCH=false` switches the wrapper back to the legacy mount-and-search path verbatim. Use during rollout if needed; remove after the new path is verified in production.
- Forward-only for snapshots: `record_snapshot` only fires from now forward. Older snapshots get bits via `mfb index backfill-snapshots <id>` invoked manually by the operator.
- The Maildir filename matching in `backfill_snapshots` uses the `:2,` prefix — Maildir convention. If your snapshots contain files without this convention (some MTAs), the matching will silently miss those. Documented v1 limitation; revisit if encountered.
